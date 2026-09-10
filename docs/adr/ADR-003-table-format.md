# ADR-003 — Apache Iceberg comme format de table du Lakehouse

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 |

## Contexte

Les données doivent être stockées sur objet (S3/MinIO) tout en offrant des garanties de base de
données : transactions, évolution de schéma, lecture concurrente pendant l'écriture, et
interrogation par **plusieurs moteurs** (Spark pour l'écriture, Trino pour l'analytique, dbt pour
la transformation).

Contraintes issues du domaine télécom :
- **Évolution de schéma inévitable** — un équipementier ajoute un compteur, une version de firmware
  change un champ (SRS FR-M2-006, PRD §16 `network.metrics v1 → v2`).
- **Retraitement fréquent** — corriger une journée sans dupliquer (FR-M2-011).
- **Écriture continue + lecture analytique simultanée** sans lecture partielle.
- **Audit et reproductibilité** — pouvoir dire « voici l'état exact de la table quand le modèle a été entraîné ».

Un stockage Parquet nu sur MinIO ne répond à aucune de ces contraintes : pas d'atomicité,
pas de gestion de schéma, `LIST` coûteux, aucune isolation lecteur/écrivain.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Interopérabilité multi-moteurs (Spark **et** Trino, sans moteur propriétaire) | 🔴 Élevé |
| C2 | Évolution de schéma sûre (rename, réordonnancement, élargissement) | 🔴 Élevé |
| C3 | Transactions ACID et isolation snapshot | 🔴 Élevé |
| C4 | Évolution de partitionnement sans réécriture | 🟠 Moyen |
| C5 | Time travel / rollback | 🟠 Moyen |

## Options envisagées

### Option A — Apache Iceberg
✅ **Évolution de schéma par identifiant de colonne** (et non par position ou par nom) : renommer une colonne n'invalide aucune donnée existante — décisif pour C2
✅ **Hidden partitioning** : les requêtes n'ont pas besoin de connaître le schéma de partitionnement, et celui-ci peut évoluer sans réécrire l'historique (C4)
✅ Support de premier ordre dans Trino, Spark, Flink, dbt — spécification réellement ouverte
✅ Snapshots, time travel, rollback, `MERGE INTO` (C3, C5)
❌ Maintenance explicite requise (compaction, expiration de snapshots) — sinon prolifération de petits fichiers
❌ Nécessite un catalogue (REST, JDBC, Hive, Glue) — une brique de plus

### Option B — Delta Lake
✅ ACID, time travel, très mature, excellent avec Spark
✅ Écosystème Databricks riche
❌ Support Trino historiquement en retrait par rapport à Iceberg
❌ Évolution de schéma **par nom**, sans identifiants de colonne : le renommage est plus fragile
❌ L'évolution de partitionnement exige une réécriture
❌ Gouvernance de fait pilotée par un éditeur

### Option C — Apache Hudi
✅ Excellent sur les upserts incrémentaux et le CDC
❌ Modèle opérationnel plus complexe (COW/MOR, compaction, timeline)
❌ Support Trino/dbt moins fluide, courbe d'apprentissage plus raide

### Option D — Parquet nu + partitions Hive
❌ Aucune atomicité, aucune gestion de schéma, `LIST` S3 coûteux → élimine C2 et C3 (bloquants)

## Comparaison

| Critère | A. Iceberg | B. Delta | C. Hudi | D. Parquet nu |
|---|---|---|---|---|
| C1 Multi-moteurs | ✅✅ | 🔸 | 🔸 | ✅ |
| C2 Évolution de schéma | ✅✅ | ✅ | ✅ | ❌ |
| C3 ACID | ✅✅ | ✅✅ | ✅✅ | ❌ |
| C4 Évolution de partitions | ✅✅ | ❌ | 🔸 | ❌ |
| C5 Time travel | ✅✅ | ✅✅ | ✅ | ❌ |

## Décision

**Apache Iceberg (Option A)**, avec un **catalogue REST** (implémentation `iceberg-rest-fixture`
en dev, adossé à PostgreSQL) partagé par Spark, Trino et dbt.

Les critères décisifs sont **C1** et **C2**. L'architecture repose sur une séparation nette
écriture (Spark) / lecture (Trino) : un format traitant les deux moteurs en citoyens de
première classe est structurant. Et l'évolution de schéma par identifiant de colonne est
précisément la garantie dont a besoin un flux réseau dont les compteurs changent avec le firmware.

## Conséquences

**Positives**
- `ALTER TABLE ... ADD COLUMN` sur `network.metrics` v1→v2 : aucune réécriture, aucune interruption.
- Trino et Spark voient la **même table**, sans copie ni synchronisation.
- Le time travel permet de reproduire exactement un entraînement de modèle (traçabilité MLflow ↔ snapshot Iceberg).
- `MERGE INTO` rend les backfills idempotents (FR-M2-011).

**Négatives**
- **Le problème des petits fichiers est réel** en streaming micro-batch : sans compaction, les performances de lecture se dégradent vite. → Procédure de maintenance planifiée obligatoire (`rewrite_data_files`, `expire_snapshots`, `remove_orphan_files`), documentée en runbook (FR-M2-009).
- Le catalogue devient un **point de défaillance** : sans lui, aucune table n'est lisible. → Sauvegarde du PostgreSQL du catalogue incluse au plan DR (Phase 8).
- Une brique supplémentaire à exploiter et à monitorer.

**À surveiller**
- Nombre de fichiers par partition et taille moyenne (cible : 128–512 Mo par fichier).
- Croissance du nombre de snapshots (rétention : 7 jours).

## Notes d'implémentation

**Stratégie de partitionnement** (le partitionnement caché autorise l'ajustement ultérieur) :

| Table | Partitionnement | Justification |
|---|---|---|
| `bronze.network_metrics` | `days(event_timestamp)` | Ingestion chronologique, purge par jour |
| `silver.network_metrics` | `days(event_timestamp)`, `region` | Requêtes quasi toujours filtrées par région |
| `silver.cdr` | `days(event_timestamp)`, `bucket(16, subscriber_token)` | Évite les partitions déséquilibrées par abonné très actif |
| `gold.network_kpis_hourly` | `days(kpi_date)`, `technology` | Agrégats lus par technologie |
| `gold.customer_360` | `bucket(32, subscriber_token)` | Table de dimension, accès par clé |

**Configuration clé**
```properties
write.format.default              = parquet
write.parquet.compression-codec   = zstd
write.target-file-size-bytes      = 134217728   # 128 Mo
write.distribution-mode           = hash
history.expire.max-snapshot-age-ms = 604800000  # 7 j
format-version                    = 2           # row-level deletes
```

## Références
- [FR-M2-005/006/009/010/011](../product/SRS.md) · [ADR-004](ADR-004-object-storage.md) · [ADR-006](ADR-006-query-engine.md)
