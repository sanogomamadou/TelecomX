# Dictionnaire de données

| Champ | Valeur |
|---|---|
| **Version** | 1.0 · 2026-09-10 |
| **Statut** | 📐 Spécification Phase 0 — sera **généré automatiquement** depuis dbt en Phase 1 |
| **Documents liés** | [Architecture données](../architecture/02-data-architecture.md) · [Contrats](contracts/README.md) |

> ℹ️ Ce document spécifie le modèle **cible**. À partir de la Phase 1, la version faisant foi est
> générée par `dbt docs generate` depuis les métadonnées des modèles (FR-M3-006), ce qui garantit
> qu'elle ne diverge jamais du code. Ce fichier reste la **spécification de conception** ayant
> présidé à sa construction.

---

## 1. Conventions

### 1.1 Nommage

| Élément | Convention | Exemple |
|---|---|---|
| Base de données | `<zone>` | `bronze`, `silver`, `gold` |
| Table | `<domaine>_<entité>[_<grain>]` | `network_kpis_hourly` |
| Colonne | `snake_case` | `packet_loss_pct` |
| Mesure | `<nom>_<unité>` | `traffic_mb`, `latency_ms`, `drop_rate_pct` |
| Booléen | `is_` / `has_` | `is_roaming`, `has_active_alarm` |
| Horodatage | `<nom>_timestamp` (UTC) | `event_timestamp` |
| Date | `<nom>_date` | `kpi_date` |
| Token PII | `<entité>_token` | `subscriber_token` |
| Feature à fenêtre | `<mesure>_<agrégat>_<fenêtre>` | `traffic_mb_avg_7d` |
| Feature décalée | `<mesure>_lag_<décalage>` | `traffic_mb_lag_24h` |

> ⚠️ **L'unité fait partie du nom de la colonne.** `latency` est ambigu ; `latency_ms` ne l'est
> pas. Cette règle évite la classe d'erreurs la plus coûteuse et la plus silencieuse d'une
> plateforme de données : une mesure interprétée dans la mauvaise unité, qui fausse un KPI, un
> modèle et une réponse d'agent sans jamais lever d'exception.

### 1.2 Colonnes techniques

| Zone | Colonnes ajoutées |
|---|---|
| **Bronze** | `_kafka_topic`, `_kafka_partition`, `_kafka_offset`, `_kafka_timestamp`, `_ingestion_timestamp`, `_raw_payload` |
| **Silver** | `_processing_timestamp`, `_dq_status`, `_dq_violations`, `_source_event_id`, `_schema_version` |
| **Gold** | `_computed_at`, `_dbt_model_version` |

`_dq_status` prend les valeurs `valid`, `quarantined`, `corrected` — une donnée n'est jamais
supprimée silencieusement (voir [architecture données §6.3](../architecture/02-data-architecture.md)).

---

## 2. Référentiels (`ref`)

### `ref.regions` — 9 lignes

| Colonne | Type | Description |
|---|---|---|
| `region_code` | varchar | 🔑 Code court (BKO, KYS, KLK…) |
| `region_name` | varchar | Nom complet |
| `population` | bigint | Population — sert à normaliser les KPI |
| `area_type` | varchar | `urban`, `periurban`, `rural` |
| `subscriber_count` | bigint | Base d'abonnés rattachée |

### `ref.sites` — 1 610 lignes

| Colonne | Type | Description |
|---|---|---|
| `site_id` | varchar | 🔑 Format `SITE-NNN` |
| `region_code` | varchar | 🔗 `ref.regions` |
| `latitude`, `longitude` | double | Coordonnées du site (équipement, non personnelles) |
| `site_type` | varchar | `macro`, `micro`, `indoor`, `rooftop` |
| `commissioned_date` | date | Mise en service — l'âge influence le taux de panne |
| `has_backup_power` | boolean | Alimentation de secours |
| `backhaul_type` | varchar | `fiber`, `microwave`, `satellite` |
| `backhaul_capacity_mbps` | double | **Capacité du lien de transport** — clé du diagnostic de congestion backhaul |

### `ref.cells` — 4 821 lignes

| Colonne | Type | Description |
|---|---|---|
| `cell_id` | varchar | 🔑 Format `CELL-NNNN` |
| `site_id` | varchar | 🔗 `ref.sites` |
| `technology` | varchar | `2G`, `3G`, `4G`, `5G` |
| `frequency_band` | varchar | Bande de fréquence |
| `azimuth_degrees` | int | Orientation du secteur |
| `capacity_mb_per_hour` | double | **Capacité de trafic** — transforme une prévision en prévision de *saturation* |
| `capacity_users` | int | **Capacité en utilisateurs simultanés** — fonde la règle `active_users ≤ cell_capacity` |
| `is_active` | boolean | Cellule en service |

### `ref.equipment` — 8 492 lignes

| Colonne | Type | Description |
|---|---|---|
| `equipment_id` | varchar | 🔑 Format `<TYPE>-NNN` |
| `site_id` | varchar | 🔗 `ref.sites` |
| `equipment_type` | varchar | `BBU`, `RRU`, `ROUTER`, `GENSET`… |
| `vendor`, `model` | varchar | Constructeur et modèle |
| `installed_date` | date | Installation — base du calcul d'âge |
| `expected_lifetime_years` | int | Durée de vie nominale |
| `last_maintenance_date` | date | Dernière intervention |

### `ref.bundles`, `ref.alarm_types`, `ref.technologies`, `ref.tenants`

Référentiels complémentaires, chargés en seed dbt et versionnés : `ref.tenants` (3 lignes),
`ref.technologies` (4), `ref.bundles` (28) et `ref.alarm_types` (51, avec la colonne `sop_ref` qui
désigne la procédure d'exploitation à suivre).

> 📌 Les ordres de grandeur annoncés en Phase 0 (« ~4 800 équipements », « ~40 forfaits »,
> « ~120 alarmes ») étaient des estimations. Les valeurs ci-dessus sont celles des fichiers générés,
> qui font foi. Le parc de 8 492 équipements est cohérent avec le débit de ~40 messages/s annoncé
> pour `device.events` : chaque équipement émet environ toutes les 3,5 minutes.

---

## 3. Zone bronze

Structure systématique : **charge utile du contrat Avro + colonnes techniques**, sans aucune
transformation. Une table bronze par flux, nommée d'après le contrat.

| Table | Contrat source | Volume/jour (prod) |
|---|---|---|
| `bronze.network_metrics` | `network-metrics-v1` | 6,9 M |
| `bronze.network_alarms` | `network-alarms-v1` | 260 K |
| `bronze.cdr` | `cdr-v1` | 99 M |
| `bronze.data_sessions` | `data-session-v1` | 50 M |
| `bronze.sms` | `sms-v1` | 28 M |
| `bronze.ussd` | `ussd-v1` | 18 M |
| `bronze.recharge` | `recharge-v1` | 8,2 M |
| `bronze.bundle` | `bundle-v1` | 2,6 M |
| `bronze.momo` | `mobilemoney-transaction-v1` | 2,1 M |
| `bronze.customer_events` | `customer-event-v1` | 1,0 M |
| `bronze.equipment_telemetry` | `equipment-telemetry-v1` | 3,5 M |

🔒 **Accès restreint à `platform_admin`** — peut contenir des identifiants directs
([ADR-013](../adr/ADR-013-pii-tokenization.md)).

---

## 4. Zone silver

Même granularité que bronze, avec quatre transformations : **validation, déduplication,
tokenisation PII, enrichissement**.

**Transformations appliquées systématiquement**

| Transformation | Effet |
|---|---|
| Tokenisation | `caller_id` → `subscriber_token`, `receiver_account` → `receiver_token`… (colonne source supprimée) |
| Enrichissement cellule | Ajout de `site_id`, `region_code`, `technology`, `capacity_mb_per_hour` |
| Normalisation | Unités homogénéisées, énumérations normalisées, horodatages en UTC |
| Déduplication | Sur `event_id`, fenêtre 24 h |
| Qualification | `_dq_status`, `_dq_violations` renseignés |

**Table particulière**

`silver.late_arrivals` — événements arrivés au-delà du watermark de 5 minutes. Ils ne sont pas
perdus mais isolés, comptés et rejouables (voir [architecture données §5](../architecture/02-data-architecture.md)).

---

## 5. Zone gold — datasets métier

### `gold.network_kpis_hourly`
**Grain** : `tenant_id` × `cell_id` × `kpi_hour` · **Source** : `silver.network_metrics`, `silver.cdr`

| Colonne | Type | Description |
|---|---|---|
| `tenant_id`, `cell_id`, `kpi_hour` | — | 🔑 Clé composite |
| `site_id`, `region_code`, `technology` | varchar | Dimensions enrichies |
| `traffic_mb` | double | Volume total sur l'heure |
| `active_users_avg`, `active_users_peak` | int | Charge moyenne et maximale |
| `latency_ms_avg`, `latency_ms_p95` | double | Latence |
| `packet_loss_pct_avg` | double | Perte de paquets |
| `drop_rate_pct` | double | Taux d'appels interrompus |
| `call_setup_failure_pct` | double | Taux d'échec d'établissement |
| `throughput_mbps_avg` | double | Débit utile moyen |
| `prb_utilization_pct_avg` | double | Occupation des ressources radio |
| `availability_pct` | double | Disponibilité de la cellule |
| **`capacity_utilization_pct`** | double | `traffic_mb / capacity_mb_per_hour × 100` — **indicateur de saturation** |
| `total_calls`, `dropped_calls`, `failed_calls` | bigint | Volumétrie d'appels |
| `sample_count` | int | Nombre de mesures agrégées (fiabilité de l'agrégat) |

### `gold.cell_performance`
**Grain** : `tenant_id` × `cell_id` × `perf_date`

Agrégats journaliers des KPI, plus :

| Colonne | Type | Description |
|---|---|---|
| `alarm_count_critical/major/minor` | int | Comptage d'alarmes par sévérité |
| `alarm_duration_minutes` | int | Durée cumulée en alarme |
| **`health_score`** | double | Score composite [0,100] pondérant disponibilité, drop rate, latence, perte et alarmes |
| `health_status` | varchar | `healthy`, `warning`, `critical` — alimente le NOC Dashboard |
| `regional_rank` | int | Rang de la cellule dans sa région |
| `degradation_vs_7d_pct` | double | Évolution du score sur 7 jours — détecte les dégradations lentes |

### `gold.equipment_health`
**Grain** : `tenant_id` × `equipment_id` × `health_date`

| Colonne | Type | Description |
|---|---|---|
| `equipment_type`, `vendor`, `model`, `site_id` | varchar | Dimensions |
| `age_days`, `days_since_maintenance` | int | Ancienneté et suivi de maintenance |
| `temperature_celsius_avg/max` | double | Thermique |
| `cpu_utilization_pct_avg/max` | double | Charge |
| `uptime_seconds`, `restart_count_7d` | — | Stabilité — les redémarrages inattendus sont un signal fort |
| `crc_error_count_delta` | bigint | **Croissance** des erreurs CRC — annonce une dégradation de lien |
| `link_utilization_pct_avg/max` | double | Saturation du transport |
| `alarm_count_30d` | int | Historique d'alarmes |
| `failure_history_count` | int | Pannes passées |

### `gold.customer_360`
**Grain** : `tenant_id` × `subscriber_token`

| Colonne | Type | Description |
|---|---|---|
| `subscriber_token` | varchar | 🔑 Identifiant tokenisé — **jamais de MSISDN** |
| `tenure_days` | int | Ancienneté |
| `region_code`, `primary_cell_id` | varchar | Rattachement géographique |
| `arpu_30d`, `arpu_90d` | decimal | Revenu moyen |
| `voice_minutes_30d`, `data_mb_30d`, `sms_count_30d` | — | Usage par service |
| `recharge_count_30d`, `recharge_amount_30d` | — | Comportement de rechargement |
| `avg_days_between_recharges` | double | Régularité — prédicteur de churn |
| `bundle_purchase_count_30d`, `bundle_utilization_pct` | — | Rapport aux forfaits |
| `momo_transaction_count_30d`, `momo_volume_30d` | — | Activité Mobile Money |
| `complaint_count_90d`, `unresolved_complaint_count` | int | Réclamations |
| **`avg_experienced_drop_rate_pct`** | double | Qualité **vécue** — pondérée par les cellules réellement utilisées |
| **`avg_experienced_throughput_mbps`** | double | Débit réellement obtenu |
| `device_changes_90d`, `sim_swaps_90d` | int | Signaux de fraude et de churn |
| `segment` | varchar | `high_value`, `standard`, `low_usage`, `dormant` |

> 🎯 `avg_experienced_drop_rate_pct` est la colonne la plus intéressante de cette table : elle
> ne mesure pas la qualité *du réseau*, mais la qualité **que cet abonné a effectivement subie**,
> pondérée par les cellules qu'il utilise réellement. C'est ce qui permet de relier
> quantitativement une dégradation technique à un churn individuel — le cœur de l'objectif O4.

### `gold.churn_features`
**Grain** : `tenant_id` × `subscriber_token` × `feature_date`

Features à fenêtres glissantes, **strictement antérieures à la date de prédiction** (FR-M5-009) :
tendances d'usage (7/30/90 j), évolution de l'ARPU, régularité des recharges, taux d'utilisation
des forfaits, qualité vécue et sa dégradation, réclamations, interactions support.

| Colonne cible | Type | Description |
|---|---|---|
| `churned_within_30d` | boolean | 🎯 Variable cible — dérivée de `customer.events` (`CHURN`, `PORT_OUT_REQUEST`) |

### `gold.fraud_features`
**Grain** : `tenant_id` × `transaction_id`

| Famille | Colonnes |
|---|---|
| **Vélocité** | `txn_count_1h/24h/7d`, `amount_sum_1h/24h`, `distinct_receivers_24h` |
| **Écart au comportement** | `amount_vs_avg_ratio`, `amount_zscore_90d`, `is_amount_outlier` |
| **Géographie** | `region_changed`, `distance_from_usual_km`, `impossible_travel_flag` |
| **Appareil** | `device_changed_recently`, `days_since_device_change`, `sim_swap_recent` |
| **Réseau de contreparties** | `receiver_is_new`, `receiver_txn_count_24h`, `is_circular_pattern` |
| **Contexte** | `is_night_transaction`, `is_cross_border`, `channel_changed` |

> `impossible_travel_flag` — deux transactions depuis des régions dont la distance est
> incompatible avec l'intervalle temporel. Signal classique et très discriminant de prise de
> contrôle de compte.

### `gold.traffic_features`
**Grain** : `tenant_id` × `cell_id` × `feature_hour`

Features de la prévision : décalages (1 h, 24 h, 168 h), moyennes glissantes, tendance, variables
calendaires (heure, jour, jour férié, ramadan, événements), capacité, utilisateurs actifs,
technologie, région.

| Colonne cible | Description |
|---|---|
| `traffic_mb_next_1h` … `traffic_mb_next_24h` | 🎯 Cibles multi-horizon |

### `gold.revenue_metrics`
**Grain** : `tenant_id` × `region_code` × `revenue_date` × `product_category`

Revenus par service (voix, data, SMS, Mobile Money, forfaits), volumes, abonnés actifs, ARPU,
activations, taux de conversion, et — colonne clé de l'analyse causale — `network_availability_pct`
de la région, qui permet de corréler baisse de revenu et dégradation réseau (UC-07).

---

## 6. Tables opérationnelles (PostgreSQL)

| Table | Rôle |
|---|---|
| `ops.incidents` | Incidents créés par les agents ou les opérateurs |
| `ops.maintenance_tickets` | Interventions planifiées |
| `ops.approvals` | Demandes d'approbation HITL (état, approbateur, expiration) |
| `ops.audit_log` | Journal immuable (NFR-SEC-006) |
| `ops.agent_executions` | Historique des exécutions LangGraph (checkpointer) |
| `ops.agent_memory` | Mémoire long terme des agents |
| `vault.subscriber_identity` | 🔒 Correspondance token → identité chiffrée — accès très restreint |

---

## 7. Vues d'exposition pour les agents (`marts`)

Les tools n'interrogent **jamais** les tables gold directement : ils passent par des vues
d'exposition qui appliquent la row-level security, masquent les colonnes sensibles et
garantissent un contrat stable.

| Vue | Tool consommateur |
|---|---|
| `marts.agent_network_metrics` | `query_network_metrics` |
| `marts.agent_cell_status` | `get_cell_status` |
| `marts.agent_alarms` | `get_network_alarms` |
| `marts.agent_equipment` | `get_equipment_status` |
| `marts.agent_customer` | `get_customer_profile` |
| `marts.agent_revenue` | `get_revenue_metrics` |

> Cette indirection permet de faire évoluer le modèle gold sans casser les tools — et de
> garantir qu'aucun tool ne peut, par construction, atteindre une colonne non prévue.
