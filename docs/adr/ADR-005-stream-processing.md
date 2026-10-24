# ADR-005 — Spark Structured Streaming comme moteur de traitement

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 |
| **À réexaminer si** | une latence < 1 s devient une exigence |

## Contexte

Il faut un moteur capable de traiter **le même code** en streaming (ingestion continue Kafka →
Iceberg) et en batch (backfill, réentraînement, retraitement d'une journée). Les exigences :

- Latence bronze → silver : **p95 < 3 min** (NFR-PERF-002) — une latence de quelques secondes suffit largement.
- Déduplication à état borné sur fenêtre 24 h (FR-M2-004).
- Gestion des événements en retard jusqu'à 5 min (FR-M1-010) → watermarking.
- Reprise sur checkpoint sans perte ni duplication (FR-M2-007).
- Écriture transactionnelle Iceberg.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Unification batch / streaming (un seul code, un seul modèle mental) | 🔴 Élevé |
| C2 | Intégration Iceberg en écriture transactionnelle | 🔴 Élevé |
| C3 | Latence atteignable | 🟠 Moyen (seuil : quelques minutes) |
| C4 | Empreinte ressources locale | 🟠 Moyen |
| C5 | Valeur sur le marché de l'emploi Big Data | 🟠 Moyen |

## Options envisagées

### Option A — Apache Spark Structured Streaming
✅ **Même API DataFrame en batch et en streaming** — le job de backfill est le job de streaming avec une source différente (C1, décisif)
✅ Intégration Iceberg mature (écriture, `MERGE INTO`, maintenance de tables)
✅ Écosystème complet : SQL, MLlib, connecteurs, et compétence Spark très demandée (C5)
✅ Checkpointing robuste, exactly-once vers un puits idempotent
❌ Micro-batch : latence plancher de l'ordre de la seconde (largement suffisante ici)
❌ JVM lourde : ~2 Go pour driver + executor en local (C4)

### Option B — Apache Flink
✅ Vrai streaming événement par événement : latence sub-seconde
✅ Gestion d'état et de fenêtres plus riche, sémantique exactly-once native
❌ API batch (unifiée depuis Flink 1.16) reste moins naturelle que celle de Spark
❌ Connecteur Iceberg fonctionnel mais moins de retours d'expérience que Spark
❌ Empreinte locale comparable, complexité opérationnelle supérieure (savepoints, gestion d'état)
❌ **Le gain de latence ne répond à aucune exigence** : viser 200 ms quand le SLO est à 3 min est du sur-dimensionnement

### Option C — Kafka Streams
✅ Très léger, pas de cluster à opérer, s'exécute dans l'application
❌ Écosystème Java/Scala uniquement — incompatible avec une plateforme ML Python
❌ Pas d'intégration Iceberg native ; inadapté aux traitements analytiques lourds

### Option D — Consommateur Python + écriture PyArrow
✅ Ultra-léger, pas de JVM
❌ Aucune tolérance de panne, aucun état géré, aucun watermarking : il faudrait tout réécrire
❌ Ne démontre aucune compétence Big Data distribué

## Comparaison

| Critère | A. Spark | B. Flink | C. Kafka Streams | D. Python |
|---|---|---|---|---|
| C1 Batch+stream unifié | ✅✅ | ✅ | ❌ | ❌ |
| C2 Iceberg | ✅✅ | ✅ | ❌ | 🔸 |
| C3 Latence | ✅ (secondes) | ✅✅ (ms) | ✅✅ | ✅ |
| C4 Ressources | 🔸 | 🔸 | ✅✅ | ✅✅ |
| C5 Valeur marché | ✅✅ | ✅ | 🔸 | ❌ |

## Décision

**Spark Structured Streaming (Option A)**, en PySpark, avec `trigger(processingTime="30 seconds")`
pour l'ingestion bronze et des micro-batchs plus longs pour les transformations silver.

Flink serait le meilleur choix si la latence était contrainte sous la seconde. Elle ne l'est pas :
le SLO est à 3 minutes. Le critère qui tranche est **C1** — pouvoir rejouer trois mois d'historique
avec exactement le même code que le flux temps réel élimine une classe entière de bugs
(divergence batch/streaming), qui est précisément le problème que l'architecture Lambda a passé
une décennie à essayer de contourner.

## Conséquences

**Positives**
- Un seul code de transformation, testé une fois, exécuté en streaming et en batch.
- Backfill trivial : changer la source Kafka en source Iceberg, réutiliser la même fonction.
- Les jobs de maintenance Iceberg (compaction) s'exécutent dans le même moteur.
- PySpark s'aligne avec la stack Python de la plateforme ML.

**Négatives**
- **Empreinte mémoire significative** : le profil `lakehouse` réserve ~2,3 Go pour Spark seul → poste le plus lourd du budget local ([ADR-011](ADR-011-local-dev-strategy.md), §Révision).
- Micro-batch ⇒ **génération de petits fichiers** dans Iceberg → compaction obligatoire ([ADR-003](ADR-003-table-format.md), conséquences négatives).
- Le démarrage d'un job Spark coûte plusieurs dizaines de secondes — inadapté aux tâches très courtes.
- Le débogage d'un job distribué est plus laborieux qu'un script Python.

**À surveiller**
- Durée du micro-batch vs intervalle de trigger : si la durée dépasse l'intervalle, le lag s'accumule.
- Taille de l'état de déduplication (fenêtre 24 h × débit).

## Notes d'implémentation

**Architecture des jobs**

```
data-platform/spark/
├── jobs/
│   ├── bronze_ingest.py       # Kafka → bronze (générique, piloté par contrat)
│   ├── silver_transform.py    # bronze → silver (validation, dédup, PII, enrichissement)
│   └── maintenance.py         # compaction, expiration de snapshots
├── transformations/           # fonctions PURES DataFrame → DataFrame (testables sans Spark cluster)
└── conftest.py                # session Spark locale pour les tests
```

> 🧪 **Règle de testabilité** : toute logique métier vit dans `transformations/` sous forme de
> fonctions pures. Les jobs ne font qu'assembler source → transformation → puits. Les tests
> unitaires s'exécutent sur une `SparkSession` locale avec de petits DataFrames, sans Kafka ni MinIO.

**Configuration streaming clé**
```python
.option("checkpointLocation", "s3a://telecom-checkpoints/<job>/")
.option("maxOffsetsPerTrigger", 50_000)      # borne la taille du micro-batch
.trigger(processingTime="30 seconds")
.outputMode("append")
# watermark de 5 min pour les événements en retard (FR-M1-010)
.withWatermark("event_timestamp", "5 minutes")
.dropDuplicates(["event_id"])                 # dédup bornée par le watermark
```

**Ressources en développement** : driver 768 Mo, 1 executor 1,5 Go, 2 cœurs, `spark.sql.shuffle.partitions=8`
(la valeur par défaut de 200 est catastrophique en local).

## Références
- [FR-M2-001…011](../product/SRS.md) · [NFR-PERF-002](../product/SRS.md) · [ADR-003](ADR-003-table-format.md) · [ADR-011](ADR-011-local-dev-strategy.md)
