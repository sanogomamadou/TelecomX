# ADR-006 — Trino comme moteur SQL d'interrogation

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 |

## Contexte

Plusieurs consommateurs très différents doivent interroger le Lakehouse :

- les **tools des agents IA** (`query_network_metrics`) — requêtes courtes, latence critique (< 5 s) ;
- **dbt**, pour matérialiser les modèles silver → gold ;
- les **analystes** en libre-service (U3) ;
- l'**API** et les dashboards, pour les KPI.

Ces requêtes doivent viser les **mêmes tables Iceberg** que celles écrites par Spark, sans copie
ni synchronisation. Elles doivent aussi, à terme, pouvoir **joindre** des données du lac avec des
données opérationnelles PostgreSQL (référentiel, incidents) — sans ETL supplémentaire.

Utiliser Spark SQL pour ces requêtes serait possible mais inadapté : le coût de démarrage d'un job
(dizaines de secondes) est rédhibitoire face à un SLO de 5 s, et un agent IA ne peut pas
mobiliser un cluster Spark pour compter des lignes.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Latence interactive sur requêtes analytiques (p95 < 5 s) | 🔴 Élevé |
| C2 | Support Iceberg en lecture **et** écriture | 🔴 Élevé |
| C3 | Fédération multi-sources (Iceberg + PostgreSQL + Kafka) | 🟠 Moyen |
| C4 | Compatibilité dbt | 🔴 Élevé |
| C5 | Sécurité fine (RBAC, row-level, masquage de colonnes) | 🟠 Moyen |
| C6 | Empreinte ressources | 🟠 Moyen |

## Options envisagées

| Option | Analyse |
|---|---|
| **A — Trino** | ✅ Conçu pour l'interactif (MPP en mémoire, pas de matérialisation intermédiaire) ✅ Connecteur Iceberg de première classe (lecture, écriture, `MERGE`, maintenance) ✅ **Fédération** : un `JOIN` entre Iceberg et PostgreSQL dans une seule requête ✅ Adaptateur dbt-trino officiel ✅ Contrôle d'accès fin (`file`-based ou Open Policy Agent), masquage de colonnes ❌ Tout en mémoire : une requête trop large échoue au lieu de déborder sur disque ❌ Coordinateur + worker ⇒ ~2 Go en local |
| **B — Spark SQL (Thrift Server)** | ✅ Déjà présent dans la stack, aucun composant supplémentaire ❌ Latence de démarrage incompatible avec C1 ❌ Pas de fédération réelle ❌ Le serveur Thrift est un point de contention connu |
| **C — DuckDB** | ✅ Extrêmement léger et rapide, excellent sur fichiers Parquet ❌ Mono-processus, mono-utilisateur : inadapté à des consommateurs concurrents (API + agents + dbt) ❌ Support Iceberg encore limité (lecture seule, fonctionnalités partielles) ❌ Ne démontre pas d'architecture distribuée |
| **D — ClickHouse** | ✅ Performances OLAP exceptionnelles ❌ Impose son propre stockage : dupliquerait le Lakehouse et briserait la source unique de vérité ❌ Support Iceberg immature |
| **E — Dremio / Presto** | 🔸 Proches de Trino ; Trino est le fork le plus actif et le mieux intégré à Iceberg |

## Décision

**Trino (Option A)**, coordinateur + 1 worker en développement, avec un catalogue Iceberg pointant
sur le catalogue REST partagé avec Spark ([ADR-003](ADR-003-table-format.md)), plus un catalogue
PostgreSQL pour les données opérationnelles.

**Répartition des responsabilités** (règle d'architecture) :

| Moteur | Responsabilité | Ne fait pas |
|---|---|---|
| **Spark** | Ingestion streaming, transformations lourdes, ML, maintenance de tables | Requêtes interactives |
| **Trino** | Requêtes interactives, transformations dbt, service des agents et de l'API | Ingestion streaming, écritures massives |

## Conséquences

**Positives**
- Les tools des agents obtiennent une réponse en quelques secondes sur des dizaines de millions de lignes.
- La fédération supprime un besoin d'ETL : joindre `gold.customer_360` (Iceberg) et `ops.incidents` (PostgreSQL) est une simple requête.
- dbt s'exécute sur Trino, donc les modèles gold sont matérialisés par le même moteur qui les sert.
- Le contrôle d'accès Trino applique la matrice RBAC (SRS §2.2) au niveau du moteur — défense en profondeur (FR-M3-005).

**Négatives**
- **Trino n'a pas de spill-to-disk généralisé** : une requête mal écrite (jointure cartésienne, agrégation sans filtre) échoue par manque de mémoire. → Limites par requête configurées et documentées ; les tools des agents imposent obligatoirement un filtre temporel et un `LIMIT`.
- Deux moteurs à exploiter (Spark **et** Trino) au lieu d'un.
- Composant supplémentaire dans le budget mémoire → profil `lakehouse` ([ADR-011](ADR-011-local-dev-strategy.md)).

**À surveiller**
- Requêtes lancées par les agents : un LLM peut générer une requête coûteuse → garde-fous obligatoires
  (`query.max-memory-per-node`, timeout, validation du SQL généré avant exécution).

## Notes d'implémentation

**Catalogues**
```
iceberg    → catalogue REST Iceberg (bronze, silver, gold)   [lecture + écriture dbt]
postgres   → base opérationnelle (référentiel, incidents, audit, approbations)
memory     → tables temporaires de test
```

**Garde-fous de ressources (dev)**
```properties
query.max-memory              = 1GB
query.max-memory-per-node     = 512MB
query.max-execution-time       = 60s
query.max-total-memory         = 1.5GB
```

**Garde-fous applicatifs pour les tools d'agents** — un tool SQL n'expose jamais de SQL libre au LLM :
il expose des **fonctions paramétrées** (cellule, plage temporelle, granularité) qui construisent
la requête côté serveur. Cela élimine à la fois l'injection SQL et les requêtes ruineuses.

## Références
- [FR-M3-001/005](../product/SRS.md) · [NFR-PERF-004](../product/SRS.md) · [ADR-003](ADR-003-table-format.md) · [ADR-007](ADR-007-transformation-layer.md)
