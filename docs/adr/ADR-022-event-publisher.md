# ADR-022 — Publieur d'événements : valider avant d'envoyer, rejeter vers une file de rebut sous contrat

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-10-30 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 (lot 1.6) |

## Contexte

Les générateurs (lot 1.4) produisent des événements valides en mémoire ; Kafka et le Schema Registry
tournent (lot 1.5). Il manque le maillon qui les relie, et deux exigences du SRS lui reviennent :

| Exigence | Énoncé |
|---|---|
| FR-M1-007 | Sémantique **au-moins-une-fois**, clé de partition déterministe |
| FR-M1-008 | Un message non conforme est routé vers `<topic>.dlq` **avec son motif**, sans bloquer le flux |

Trois risques à neutraliser :

| Risque | Conséquence |
|---|---|
| Un événement invalide entre dans le topic | Il contamine bronze ; la correction coûte dix fois plus en aval |
| Un rejet est silencieux (message perdu ou seulement journalisé) | Une perte de données que personne ne voit : « aucune donnée n'est jamais perdue silencieusement » |
| Le motif de rejet contient la valeur refusée | Un MSISDN ou un IMSI se retrouve dans les journaux et les alertes |

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Aucun message invalide dans un topic principal | 🔴 Élevé |
| C2 | Aucun rejet silencieux, aucune livraison perdue sans erreur | 🔴 **Bloquant** |
| C3 | Le contrat reste la source unique des règles (ADR-015) | 🔴 Élevé |
| C4 | Testable sans Docker, et vérifié aussi contre la vraie pile | 🟠 Moyen |
| C5 | Peu de dépendances ; `telecom-common` reste léger (ADR-001) | 🟠 Moyen |

## Décision

1. **Le publieur vit dans `data-platform/generators`**, pas dans `libs/`. Le client Kafka est une
   dépendance lourde que `telecom-common` n'a pas le droit de porter (contrat `.importlinter`,
   vérifié en CI).
2. **On valide avant d'envoyer.** Chaque événement passe par le modèle Pydantic de son contrat
   (le contrat exécutable, ADR-015) puis par l'encodeur Avro. Un échec à l'une des deux étapes
   envoie un message de rebut au lieu de l'événement. Rien d'invalide n'atteint un topic principal.
3. **La file de rebut est elle-même sous contrat** : `data-platform/schemas/avro/dlq/dead-letter-v1.avsc`
   (règle « tout flux a un contrat »). Un seul contrat sert les onze `<topic>.dlq`. Il n'est pas
   dérivé en modèle Pydantic (seul le publieur l'écrit) et le validateur de contrats lui applique
   des règles propres (sa liste de champs au lieu de l'enveloppe d'événement).
4. **Le message refusé est conservé en JSON**, pas en Avro : un message qui a échoué à la
   validation ne peut pas être supposé encodable dans son propre schéma.
5. **Le motif ne contient jamais de valeur** : uniquement `champ : type d'erreur`. Les valeurs
   refusées restent dans la charge utile du rebut, sous le régime d'accès des topics (risque R8).
6. **Quatre codes de rejet** : `MISSING_TENANT` (ADR-016), `SCHEMA_VIOLATION` (forme),
   `CONSTRAINT_VIOLATION` (borne, format, date future), `SERIALIZATION_ERROR` (anomalie du publieur,
   à alerter). La distinction pilote la lecture d'une alerte : un taux de contraintes qui monte est un
   problème de qualité de données, un taux de schéma qui monte est un producteur mal déployé.
7. **Au-moins-une-fois** : producteur idempotent, `acks=all`, `max.in.flight=5`, `retries` maximal
   (ADR-002). `flush()` **lève une erreur** si un message n'est pas acquitté : un échec ne passe pas
   inaperçu. Un événement n'est compté « publié » qu'à son **acquittement**, jamais à la mise en file.
8. **Compatibilité imposée par le registre** : le niveau déclaré dans le contrat est posé sur le sujet
   *avant* l'enregistrement. Une évolution incompatible est alors refusée par le registre lui-même
   (HTTP 409), pas par convention.
9. **Client du registre écrit sur la bibliothèque standard** (une centaine de lignes) plutôt que le
   client officiel, qui apporte une pile HTTP, une bibliothèque OAuth et un cache dont ce besoin ne se
   sert pas. Le codage Avro utilise `fastavro` ; le client Kafka est `confluent-kafka`.
10. **Horodatage Kafka = instant de publication**, pas instant de l'événement. La rétention du
    broker se calcule sur cet horodatage : avec l'instant de l'événement, le rejeu d'un mois passé
    serait supprimé au moment où il est écrit.
11. **Injection de messages corrompus** (`--poison-fraction`) pour exercer le chemin de rebut : sans
    elle les générateurs, qui produisent des données valides, ne l'alimenteraient jamais.

## Conséquences

| ✅ Positives | ⚠️ Négatives / limites assumées |
|---|---|
| Aucun message invalide dans un topic principal (testé sur les 11 contrats × chaque corruption applicable) | **Au-moins-une-fois ⇒ doublons possibles** : la déduplication par `event_id` est à faire en silver (FR-M2-003) |
| Un rejet est une donnée : compté, classé, rejouable | **L'outil de rejeu n'existe pas encore** : le contrat et le format sont prêts, pas l'outil |
| Le taux de rebut se mesure contre le SLO (< 0,1 %) à chaque exécution | Les contraintes qui exigent un état extérieur (`unique`, `in_ref`) ne sont **pas** vérifiables à la production : elles sont traitées en silver (gravité « majeure », mise en quarantaine) |
| Le publieur se teste sans Docker (faux producteur, faux registre HTTP) et contre la vraie pile | Un client de registre maison est un code de plus à maintenir |
| Le registre refuse lui-même une évolution incompatible | Les événements du scénario sont horodatés dans le passé : les consommateurs doivent raisonner en temps d'événement, jamais en temps Kafka |

## Notes d'implémentation

- **Défauts trouvés par l'exécution réelle**, absents des tests simulés :
  - sous Windows, `localhost` se résout d'abord en IPv6 (`::1`), où Docker n'écoute pas : chaque
    requête au registre attendait ≈ 2,07 s avant de retomber sur IPv4, contre 0,02 s en
    `127.0.0.1` (mesuré). Les valeurs par défaut utilisent l'adresse numérique, et le client Kafka
    fixe `broker.address.family=v4` ;
  - `make test` masquait ses échecs (`|| echo`), comme deux contrôles de sécurité : corrigés.
- Les erreurs Pydantic sont rangées par type (`value_error`, `greater_than_equal`... pour les
  contraintes ; le reste pour la forme). Un type d'erreur inconnu est classé **schéma**, le choix le
  plus prudent.
- `pytest` nu n'exécute plus les tests d'intégration (ils exigent Docker) : `-m integration`.

## Références

- [ADR-002](ADR-002-event-backbone.md) · [ADR-015](ADR-015-data-contracts.md) · [ADR-016](ADR-016-multi-tenancy.md) · [ADR-020](ADR-020-core-runtime.md)
- [Contrats de données §5](../data/contracts/README.md) · FR-M1-006, FR-M1-007, FR-M1-008
