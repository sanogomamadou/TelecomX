# ADR-004 — MinIO comme stockage objet compatible S3

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 |

## Contexte

Le Lakehouse Iceberg ([ADR-003](ADR-003-table-format.md)) exige un stockage objet. Le projet doit
fonctionner **entièrement en local, sans budget cloud** (PRD C2), tout en démontrant une
architecture réellement transposable vers AWS S3, GCS ou Azure Blob.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Compatibilité API S3 (portabilité du code vers le cloud) | 🔴 Élevé |
| C2 | Coût nul et exécution locale | 🔴 Élevé |
| C3 | Empreinte ressources | 🟠 Moyen |
| C4 | Fonctionnalités attendues d'un lac (versioning, politiques, chiffrement) | 🟠 Moyen |

## Options envisagées

| Option | Verdict |
|---|---|
| **A — MinIO** | ✅ API S3 native, léger (~200 Mo), console web, versioning, politiques IAM, chiffrement SSE, notifications d'événements. ❌ Fonctionnalités avancées progressivement réservées à l'édition commerciale ; mono-nœud en dev = pas de tolérance de panne. |
| **B — AWS S3 réel** | ✅ Le vrai service, zéro exploitation. ❌ Coût et compte cloud requis → viole C2. |
| **C — LocalStack** | ✅ Émule S3 et bien d'autres services AWS. ❌ Émulateur destiné aux tests, non à un usage soutenu ; performances et persistance inadaptées à un lac de données. |
| **D — Système de fichiers local (`file://`)** | ✅ Trivial. ❌ Ne démontre aucune architecture de stockage objet ; comportement différent (pas de cohérence éventuelle, pas de préfixes, pas d'IAM) → l'architecture ne serait pas transposable. |

## Décision

**MinIO (Option A)**, déployé en mono-nœud pour le développement, avec un accès effectué
**exclusivement via l'API S3** (`s3a://` côté Spark, `s3://` côté Trino/Iceberg).

Le point essentiel n'est pas MinIO en soi, mais la **discipline** qui l'accompagne : aucun code
n'écrit sur le système de fichiers local. La bascule vers AWS S3 se limite alors à un changement
d'endpoint et de fournisseur d'identifiants.

## Conséquences

**Positives**
- Portabilité cloud réelle : mêmes chemins, mêmes clients, mêmes politiques.
- Console web utile en démonstration (voir apparaître les fichiers Parquet et les métadonnées Iceberg).
- Versioning de bucket activable pour la stratégie de sauvegarde (Phase 8).

**Négatives**
- Mono-nœud ⇒ **aucune redondance** : la perte du volume entraîne la perte du lac. → Sauvegarde via `mc mirror` intégrée au plan DR ; la production utiliserait un déploiement distribué (erasure coding).
- Consommation disque non négligeable sur 54 Go disponibles → rétention bronze limitée à 7 jours en dev.
- Les performances locales (un seul disque) ne sont pas représentatives du cloud → les tests de charge (Phase 8) le mentionneront explicitement.

## Notes d'implémentation

**Organisation des buckets**

```
telecom-lake/                 # données du Lakehouse
├── bronze/<source>/...
├── silver/<domain>/...
└── gold/<mart>/...
telecom-warehouse/            # emplacement des tables Iceberg (métadonnées + données)
telecom-mlflow/               # artefacts MLflow
telecom-checkpoints/          # checkpoints Spark Structured Streaming
telecom-rag/                  # documents sources du corpus RAG
telecom-backup/               # sauvegardes (Phase 8)
```

**Politique de cycle de vie**

| Zone | Rétention dev | Rétention production cible |
|---|---|---|
| bronze | 7 j | 90 j |
| silver | 30 j | 400 j |
| gold | illimitée | 5 ans |
| checkpoints | 3 j | 7 j |

**Sécurité** : utilisateurs de service distincts par composant (`spark`, `trino`, `mlflow`, `api`),
avec des politiques limitées aux préfixes nécessaires — jamais les identifiants racine.

## Références
- [ADR-003](ADR-003-table-format.md) · [ADR-011](ADR-011-local-dev-strategy.md) · [PRD §9.1 C2](../product/PRD.md)
