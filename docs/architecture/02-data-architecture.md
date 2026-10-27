# Architecture de données

| Champ | Valeur |
|---|---|
| **Version** | 1.0 · 2026-09-10 |
| **Statut** | ✅ Baseline Phase 0 |
| **Documents liés** | [Architecture système](01-system-architecture.md) · [Dictionnaire de données](../data/data-dictionary.md) · [Contrats](../data/contracts/README.md) · [ADR-003](../adr/ADR-003-table-format.md) · [ADR-015](../adr/ADR-015-data-contracts.md) |

---

## 1. Modèle en zones (medallion)

```
   SOURCES          BRONZE              SILVER               GOLD              CONSOMMATION
   ────────         ──────              ──────               ────              ────────────
   9 flux    ──►   brut, immuable  ──► nettoyé,          ──► agrégats     ──►  Agents IA
   Kafka           tel que reçu        conforme,             métier            Modèles ML
   Avro            + métadonnées       tokenisé,             prêts à           Dashboards
                                       enrichi               l'emploi          Analystes

   Écrivain :      Spark               Spark                 dbt (Trino)
   Latence   :     < 60 s              < 3 min               < 15 min
   Rétention :     90 j (7 j dev)      400 j (30 j dev)      5 ans
   Accès     :     platform_admin      analystes + ML        tous rôles (RLS)
                   uniquement          (PII tokenisée)
```

### 1.1 Règles par zone

| Zone | Ce qui est fait | Ce qui est **interdit** |
|---|---|---|
| **Bronze** | Écriture telle quelle, ajout de métadonnées techniques (offset, partition, timestamps), partitionnement par date | Toute transformation métier, tout filtrage, toute correction |
| **Silver** | Validation de schéma, déduplication, normalisation de types et d'unités, **tokenisation PII**, enrichissement par référentiel, gestion des retards | Agrégation métier, jointures complexes, calculs de KPI |
| **Gold** | Agrégations, KPI, features ML, dimensions métier, vues d'exposition | Accès à des identifiants directs, logique d'ingestion |

> 🧱 **Bronze est immuable (principe P2).** Une donnée fausse en bronze le reste : la correction
> se fait en silver, jamais par réécriture. C'est ce qui rend le rejeu, l'audit et le débogage
> possibles — et ce qui permet de dire, six mois plus tard, *ce que le système a réellement reçu*.

---

## 2. Flux de données

### 2.1 Cartographie source → zone

| Flux source | Topic Kafka | Table bronze | Table silver | Alimente en gold |
|---|---|---|---|---|
| Métriques radio | `network.metrics` | `bronze.network_metrics` | `silver.network_metrics` | `network_kpis_hourly`, `cell_performance`, `traffic_features` |
| Alarmes réseau | `network.alarms` | `bronze.network_alarms` | `silver.network_alarms` | `cell_performance`, `equipment_health` |
| CDR voix | `cdr.events` | `bronze.cdr` | `silver.cdr` | `customer_360`, `churn_features`, `revenue_metrics` |
| Sessions data | `data.sessions` | `bronze.data_sessions` | `silver.data_sessions` | `customer_360`, `traffic_features`, `revenue_metrics` |
| SMS | `sms.events` | `bronze.sms` | `silver.sms` | `customer_360`, `fraud_features` |
| USSD | `ussd.events` | `bronze.ussd` | `silver.ussd` | `customer_360` |
| Recharges | `recharge.events` | `bronze.recharge` | `silver.recharge` | `customer_360`, `churn_features`, `revenue_metrics` |
| Bundles | `bundle.events` | `bronze.bundle` | `silver.bundle` | `customer_360`, `revenue_metrics` |
| Mobile Money | `mobilemoney.transactions` | `bronze.momo` | `silver.momo` | `fraud_features`, `revenue_metrics` |
| Événements client | `customer.events` | `bronze.customer_events` | `silver.customer_events` | `customer_360`, `churn_features` |

### 2.2 Référentiels (dimensions statiques)

Chargés en seed dbt, versionnés dans Git, sans passage par Kafka :

| Référentiel | Contenu | Cardinalité |
|---|---|---|
| `ref.regions` | code, nom, population, typologie | 9 |
| `ref.sites` | site_id, région, coordonnées, type, date de mise en service | 1 610 |
| `ref.cells` | cell_id, site_id, technologie, bande, **capacité**, azimut | 4 821 |
| `ref.equipment` | equipment_id, site_id, type, modèle, date d'installation | 8 492 |
| `ref.technologies` | 2G/3G/4G/5G, débit théorique, latence de référence | 4 |
| `ref.bundles` | code, libellé, volume, prix, validité | 28 |
| `ref.alarm_types` | code, libellé, sévérité, domaine, procédure | 51 |

> La `capacity_mb_per_hour` de `ref.cells` est indispensable : elle transforme une prévision de
> trafic en **prévision de saturation** (UC-02), et fonde la règle de qualité
> `active_users ≤ cell_capacity` (FR-M4-004).

---

## 3. Modèle de données gold

### 3.1 Datasets métier

| Table | Grain | Contenu principal | Consommateur |
|---|---|---|---|
| `gold.network_kpis_hourly` | cellule × heure | trafic, utilisateurs actifs, latence, perte, taux d'échec, disponibilité | Network Agent, dashboards |
| `gold.cell_performance` | cellule × jour | KPI agrégés, comptage d'alarmes, score de santé, rang régional | Network Agent, NOC |
| `gold.equipment_health` | équipement × jour | température, charge, uptime, historique de pannes, erreurs | Maintenance Agent, modèle |
| `gold.traffic_features` | cellule × heure | features temporelles pour la prévision (retards, moyennes glissantes, calendrier) | Modèle de prévision |
| `gold.customer_360` | abonné | usage, recharges, bundles, ARPU, ancienneté, qualité vécue, réclamations | Customer Agent, marketing |
| `gold.churn_features` | abonné × jour | tendances d'usage, fréquence de recharge, évolution ARPU, qualité réseau, interactions | Modèle de churn |
| `gold.fraud_features` | transaction | vélocité, écart au comportement habituel, géographie, appareil, réseau de contreparties | Modèle de fraude |
| `gold.revenue_metrics` | région × jour × produit | revenu, volumes, activations, taux de conversion | Revenue Agent, direction |

### 3.2 Principe de conception des features

> ⚠️ **Toute feature d'un dataset ML est calculée à partir de données disponibles à l'instant de
> la prédiction.** Une feature de `churn_features` datée du jour J ne doit utiliser que des données
> antérieures ou égales à J — jamais l'information de résiliation elle-même, ni aucune donnée
> postérieure.

Cette règle (absence de fuite temporelle, FR-M5-009) est vérifiée par des tests dbt dédiés et par
la convention de nommage : toute feature dérivée d'une fenêtre passée porte explicitement son
horizon (`traffic_mb_lag_24h`, `recharge_count_30d`, `arpu_trend_90d`).

---

## 4. Stratégie de partitionnement

| Table | Partitionnement | Justification |
|---|---|---|
| `bronze.*` | `tenant_id`, `days(ingestion_timestamp)` | Ingestion chronologique, purge par jour |
| `silver.network_metrics` | `tenant_id`, `days(event_timestamp)`, `region` | Les requêtes filtrent presque toujours par région |
| `silver.cdr` | `tenant_id`, `days(event_timestamp)`, `bucket(16, subscriber_token)` | Le bucketing évite les partitions déséquilibrées par abonné très actif |
| `silver.momo` | `tenant_id`, `days(event_timestamp)` | Volume modéré |
| `gold.network_kpis_hourly` | `tenant_id`, `days(kpi_hour)`, `technology` | Lecture fréquente par technologie |
| `gold.customer_360` | `tenant_id`, `bucket(32, subscriber_token)` | Table de dimension, accès par clé |
| `gold.fraud_features` | `tenant_id`, `days(transaction_date)` | Analyse quotidienne |

**Pourquoi `tenant_id` en tête** : l'élagage de partition rend l'isolation multi-tenant
([ADR-016](../adr/ADR-016-multi-tenancy.md)) simultanément **sûre** et **performante** —
une requête d'un tenant ne lit physiquement jamais les fichiers d'un autre.

**Pourquoi le partitionnement caché d'Iceberg est décisif ici** : la stratégie ci-dessus est une
hypothèse fondée sur les usages *attendus*. Les usages réels différeront. Iceberg permet de faire
évoluer le partitionnement sans réécrire l'historique ni casser les requêtes existantes —
ce qui rend cette hypothèse révisable au lieu d'être définitive.

---

## 5. Gestion du temps

Trois horodatages distincts coexistent, et les confondre est une source classique d'erreurs :

| Horodatage | Signification | Usage |
|---|---|---|
| `event_timestamp` | Instant de l'événement dans le monde réel | **Partitionnement, fenêtres, analyses métier** |
| `ingestion_timestamp` | Instant d'entrée dans la plateforme | Mesure de fraîcheur, SLA, audit |
| `processing_timestamp` | Instant de traitement par Spark | Débogage, mesure de latence de pipeline |

**Conventions non négociables**
- Tous les horodatages sont en **UTC**, avec fuseau explicite (règle `DTZ` de ruff, [ADR-014](../adr/ADR-014-python-tooling.md)).
- Les fenêtres de streaming utilisent `event_timestamp` avec un **watermark de 5 minutes** (FR-M1-010).
- Un événement arrivant au-delà du watermark est routé vers une table `late_arrivals` plutôt que perdu.
- L'affichage en heure locale (UTC+0, Mali) n'a lieu que dans le frontend.

---

## 6. Qualité des données

### 6.1 Points de contrôle

```
Production ──► [1] Validation Avro ────────► DLQ si non conforme
               │
Bronze ───────► [2] Contrôles techniques ──► métriques + quarantaine
               │
Silver ───────► [3] Règles métier ─────────► métriques + quarantaine
               │
Gold ─────────► [4] Tests dbt ─────────────► ⛔ BLOQUANT si sous le seuil
               │
Publication ──► [5] Contrôle de fraîcheur ─► alerte
```

### 6.2 Catégories de règles

| Catégorie | Exemples | Point de contrôle |
|---|---|---|
| **Schéma** | conformité Avro, types, champs obligatoires | [1] |
| **Domaine** | `0 ≤ drop_rate ≤ 100`, `latency_ms ≥ 0`, `traffic_mb ≥ 0` | [2] |
| **Unicité** | `event_id` unique, pas de doublon en silver | [2] |
| **Référentiel** | `cell_id` existe dans `ref.cells`, `site_id` cohérent | [3] |
| **Métier** | `active_users ≤ cell_capacity`, `download + upload = total_volume`, `call_duration ≥ 0` | [3] |
| **Cohérence temporelle** | `event_timestamp ≤ ingestion_timestamp`, pas de date future | [2] |
| **Complétude** | ≥ 99,9 % des champs obligatoires renseignés | [3] |
| **Fraîcheur** | table gold mise à jour dans le SLO du contrat | [5] |
| **Volumétrie** | écart au volume attendu > 3σ ⇒ anomalie de collecte | [5] |

> 📌 **Les règles ne sont pas écrites deux fois.** Elles sont déclarées dans les attributs
> `telecomx.*` des contrats Avro ([ADR-015](../adr/ADR-015-data-contracts.md)) et **générées**
> en validations Pydantic, assertions Spark et tests dbt. Le contrat est exécutable.

### 6.3 Traitement des non-conformités

| Gravité | Traitement | Exemple |
|---|---|---|
| **Bloquante** | DLQ, l'événement n'entre pas | Schéma invalide, `tenant_id` absent |
| **Majeure** | Ingéré, marqué `dq_status = 'quarantined'`, exclu de gold | `cell_id` inconnu du référentiel |
| **Mineure** | Ingéré, marqué, corrigé si possible, compté | `latency_ms` aberrant → borné, drapeau posé |

Aucune donnée n'est **jamais supprimée silencieusement**. Une donnée rejetée est traçable, comptée
et rejouable après correction.

---

## 7. Lineage

```
Système source
    │  contrat Avro (version)
    ▼
Topic Kafka  ──────────────────────► OpenLineage : job Spark
    ▼
bronze.<table>  (snapshot Iceberg) ─► OpenLineage : job Spark
    ▼
silver.<table>  (snapshot Iceberg) ─► lineage dbt (ref())
    ▼
gold.<table>    (snapshot Iceberg) ─► MLflow : dataset d'entraînement
    ▼
Modèle ML (version registry) ──────► Tool d'agent (version)
    ▼
Réponse d'agent (trace Langfuse)
```

Ce lineage répond à deux questions opérationnelles réelles :
- **Impact** : « si je modifie `silver.cdr`, qu'est-ce qui casse ? »
- **Provenance** : « cette réponse d'agent affirme -8,3 % de revenu — d'où vient ce chiffre ? »

La seconde est particulièrement importante : elle permet de **remonter d'une affirmation d'un
agent jusqu'aux lignes de données qui la fondent**, ce qui est la condition d'auditabilité d'un
système d'aide à la décision.

---

## 8. Rétention et cycle de vie

| Zone | Dev | Production | Justification |
|---|---|---|---|
| Bronze | 7 j | 90 j | Rejeu et audit ; contient potentiellement des identifiants directs ⇒ rétention courte ([ADR-013](../adr/ADR-013-pii-tokenization.md)) |
| Silver | 30 j | 400 j | Analyses sur 13 mois (comparaison année sur année) |
| Gold | illimité | 5 ans | Historique métier, entraînement de modèles |
| DLQ | 30 j | 30 j | Délai de correction et de rejeu |
| Checkpoints | 3 j | 7 j | Reprise de streaming |
| Snapshots Iceberg | 7 j | 30 j | Time travel et rollback |

**Maintenance planifiée** (FR-M2-009) : compaction quotidienne, expiration de snapshots
hebdomadaire, suppression des fichiers orphelins mensuelle. Documentée en runbook.

---

## 9. Volumétrie projetée (production)

| Zone | Par jour | À 1 an | Format |
|---|---|---|---|
| Bronze | ~42 Go | ~3,8 To (90 j) | Parquet + zstd |
| Silver | ~28 Go | ~11 To (400 j) | Parquet + zstd |
| Gold | ~2 Go | ~3,6 To (5 ans) | Parquet + zstd |
| **Total** | **~72 Go/j** | **~18 To** | |

En développement, `SPEED_FACTOR = 0.001` ramène ces volumes à ~70 Mo/jour, compatible avec les
54 Go disponibles.

---

*Voir le [dictionnaire de données](../data/data-dictionary.md) pour le détail colonne par colonne.*
