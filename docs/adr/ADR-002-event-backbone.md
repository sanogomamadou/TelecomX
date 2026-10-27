# ADR-002 — Apache Kafka (mode KRaft) comme bus d'événements

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 |

## Contexte

La plateforme doit absorber **~2 475 msg/s en moyenne et ~17 800 msg/s en pic** répartis sur
9 flux hétérogènes (PRD §2.2), avec des exigences fortes :

- **Aucune perte de données** (NFR-AVAIL-003) — les CDR ont une valeur métier et une portée réglementaire.
- **Rejouabilité** : un modèle ML réentraîné ou un bug de transformation exige de rejouer l'historique.
- **Multi-consommateurs indépendants** : le même flux `network.metrics` alimente simultanément
  l'ingestion bronze, la détection d'anomalie temps réel et le monitoring.
- **Découplage producteur/consommateur** : une panne du Lakehouse ne doit pas faire perdre d'événements.

Contrainte transverse : l'environnement de développement dispose de 16 GB de RAM ([ADR-011](ADR-011-local-dev-strategy.md)).

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Durabilité et rejouabilité (rétention longue, offsets) | 🔴 Élevé |
| C2 | Débit soutenu et scalabilité horizontale | 🔴 Élevé |
| C3 | Écosystème d'intégration (Spark, Schema Registry, Connect) | 🔴 Élevé |
| C4 | Empreinte mémoire en développement local | 🟠 Moyen |
| C5 | Valeur sur le marché de l'emploi télécom | 🟠 Moyen |

## Options envisagées

### Option A — Apache Kafka (mode KRaft, sans ZooKeeper)
✅ Standard de fait ; **omniprésent chez les opérateurs télécom**
✅ Rétention configurable, log compaction, rejeu par offset
✅ Intégration native Spark Structured Streaming, Schema Registry, Kafka Connect
✅ KRaft supprime ZooKeeper : **un processus JVM au lieu de deux** (~700 Mo au lieu de ~1,2 Go)
❌ JVM gourmande comparée aux alternatives natives
❌ Configuration riche donc exigeante (ISR, acks, rétention, compaction)

### Option B — Redpanda
✅ Compatible API Kafka, écrit en C++ : **empreinte mémoire ~3× inférieure**, pas de JVM
✅ Un seul binaire, démarrage quasi instantané — objectivement supérieur pour C4
❌ Écosystème périphérique moins mature (Connect, outillage tiers)
❌ **Beaucoup moins présent dans les offres d'emploi télécom** — pénalise C5
❌ Certaines subtilités de comportement diffèrent de Kafka (une démonstration de maîtrise de Kafka a plus de valeur)

### Option C — Apache Pulsar
✅ Séparation calcul/stockage, multi-tenancy native, géo-réplication intégrée
❌ Architecture à trois composants (Broker + BookKeeper + ZooKeeper) : **rédhibitoire en 16 GB**
❌ Adoption plus faible, écosystème Spark moins mature

### Option D — RabbitMQ / NATS
❌ Sémantique de file de messages, pas de log persistant rejouable → **élimine C1**, qui est bloquant

## Comparaison

| Critère | A. Kafka KRaft | B. Redpanda | C. Pulsar | D. RabbitMQ |
|---|---|---|---|---|
| C1 Durabilité/rejeu | ✅✅ | ✅✅ | ✅✅ | ❌ |
| C2 Débit/scalabilité | ✅✅ | ✅✅ | ✅✅ | 🔸 |
| C3 Écosystème | ✅✅ | ✅ | 🔸 | 🔸 |
| C4 Empreinte locale | 🔸 | ✅✅ | ❌ | ✅ |
| C5 Valeur métier télécom | ✅✅ | 🔸 | 🔸 | 🔸 |

## Décision

**Apache Kafka en mode KRaft (Option A)**, broker unique en développement, configuration
multi-broker documentée et paramétrée pour la production.

Redpanda gagne strictement sur C4, mais C3 et C5 sont déterminants pour ce projet : Kafka est
**le** backbone événementiel du secteur télécom, et l'écosystème Schema Registry + Spark +
Connect est un pilier de l'architecture. KRaft réduit suffisamment l'écart sur C4 pour que le
compromis soit acceptable.

## Conséquences

**Positives**
- Rejeu complet possible : réentraîner un modèle sur 30 jours d'historique ne demande qu'un reset d'offset.
- Découplage réel : arrêter Spark n'entraîne aucune perte, seulement du lag.
- Compatible avec la stratégie de contrats de données ([ADR-015](ADR-015-data-contracts.md)).

**Négatives**
- **Broker unique en dev ⇒ `replication.factor = 1` ⇒ aucune tolérance de panne locale.** C'est assumé et documenté : la configuration de production utilise RF=3 et `min.insync.replicas=2`. Les expériences de chaos sur la perte de broker se feront sur `kind` (Phase 8).
- Consommation mémoire à surveiller : heap plafonné à 512 Mo en développement.
- La rétention longue consomme du disque → rétention dev réduite à 24 h, production à 7 j.

**À surveiller**
- Le **consumer lag** est l'indicateur de santé n°1 de la plateforme (NFR-OBS, alerting Phase 7).

## Notes d'implémentation

**Dimensionnement des topics** (partitions calculées pour le débit de *production*, cf. PRD §2.2) :

| Topic | Partitions | Rétention | Clé de partition | Justification |
|---|---|---|---|---|
| `network.metrics` | 12 | 7 j | `cell_id` | Ordre garanti par cellule (indispensable à la détection d'anomalie) |
| `network.alarms` | 6 | 30 j | `site_id` | Faible débit, forte valeur, rétention longue |
| `cdr.events` | 24 | 3 j | `caller_id` ⚠️ | Flux le plus volumineux (99 M/j) |
| `data.sessions` | 18 | 3 j | `subscriber_id` ⚠️ | |
| `sms.events` | 12 | 3 j | `sender_id` ⚠️ | Pics extrêmes (Nouvel An ×28) |
| `ussd.events` | 6 | 3 j | `subscriber_id` ⚠️ | |
| `recharge.events` | 6 | 30 j | `subscriber_id` ⚠️ | Donnée financière |
| `bundle.events` | 6 | 30 j | `subscriber_id` ⚠️ | |
| `mobilemoney.transactions` | 12 | 90 j | `sender_account` ⚠️ | Donnée financière, exigence d'audit |
| `customer.events` | 6 | 90 j | `subscriber_id` ⚠️ | |
| `<topic>.dlq` | 3 | 30 j | — | Une DLQ par topic (FR-M1-008) |

> ⚙️ En développement, `SPEED_FACTOR` réduit le débit ; le nombre de partitions reste identique
> afin de valider le comportement de parallélisme réel.

**Garanties producteur** : `acks=all`, `enable.idempotence=true`, `max.in.flight=5`, `retries=MAX`.
**Consommateurs** : `isolation.level=read_committed`, `auto.offset.reset=earliest`, offsets gérés par les checkpoints Spark.

---

## Correction — 2026-10-27

**⚠️ = identifiant brut.** La version initiale de la table ci-dessus indiquait des clés de la
forme `subscriber_token` et `account_token`. C'était **impossible** : un jeton est produit par la
tokenisation, qui a lieu dans le job Spark bronze → silver ([ADR-013](ADR-013-pii-tokenization.md)),
donc *après* Kafka. Le producteur ne détient que l'identifiant brut. La vérité exécutable est le
contrat Avro (`telecomx.partition_key`), et elle partitionne sur l'identifiant brut.

L'erreur a été trouvée en écrivant le générateur de code du lot 1.2, qui lit ces contrats.

### Conséquence, plus large que la table

**Les topics Kafka portent des identifiants directs**, en clé de message **et** en charge utile,
avec les rétentions ci-dessus (jusqu'à 90 j pour `mobilemoney.transactions` et `customer.events`).
Le modèle de menaces ne traitait ce risque que pour la zone bronze.

Kafka est dans la **même zone de confiance que bronze** : même donnée brute, même rétention, même
raison d'être (rejeu). Il hérite donc du même régime, désormais écrit :

| Contrôle | Application aux topics |
|---|---|
| Accès | ACL par topic, réservées aux producteurs et au job d'ingestion ; aucun consommateur applicatif (API, agents, ML) |
| Authentification | SASL/mTLS en production |
| Rétention | Ne jamais dépasser celle de bronze (90 j en production) |
| Exposition | Aucun accès libre-service ; les consommateurs métier lisent silver, jamais Kafka |
| Chiffrement | TLS en transit, chiffrement du volume au repos |
| DLQ | Elles conservent la charge utile **rejetée** (`raw_payload`), donc les mêmes identifiants : même régime, rétention de 30 j |

> 💡 **Alternative écartée** : faire partitionner par un pseudonyme calculé côté producteur
> supposerait que celui-ci détienne la clé HMAC, ce qui contredit [ADR-013](ADR-013-pii-tokenization.md)
> (la clé n'est détenue que par le job d'ingestion). Le coût d'un identifiant brut en clé est donc
> accepté, et circonscrit par le régime ci-dessus.

Documents alignés : [threat model](../security/threat-model.md) (R8), [politique PII](../security/pii-policy.md) §6.

## Références
- [PRD §2.2](../product/PRD.md) · [FR-M1-007/008](../product/SRS.md) · [ADR-011](ADR-011-local-dev-strategy.md) · [ADR-015](ADR-015-data-contracts.md)
