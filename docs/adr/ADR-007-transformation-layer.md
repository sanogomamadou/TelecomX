# ADR-007 — dbt pour les transformations silver → gold

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 |

## Contexte

La couche gold comporte huit datasets métier (FR-M2-008) construits par agrégation et jointure
depuis silver. Ces transformations sont **essentiellement du SQL analytique** : agrégations
temporelles, fenêtres glissantes, jointures avec le référentiel, calculs de KPI.

Trois besoins accompagnent ces transformations :
1. **Tests de données** — un KPI faux se propage aux modèles ML et aux réponses des agents.
2. **Lineage** — exigence de gouvernance (FR-M4-007) : savoir que `gold.churn_features` dérive de
   `silver.cdr` + `silver.recharge` + `silver.network_metrics`.
3. **Documentation** — le dictionnaire de données doit être généré, pas maintenu à la main (FR-M3-006).

Écrire ces transformations en PySpark serait possible mais reviendrait à réimplémenter à la main
la gestion des dépendances, les tests, la documentation et le lineage.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Tests de données déclaratifs | 🔴 Élevé |
| C2 | Lineage et documentation générés automatiquement | 🔴 Élevé |
| C3 | Gestion du graphe de dépendances entre modèles | 🔴 Élevé |
| C4 | Matérialisation incrémentale | 🟠 Moyen |
| C5 | Compatibilité Trino/Iceberg | 🔴 Élevé |

## Options envisagées

| Option | Analyse |
|---|---|
| **A — dbt-core (adaptateur `dbt-trino`)** | ✅ Graphe de dépendances par simple `ref()` ✅ Tests déclaratifs (`not_null`, `unique`, `accepted_values`, `relationships`) + tests SQL personnalisés ✅ `dbt docs` génère lineage et dictionnaire ✅ Matérialisations `incremental` avec stratégie `merge` (Iceberg) ✅ Standard de l'industrie analytics engineering ❌ SQL uniquement — inadapté aux transformations non exprimables en SQL ❌ Une brique et un cycle d'exécution de plus |
| **B — PySpark uniquement** | ✅ Un seul moteur, expressivité totale, déjà présent ❌ Il faudrait réimplémenter tests, lineage, docs et orchestration du graphe ❌ Les agrégations SQL sont plus lisibles en SQL qu'en chaînes d'API |
| **C — SQLMesh** | ✅ Conception élégante : compréhension sémantique du SQL, gestion d'environnements virtuels, détection de changement de colonne ❌ Écosystème et adoption bien plus récents ❌ Moins reconnu sur le marché de l'emploi (critère portfolio) |
| **D — Vues Trino natives** | ✅ Zéro outil supplémentaire ❌ Aucun test, aucun lineage, aucune matérialisation incrémentale ❌ Les vues empilées deviennent ingérables |

## Décision

**dbt-core avec l'adaptateur `dbt-trino` (Option A)**, pour toutes les transformations
**silver → gold**.

**Frontière explicite entre Spark et dbt** :

| Couche | Outil | Pourquoi |
|---|---|---|
| Kafka → bronze | **Spark** | Streaming, tolérance de panne, checkpointing |
| bronze → silver | **Spark** | Tokenisation PII, dédup à état, logique non-SQL |
| silver → gold | **dbt** | Agrégations SQL, tests, lineage, documentation |

Cette frontière n'est pas arbitraire : la tokenisation HMAC ([ADR-013](ADR-013-pii-tokenization.md))
et la déduplication fenêtrée relèvent du code impératif, tandis que les KPI relèvent du SQL déclaratif.

## Conséquences

**Positives**
- `dbt test` devient une **porte de qualité bloquante** avant publication en gold (FR-M4-006).
- `dbt docs generate` produit gratuitement le dictionnaire de données et le graphe de lineage.
- Les modèles gold sont versionnés, revus et testés comme du code applicatif.
- Les modèles incrémentaux évitent de recalculer l'historique à chaque exécution.

**Négatives**
- Un troisième moteur d'exécution dans le pipeline (Spark, Trino, dbt) → complexité d'orchestration : l'ordonnancement doit garantir que silver est à jour avant l'exécution dbt.
- Le lineage dbt s'arrête à ses propres frontières : il ne voit ni Kafka ni Spark → le lineage de bout en bout est complété par OpenMetadata (Phase 2).
- Les macros Jinja complexes nuisent à la lisibilité → règle : privilégier le SQL explicite, réserver les macros aux répétitions avérées.

## Notes d'implémentation

```
data-platform/dbt/
├── models/
│   ├── silver/          # sources déclarées (écrites par Spark, référencées ici)
│   ├── gold/
│   │   ├── network/     # network_kpis_hourly, cell_performance, equipment_health
│   │   ├── customer/    # customer_360, churn_features
│   │   ├── fraud/       # fraud_features
│   │   ├── traffic/     # traffic_features
│   │   └── revenue/     # revenue_metrics
│   └── marts/           # vues d'exposition destinées aux agents et à l'API
├── tests/               # tests SQL personnalisés (règles métier)
├── macros/
└── seeds/               # référentiels : régions, technologies, capacités de cellules
```

**Conventions**
- Un modèle = un fichier = une responsabilité ; nommage `<couche>_<domaine>_<grain>`.
- Tout modèle gold déclare au minimum : test d'unicité de clé, non-nullité des dimensions, bornes des mesures.
- Matérialisation : `incremental` (stratégie `merge` sur clé métier) dès que le volume le justifie, `table` sinon.
- Les règles métier du PRD §15 sont implémentées comme tests dbt personnalisés (`active_users <= cell_capacity`).

## Références
- [FR-M2-008](../product/SRS.md) · [FR-M3-002/006](../product/SRS.md) · [FR-M4-004/006/007](../product/SRS.md) · [ADR-006](ADR-006-query-engine.md)
