# `telecom-common`

Bibliothèque partagée de la plateforme TelecomX. **Source unique de vérité** des types métier,
de la tokenisation PII et de l'instrumentation.

## Pourquoi cette bibliothèque existe

Elle porte les artefacts dont une divergence provoquerait les bugs les plus coûteux de la
plateforme ([ADR-001](../../docs/adr/ADR-001-monorepo.md)) :

| Module | Rôle | Risque qu'il élimine |
|---|---|---|
| `domain/` | Identifiants et entités validés | Un `cell_id` mal formé qui se propage jusqu'en gold |
| `security/` | Tokenisation HMAC des PII | Un MSISDN en clair au-delà de l'ingestion |
| `observability/` | Métriques, logs, traces | Un composant non instrumenté, invisible en production |
| `timeutils` | Horodatages UTC | Un `datetime` naïf qui fausse un partitionnement |
| `config` | Configuration typée | Une variable d'environnement absente découverte en production |

## Règle d'import

```
apps/ · agents/ · ml-platform/ · data-platform/   ──importent──►   libs/telecom-common
```

**Jamais l'inverse, jamais entre eux.** Contrainte vérifiée en CI par `import-linter`
(`.importlinter`, contrat `layers`).

## Exigences de qualité

Cette bibliothèque est soumise au régime le plus strict du dépôt
([ADR-014](../../docs/adr/ADR-014-python-tooling.md)) :

- `mypy --strict` — zéro erreur
- couverture de tests ≥ 90 %
- aucune dépendance ajoutée sans justification (elle est imposée à tous les consommateurs)

## Structure

```
src/telecom_common/
├── domain/          identifiers · enums · entités réseau et client
├── security/        tokenisation HMAC · détection PII
├── observability/   logging structuré · métriques · tracing
├── timeutils.py     helpers UTC (la règle ruff DTZ interdit les datetime naïfs)
└── config.py        configuration typée depuis l'environnement
```
