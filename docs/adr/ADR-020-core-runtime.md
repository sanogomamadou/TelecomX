# ADR-020 — Socle d'exécution local : le profil `core`, ses garde-fous vérifiés sans Docker

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-10-27 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 (lot 1.5) |

## Contexte

[ADR-011](ADR-011-local-dev-strategy.md) a décidé *quoi* démarrer (des profils plutôt que toute la
pile). Ce lot décide *comment* le premier profil, `core` (Kafka, Schema Registry, MinIO,
PostgreSQL, Redis), est décrit, configuré et contrôlé.

Trois risques concrets, tous **silencieux** quand ils se produisent :

| Risque | Ce qui se passe |
|---|---|
| Limite mémoire de conteneur sans `-Xmx` correspondant | Le processus Java dépasse la limite, le noyau le tue (`OOMKilled`), sans message utile |
| Port publié sur toutes les interfaces | Kafka, MinIO ou PostgreSQL deviennent joignables depuis le réseau local |
| Mot de passe avec valeur par défaut dans le fichier | Un identifiant faible se retrouve dans le dépôt, puis en production par copie |

À cela s'ajoute une contrainte de méthode : **le moteur Docker de la machine de développement est
fragile** (cf. ADR-011, erreur 1). Les règles ci-dessus ne peuvent donc pas dépendre d'un démarrage
réel pour être vérifiées.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Les règles de mémoire et de sécurité sont **vérifiées automatiquement** | 🔴 Élevé |
| C2 | Démarrage reproductible : topics et buckets créés sans geste manuel | 🔴 Élevé |
| C3 | Aucun secret dans le dépôt, démarrage refusé s'il manque | 🔴 **Bloquant** |
| C4 | Tient dans le budget de 2,1 GB (ADR-011) | 🔴 Élevé |
| C5 | Fidélité à l'architecture : vrais Kafka et Schema Registry, pas des substituts | 🔴 Élevé |

## Décision

1. **Un seul `docker-compose.yml`**, avec des *profils*. `docker compose up` sans profil ne démarre
   **rien** (testé) : démarrer « tout » est précisément ce que la machine ne supporte pas.
2. **Images officielles épinglées** : `apache/kafka` (KRaft natif, sans ZooKeeper), `confluentinc/cp-schema-registry`,
   `minio/minio` et `minio/mc`, `postgres:*-alpine`, `redis:*-alpine`. Aucun `latest` (testé).
   Choisir `apache/kafka` plutôt que l'image Confluent évite un second éditeur pour un composant
   que le projet ne modifie pas ; le Schema Registry, lui, n'existe en pratique que chez Confluent.
3. **Deux portes d'entrée Kafka.** Les conteneurs parlent à `kafka:19092`, la machine hôte à
   `localhost:9092`. Un seul écouteur publicitaire ferait échouer l'un des deux : le client reçoit
   l'adresse annoncée, qui doit être joignable *de son côté*.
4. **Les topics ne se créent jamais seuls** (`auto.create.topics.enable=false`). Ils sont déclarés
   dans `data-platform/kafka/topics.conf`, **source unique** lue par le script d'initialisation et
   comparée par les tests aux contrats Avro et à l'ADR-002. Une faute de frappe dans le nom d'un
   topic par un producteur devient une erreur, pas un nouveau topic fantôme.
5. **Rétention locale courte (24 h), DLQ 72 h.** Le disque est compté ; une DLQ doit survivre à une
   nuit pour être examinée. La rétention de production reste documentée dans la même table.
6. **Secrets : `${VAR:?message}`.** Compose refuse de démarrer si la variable manque. `make env`
   génère localement les secrets vides (aléatoires, jamais affichés, jamais écrasés).
7. **Ports publiés sur `127.0.0.1` uniquement.**
8. **Contrôles sans Docker** (`tests/unit/infra/`, 54 tests) : mémoire, heap JVM ≤ 70 % de la limite,
   ports, secrets, images, healthchecks, ordre de démarrage, cohérence topics ↔ contrats ↔ ADR,
   script d'initialisation exécuté contre de faux outils Kafka, fins de ligne. La CI y ajoute le
   rendu réel du fichier par `docker compose config`.

## Conséquences

| ✅ Positives | ⚠️ Négatives / limites assumées |
|---|---|
| Les trois risques silencieux sont des tests qui échouent | **Les tests prouvent que le fichier respecte les règles, pas que les conteneurs démarrent.** Le démarrage réel reste à constater sur une machine dont le moteur Docker répond |
| Une seule définition des topics | Un broker unique : réplication 1, aucune tolérance de panne (déjà acceptée, ADR-002) |
| Démarrage refusé proprement sans secrets | Redis sans mot de passe en local (port limité à `127.0.0.1`, cache sans donnée sensible) |
| Fins de ligne imposées à LF | Le Schema Registry n'a que la compatibilité `BACKWARD` par défaut ; `FULL` pour les sorties se règle par sujet à l'enregistrement (lot 1.6) |

## Notes d'implémentation

- **Défaut trouvé par les tests** : lu sur un poste Windows en fins de ligne CRLF, `topics.conf`
  faisait créer un topic nommé « retour chariot ». Le script supprime désormais les `\r` et
  `.gitattributes` impose LF aux fichiers lus par les conteneurs.
- Le premier test de variables requises avait été trompé par un commentaire du fichier contenant
  l'exemple `${VAR:?}` : le test ignore maintenant les lignes de commentaire.
- `mc ilm rule add` n'est pas idempotent (chaque exécution empile une règle) : le script vérifie
  d'abord.
- Budget mesuré par test : Kafka 1 GB, Schema Registry 400 MB, MinIO 300 MB, PostgreSQL 300 MB,
  Redis 100 MB, soit ≈ 2,1 GB. Les deux conteneurs d'initialisation sont éphémères et exclus.

## Références

- [ADR-002](ADR-002-event-backbone.md) topics · [ADR-004](ADR-004-object-storage.md) buckets · [ADR-011](ADR-011-local-dev-strategy.md) profils
- [ADR-015](ADR-015-data-contracts.md) compatibilité · [Guide de démarrage du socle](../runbooks/core-stack.md)
