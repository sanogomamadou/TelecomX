# SRS — TelecomX Intelligence & Autonomous Operations Platform

| Champ | Valeur |
|---|---|
| **Document** | Software Requirements Specification (SRS) |
| **Version** | 1.0 |
| **Statut** | ✅ Approuvé (baseline Phase 0) |
| **Date** | 2026-09-10 |
| **Auteur** | Mamadou Sanogo |
| **Norme de référence** | ISO/IEC/IEEE 29148:2018 (adaptée) |
| **Documents liés** | [PRD](PRD.md) · [Architecture](../architecture/01-system-architecture.md) · [ADR](../adr/README.md) · [Data Contracts](../data/contracts/README.md) |

---

## 1. Introduction

### 1.1 Objet

Ce document spécifie de manière vérifiable les exigences fonctionnelles (FR) et non fonctionnelles
(NFR) de la plateforme définie dans le [PRD](PRD.md). Il constitue le **référentiel de vérité**
pour la conception, l'implémentation et la recette de chaque phase.

### 1.2 Convention d'identification

```
FR-<MODULE>-<NNN>     Exigence fonctionnelle    ex. FR-M6-004
NFR-<CATEGORIE>-<NNN> Exigence non fonctionnelle ex. NFR-PERF-002
```

| Priorité | Signification | Règle |
|---|---|---|
| **P0** | Critique | La phase ne peut être clôturée sans elle |
| **P1** | Majeure | Requise pour la complétude fonctionnelle |
| **P2** | Souhaitable | Peut être différée avec justification écrite |

Chaque exigence porte un **critère de vérification** : `Test` (automatisé), `Démo` (scénario manuel
reproductible), `Inspection` (revue de code/config), `Analyse` (mesure ou calcul).

### 1.3 Définitions

| Terme | Définition |
|---|---|
| **CDR** | Call Detail Record — enregistrement de métadonnées d'appel |
| **xDR** | Généralisation du CDR à tout type de session (data, SMS, USSD) |
| **KPI réseau** | Indicateur agrégé de qualité (drop rate, latence, débit, taux de perte) |
| **Cellule** | Secteur radio d'un site, plus petite unité de couverture |
| **Backhaul** | Lien de transport reliant un site au cœur de réseau |
| **MSISDN** | Numéro d'abonné mobile (donnée personnelle directe) |
| **Tool** | Fonction typée et versionnée appelable par un agent IA |
| **HITL** | Human-in-the-loop — interruption pour validation humaine |
| **Zone bronze/silver/gold** | Niveaux de raffinement du Lakehouse (brut / nettoyé / métier) |
| **Drift** | Dérive statistique entre données d'entraînement et de production |

---

## 2. Description générale

### 2.1 Contexte système

```
┌──────────────────────────────────────────────────────────────────────┐
│                        SYSTÈMES EXTERNES (simulés)                   │
│  EMS Radio · OSS · CRM · Billing · Mobile Money · Ticketing · IdP     │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ événements / API
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│              TELECOMX INTELLIGENCE PLATFORM (périmètre)              │
│   M1 Ingestion · M2 Lakehouse · M3 Analytics · M4 Governance         │
│   M5 ML · M6 Agents · M7 Application · M8 Infra · M9 Obs · M10 SRE   │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ actions / rapports
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    UTILISATEURS  (U1 … U9, cf. PRD §4)               │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 Matrice des rôles et permissions (RBAC)

Référence normative pour Keycloak, l'API et l'allowlist de tools des agents.

| Rôle | Données réseau | Données client | Données financières | PII détokenisée | Tools L1/L2 | Tools L3 | Tools L4 |
|---|---|---|---|---|---|---|---|
| `noc_operator` | ✅ RW | ❌ | ❌ | ❌ | ✅ | ✅ approuve | ❌ |
| `network_engineer` | ✅ RW | 🔸 agrégé | ❌ | ❌ | ✅ | ✅ approuve | 🔸 propose |
| `data_analyst` | ✅ R | ✅ R | ✅ R | ❌ | ✅ lecture | ❌ | ❌ |
| `fraud_analyst` | 🔸 agrégé | ✅ R | ✅ RW | ✅ **avec audit** | ✅ | ✅ approuve | ✅ approuve |
| `marketing_manager` | 🔸 agrégé | ✅ R | 🔸 agrégé | ❌ | ✅ lecture | ❌ | ❌ |
| `field_engineer` | ✅ R | ❌ | ❌ | ❌ | ✅ lecture | ❌ | ❌ |
| `executive` | 🔸 agrégé | 🔸 agrégé | 🔸 agrégé | ❌ | ✅ lecture | ❌ | ❌ |
| `ml_engineer` | ✅ R | ✅ R | ✅ R | ❌ | ✅ lecture | ❌ | ❌ |
| `platform_admin` | ✅ RW | ✅ RW | ✅ RW | 🔸 **break-glass** | ✅ | ✅ | ✅ |

*✅ accès complet · 🔸 accès restreint/agrégé · ❌ refus (HTTP 403 + audit)*

> 🔐 **Règle transverse NFR-SEC-004** : un marketing_manager ne peut en aucun cas invoquer
> `restart_network_service()`. L'allowlist est appliquée **à trois niveaux** : token Keycloak,
> middleware API, et filtrage du catalogue de tools transmis au LLM (un tool non autorisé
> n'est même pas *visible* par le modèle).

---

## 3. Exigences fonctionnelles

### 3.1 M1 — Data Ingestion

| ID | Exigence | Prio | Vérification |
|---|---|---|---|
| FR-M1-001 | Le système génère des événements synthétiques pour les 9 flux du PRD §2.2, avec schémas conformes aux data contracts. | P0 | Test |
| FR-M1-002 | Les générateurs reproduisent une **saisonnalité réaliste** : cycle journalier (creux 03h-05h, pics 12h-14h et 19h-22h), hebdomadaire, et effets d'événements exceptionnels. | P0 | Analyse |
| FR-M1-003 | Les générateurs supportent un `SPEED_FACTOR` réglable préservant les proportions statistiques. | P0 | Test |
| FR-M1-004 | Les générateurs peuvent **injecter des scénarios d'anomalie** paramétrables (dégradation cellule, pic de fraude, vague de churn, panne d'équipement). | P0 | Démo |
| FR-M1-005 | Aucun MSISDN en clair n'est produit : les identifiants d'abonné sont des pseudonymes synthétiques stables. | P0 | Test |
| FR-M1-006 | Chaque événement publié porte : `event_id` (unique), `event_timestamp`, `ingestion_timestamp`, `schema_version`, `producer_id`, `tenant_id`. | P0 | Test |
| FR-M1-007 | Les producteurs Kafka garantissent une sémantique **au-moins-une-fois** avec clé de partition déterministe (`cell_id` pour le réseau, `subscriber_token` pour le client). | P0 | Test |
| FR-M1-008 | Un message non conforme à son schéma est routé vers la **DLQ** `<topic>.dlq` avec le motif de rejet, sans bloquer le flux. | P0 | Test |
| FR-M1-009 | Les générateurs sont **déterministes à seed fixée** (reproductibilité des démos et des tests). | P1 | Test |
| FR-M1-010 | Le système simule des **désordres temporels** (événements en retard jusqu'à 5 min) pour valider le watermarking. | P1 | Test |

### 3.2 M2 — Streaming & Lakehouse

| ID | Exigence | Prio | Vérification |
|---|---|---|---|
| FR-M2-001 | Spark Structured Streaming consomme les topics Kafka et écrit en zone **bronze** au format Iceberg, sans transformation métier. | P0 | Test |
| FR-M2-002 | La zone bronze conserve la charge utile brute + métadonnées techniques (offset, partition, timestamp Kafka). | P0 | Inspection |
| FR-M2-003 | Le passage bronze → **silver** applique : validation de schéma, déduplication sur `event_id`, normalisation des types et unités, tokenisation PII, enrichissement (référentiel cellule/site/région). | P0 | Test |
| FR-M2-004 | La déduplication opère sur une fenêtre de **24 h** avec état borné. | P0 | Test |
| FR-M2-005 | Les tables Iceberg sont **partitionnées** selon un schéma documenté (typiquement `days(event_timestamp)` + `region` ou `bucket(cell_id)`). | P0 | Inspection |
| FR-M2-006 | Le système supporte l'**évolution de schéma** (ajout de colonne, élargissement de type) sans réécriture ni interruption. | P0 | Test |
| FR-M2-007 | Les jobs streaming redémarrent depuis leur **checkpoint** sans perte ni duplication (exactly-once en écriture Iceberg). | P0 | Test chaos |
| FR-M2-008 | La couche **gold** matérialise au minimum : `network_kpis`, `cell_performance`, `customer_360`, `churn_features`, `fraud_features`, `traffic_features`, `revenue_metrics`, `equipment_health`. | P0 | Test |
| FR-M2-009 | Une politique de **maintenance des tables** est planifiée : compaction, expiration de snapshots, suppression des fichiers orphelins. | P1 | Démo |
| FR-M2-010 | Le time-travel Iceberg permet de rejouer l'état d'une table à un instant passé. | P1 | Démo |
| FR-M2-011 | Le retraitement d'une journée (backfill) est possible sans dupliquer les données existantes (idempotence par `MERGE`). | P1 | Test |

### 3.3 M3 — Analytics

| ID | Exigence | Prio | Vérification |
|---|---|---|---|
| FR-M3-001 | Trino expose l'ensemble des tables Iceberg (bronze/silver/gold) via un catalogue unique. | P0 | Test |
| FR-M3-002 | dbt matérialise les modèles silver et gold avec tests, documentation et lineage générés. | P0 | Test |
| FR-M3-003 | Les KPI réseau sont calculés par cellule, site, région et technologie, aux granularités 5 min / 1 h / 1 j. | P0 | Test |
| FR-M3-004 | `customer_360` agrège par abonné : usage, recharges, bundles, réclamations, qualité réseau vécue, ARPU, ancienneté. | P0 | Test |
| FR-M3-005 | Les requêtes analytiques appliquent la **row-level security** selon le rôle appelant. | P1 | Test |
| FR-M3-006 | Un dictionnaire de données est généré automatiquement depuis les métadonnées dbt. | P1 | Inspection |

### 3.4 M4 — Data Quality & Governance

| ID | Exigence | Prio | Vérification |
|---|---|---|---|
| FR-M4-001 | Chaque flux possède un **data contract** versionné (Avro + JSON Schema) enregistré au Schema Registry. | P0 | Test |
| FR-M4-002 | La compatibilité de schéma est vérifiée en CI : toute évolution incompatible échoue le build. | P0 | Test CI |
| FR-M4-003 | Des règles de qualité **techniques** sont appliquées : non-nullité, bornes (`0 ≤ drop_rate ≤ 100`), types, unicité, formats. | P0 | Test |
| FR-M4-004 | Des règles de qualité **métier** sont appliquées : `active_users ≤ cell_capacity`, `call_duration ≥ 0`, `download + upload = total_volume`, cohérence référentielle cellule/site. | P0 | Test |
| FR-M4-005 | Les violations sont quantifiées, historisées et exposées en métriques Prometheus. | P0 | Test |
| FR-M4-006 | Un **seuil de qualité bloquant** empêche la publication en gold si le taux de conformité est inférieur au seuil du contrat. | P0 | Test |
| FR-M4-007 | Le lineage de bout en bout (source → bronze → silver → gold → modèle ML → agent) est consultable. | P1 | Démo |
| FR-M4-008 | Un catalogue de données recense tables, colonnes, propriétaires, classification de sensibilité et fraîcheur. | P1 | Démo |
| FR-M4-009 | Les colonnes PII sont **classifiées et étiquetées** dans le catalogue. | P0 | Inspection |
| FR-M4-010 | La fraîcheur de chaque table gold est mesurée et alertée au-delà du SLO. | P1 | Test |

### 3.5 M5 — ML Platform

| ID | Exigence | Prio | Vérification |
|---|---|---|---|
| FR-M5-001 | **Traffic Forecasting** : prédit le volume data par cellule à horizon 1–24 h, pas horaire. | P0 | Test |
| FR-M5-002 | **Anomaly Detection** : score en temps réel les métriques réseau (multivarié), avec seuil calibré. | P0 | Test |
| FR-M5-003 | **Churn Prediction** : probabilité de résiliation à 30 jours par abonné. | P1 | Test |
| FR-M5-004 | **Fraud Detection** : score de risque par transaction Mobile Money, en < 5 min après l'événement. | P0 | Test |
| FR-M5-005 | **Predictive Maintenance** : probabilité de panne d'équipement et fenêtre estimée (3–7 j, 7–30 j). | P1 | Test |
| FR-M5-006 | Toute expérimentation est tracée dans MLflow : paramètres, métriques, artefacts, jeu de données, version de code. | P0 | Inspection |
| FR-M5-007 | Les modèles promus passent par un **Model Registry** avec étapes `Staging` → `Production` et approbation explicite. | P0 | Démo |
| FR-M5-008 | Les features d'entraînement et d'inférence proviennent de la **même définition** (pas de train/serving skew). | P0 | Test |
| FR-M5-009 | La validation temporelle utilise un découpage chronologique (jamais de fuite du futur). | P0 | Inspection |
| FR-M5-010 | Chaque modèle expose une **explicabilité** (importance de features, contributions SHAP pour les scores individuels). | P1 | Test |
| FR-M5-011 | Le service d'inférence expose une API REST avec contrat typé, health check et métriques. | P0 | Test |
| FR-M5-012 | Le **drift** des features et des prédictions est mesuré (PSI, KS) et alerté. | P1 | Test |
| FR-M5-013 | Un pipeline de réentraînement est déclenchable manuellement et sur seuil de drift. | P2 | Démo |
| FR-M5-014 | Chaque modèle possède une **model card** documentant données, performances, limites et biais. | P1 | Inspection |

### 3.6 M6 — Agent Platform

| ID | Exigence | Prio | Vérification |
|---|---|---|---|
| FR-M6-001 | Le système implémente 8 agents : Supervisor, Network, Forecast, Maintenance, Customer, Fraud, Revenue, Operations. | P0 | Test |
| FR-M6-002 | L'orchestration est réalisée par **LangGraph** avec état typé et persistance des exécutions. | P0 | Inspection |
| FR-M6-003 | Le Supervisor classifie la requête, planifie, délègue aux agents spécialisés et synthétise. | P0 | Test |
| FR-M6-004 | Les agents n'accèdent aux données **que par des tools typés** ; toute réponse chiffrée cite le tool et l'horodatage de la donnée. | P0 | Test |
| FR-M6-005 | Le catalogue de tools couvre au minimum les 15 fonctions du PRD, chacune portant une **classe de risque** déclarative. | P0 | Inspection |
| FR-M6-006 | Un tool de classe L3+ **interrompt** le graphe et attend une approbation humaine explicite avant exécution. | P0 | Test |
| FR-M6-007 | Le catalogue de tools présenté au LLM est **filtré par les permissions** de l'utilisateur appelant. | P0 | Test |
| FR-M6-008 | Le RAG indexe le corpus documentaire (SOP, procédures, manuels, catalogue produit, politiques) et retourne des extraits **sourcés**. | P0 | Test |
| FR-M6-009 | Les agents disposent d'une **mémoire** : court terme (conversation) et long terme (incidents résolus, décisions passées). | P1 | Test |
| FR-M6-010 | Un échec de tool est géré : retry avec backoff, dégradation explicite, et jamais d'invention de résultat. | P0 | Test chaos |
| FR-M6-011 | Une indisponibilité du LLM primaire bascule sur un **fallback** sans interrompre le service. | P0 | Test chaos |
| FR-M6-012 | Des **garde-fous** filtrent entrées (injection de prompt) et sorties (PII, actions hors périmètre). | P0 | Test |
| FR-M6-013 | Chaque exécution d'agent est tracée dans Langfuse : prompts, appels de tools, tokens, coût, latence, trajectoire. | P0 | Démo |
| FR-M6-014 | Un **jeu d'évaluation** mesure la justesse, l'ancrage factuel, le choix de tools et la sécurité des agents. | P1 | Test |
| FR-M6-015 | Les agents peuvent être déclenchés par **événement** (alarme Kafka) et pas seulement par requête utilisateur. | P0 | Démo |
| FR-M6-016 | Un budget de tokens et de tours de boucle borne chaque exécution (protection contre les boucles infinies). | P0 | Test |
| FR-M6-017 | Toute action exécutée est enregistrée dans un **journal d'audit immuable** : agent, tool, arguments, approbateur, résultat, trace. | P0 | Test |

### 3.7 M7 — Application

| ID | Exigence | Prio | Vérification |
|---|---|---|---|
| FR-M7-001 | L'API expose les ressources `/api/v1/{network,customers,fraud,forecast,churn,agents,incidents,operations}` documentées en OpenAPI 3.1. | P0 | Test |
| FR-M7-002 | L'authentification repose sur des **JWT émis par Keycloak** (OIDC), avec vérification de signature et d'audience. | P0 | Test |
| FR-M7-003 | Chaque endpoint applique les permissions de la matrice RBAC §2.2. | P0 | Test |
| FR-M7-004 | L'API applique une **limitation de débit** par utilisateur et par rôle. | P1 | Test |
| FR-M7-005 | Toute requête mutante est **auditée** (qui, quoi, quand, depuis où, résultat). | P0 | Test |
| FR-M7-006 | Le frontend fournit un **NOC Dashboard** : état global, carte géographique région→site→cellule, alarmes, incidents actifs. | P0 | Démo |
| FR-M7-007 | Le frontend fournit un **AI Command Center** : saisie en langage naturel, affichage **en streaming** du raisonnement, des tools appelés et des sources. | P0 | Démo |
| FR-M7-008 | Le frontend fournit une **interface d'approbation** présentant l'action proposée, sa justification, les données invoquées et son niveau de risque. | P0 | Démo |
| FR-M7-009 | Les mises à jour temps réel utilisent SSE ou WebSocket (pas de polling agressif). | P1 | Inspection |
| FR-M7-010 | L'interface est responsive et respecte l'accessibilité WCAG 2.1 niveau AA sur les parcours critiques. | P1 | Test |
| FR-M7-011 | Un mode sombre est disponible (usage NOC 24/7). | P2 | Démo |

### 3.8 M8/M9/M10 — Infrastructure, Observabilité, Fiabilité

| ID | Exigence | Prio | Vérification |
|---|---|---|---|
| FR-M8-001 | L'ensemble de la stack démarre en local via `docker compose` avec des **profils** sélectionnables. | P0 | Démo |
| FR-M8-002 | Chaque service applicatif possède une image Docker multi-stage, non-root, avec health check. | P0 | Inspection |
| FR-M8-003 | Les manifestes Kubernetes / charts Helm couvrent l'ensemble des composants avec requests/limits. | P1 | Inspection |
| FR-M8-004 | Terraform décrit l'infrastructure cible de façon idempotente. | P1 | Test |
| FR-M8-005 | La CI exécute : lint, typage, tests unitaires, intégration, scan de sécurité, build d'images. | P0 | Test CI |
| FR-M8-006 | Aucun secret n'est stocké en clair dans le dépôt ; un scan automatisé le vérifie. | P0 | Test CI |
| FR-M9-001 | Les métriques infra, data, ML et agents sont collectées par Prometheus. | P0 | Démo |
| FR-M9-002 | Six dashboards Grafana existent : Kafka, Spark/Data, API, ML, Agents, Network. | P0 | Démo |
| FR-M9-003 | Les logs sont centralisés (Loki) et **corrélés aux traces** par `trace_id`. | P0 | Démo |
| FR-M9-004 | Une requête utilisateur est traçable de bout en bout (UI → API → agent → tool → Trino/ML → LLM). | P0 | Démo |
| FR-M9-005 | Des règles d'alerte couvrent : consumer lag, fraîcheur, échec de pipeline, drift, taux d'erreur agent, budget LLM. | P1 | Inspection |
| FR-M10-001 | Des tests de charge établissent la capacité et les points de rupture. | P1 | Test |
| FR-M10-002 | Neuf expériences de chaos sont scriptées et documentées avec leur comportement attendu. | P1 | Test |
| FR-M10-003 | Une procédure de sauvegarde/restauration est documentée **et testée** (RPO 15 min, RTO 30 min). | P1 | Démo |

---

## 4. Exigences non fonctionnelles

### 4.1 Performance

| ID | Exigence | Cible | Vérification |
|---|---|---|---|
| NFR-PERF-001 | Latence d'ingestion (production → disponible en bronze), p95 | < 60 s | Analyse |
| NFR-PERF-002 | Latence bronze → silver, p95 | < 3 min | Analyse |
| NFR-PERF-003 | Fraîcheur des tables gold, p95 | < 15 min | Analyse |
| NFR-PERF-004 | Latence des requêtes Trino sur KPI agrégés (30 j), p95 | < 5 s | Test |
| NFR-PERF-005 | Latence d'inférence ML (unitaire), p95 | < 200 ms | Test |
| NFR-PERF-006 | Latence d'un agent simple (1–3 tools), p95 | < 8 s | Test |
| NFR-PERF-007 | Latence du Supervisor multi-agent, p95 | < 20 s | Test |
| NFR-PERF-008 | Latence API (endpoints de lecture), p95 | < 500 ms | Test |
| NFR-PERF-009 | Premier octet de la réponse agent en streaming | < 2 s | Test |
| NFR-PERF-010 | Détection d'anomalie après l'événement source | < 90 s | Démo |

### 4.2 Scalabilité

| ID | Exigence | Cible |
|---|---|---|
| NFR-SCAL-001 | Débit d'ingestion soutenu (dimensionnement documenté) | 20 000 msg/s |
| NFR-SCAL-002 | Volume Lakehouse adressable | 50 To |
| NFR-SCAL-003 | Consommateurs Kafka scalables horizontalement sans reconfiguration | ≥ 12 partitions/topic majeur |
| NFR-SCAL-004 | Agents concurrents | ≥ 50 exécutions simultanées |
| NFR-SCAL-005 | Utilisateurs simultanés sur l'UI | ≥ 200 |

### 4.3 Disponibilité & fiabilité

| ID | Exigence | Cible |
|---|---|---|
| NFR-AVAIL-001 | Disponibilité API | ≥ 99,5 % |
| NFR-AVAIL-002 | Disponibilité pipeline d'ingestion | ≥ 99,9 % |
| NFR-AVAIL-003 | Perte de données tolérée | **0** (au-moins-une-fois + dédup) |
| NFR-AVAIL-004 | RPO | ≤ 15 min |
| NFR-AVAIL-005 | RTO | ≤ 30 min |
| NFR-AVAIL-006 | Dégradation gracieuse en cas d'indisponibilité LLM | Réponse déterministe issue des données, sans synthèse LLM |
| NFR-AVAIL-007 | Reprise automatique des consommateurs après crash | < 60 s, sans perte |

### 4.4 Sécurité

| ID | Exigence | Vérification |
|---|---|---|
| NFR-SEC-001 | Aucun MSISDN ni identifiant direct en clair au-delà de la couche d'ingestion. | Test |
| NFR-SEC-002 | Tokenisation par HMAC-SHA256 avec clé gérée hors du dépôt et rotative. | Inspection |
| NFR-SEC-003 | Authentification obligatoire sur tout endpoint non public ; refus par défaut. | Test |
| NFR-SEC-004 | Autorisation appliquée à trois niveaux : token, API, catalogue de tools. | Test |
| NFR-SEC-005 | Chiffrement TLS pour tous les flux externes ; chiffrement au repos documenté. | Inspection |
| NFR-SEC-006 | Journal d'audit immuable, horodaté, exportable, pour toute action sensible. | Test |
| NFR-SEC-007 | Aucun secret en clair dans le code, les images ou les manifestes. | Test CI |
| NFR-SEC-008 | Les dépendances sont scannées ; aucune vulnérabilité critique non traitée. | Test CI |
| NFR-SEC-009 | Les entrées utilisateur transmises au LLM sont assainies contre l'injection de prompt. | Test |
| NFR-SEC-010 | Les sorties d'agents sont filtrées contre la fuite de PII et de secrets. | Test |
| NFR-SEC-011 | Un **kill-switch** désactive instantanément toute exécution d'action par les agents. | Démo |
| NFR-SEC-012 | Accès break-glass tracé, limité dans le temps et notifié. | Inspection |

### 4.5 Qualité des données

| ID | Exigence | Cible |
|---|---|---|
| NFR-DQ-001 | Conformité au contrat de schéma | 100 % (non conforme ⇒ DLQ) |
| NFR-DQ-002 | Complétude des champs obligatoires en silver | ≥ 99,9 % |
| NFR-DQ-003 | Taux de doublons résiduels en silver | < 0,01 % |
| NFR-DQ-004 | Intégrité référentielle (cellule → site → région) | 100 % |
| NFR-DQ-005 | Taux de messages en DLQ | < 0,1 % (alerte au-delà) |

### 4.6 Maintenabilité & qualité logicielle

| ID | Exigence | Cible |
|---|---|---|
| NFR-MAINT-001 | Couverture de tests (libs partagées, API, agents) | ≥ 80 % |
| NFR-MAINT-002 | Typage statique Python (`mypy --strict` sur les libs) | 0 erreur |
| NFR-MAINT-003 | Lint et formatage automatisés (`ruff`, `eslint`, `prettier`) | 0 violation |
| NFR-MAINT-004 | Toute décision structurante fait l'objet d'un ADR | Inspection |
| NFR-MAINT-005 | Convention de commits (Conventional Commits) vérifiée en CI | Test CI |
| NFR-MAINT-006 | Chaque service dispose d'un README d'exploitation | Inspection |
| NFR-MAINT-007 | Durée de la CI complète | < 15 min |

### 4.7 Observabilité

| ID | Exigence |
|---|---|
| NFR-OBS-001 | Tout service émet métriques (Prometheus), logs structurés JSON et traces OTel. |
| NFR-OBS-002 | Tout log applicatif contient `trace_id`, `span_id`, `tenant_id` et `user_id` lorsqu'applicables. |
| NFR-OBS-003 | Toute exécution d'agent produit une trace Langfuse complète et rejouable. |
| NFR-OBS-004 | Les métriques de coût LLM sont agrégées par agent, utilisateur et période. |
| NFR-OBS-005 | Les alertes distinguent sévérité et destinataire, et sont documentées par un runbook. |

### 4.8 Contraintes d'environnement

| ID | Exigence |
|---|---|
| NFR-ENV-001 | Le développement local fonctionne sur 16 GB de RAM grâce aux profils Compose. |
| NFR-ENV-002 | Aucun composant ne requiert de service cloud payant pour la démonstration. |
| NFR-ENV-003 | Le projet est utilisable sous Windows, Linux et macOS (scripts PowerShell + Bash). |
| NFR-ENV-004 | Le budget LLM de la démonstration reste dans le free tier du fournisseur. |

---

## 5. Matrice de traçabilité

Chaque cas d'usage du PRD est couvert par des exigences vérifiables :

| Cas d'usage | Exigences fonctionnelles | NFR critiques |
|---|---|---|
| UC-01 Diagnostic dégradation réseau | FR-M6-001/003/004/008, FR-M2-008, FR-M3-003 | PERF-006/007, SEC-009 |
| UC-02 Prévision de trafic | FR-M5-001/008/011, FR-M6-005 | PERF-005 |
| UC-03 Détection d'anomalie temps réel | FR-M5-002, FR-M2-001/007, FR-M1-004 | PERF-010, AVAIL-003 |
| UC-04 Maintenance prédictive | FR-M5-005/010, FR-M6-001 | PERF-005 |
| UC-05 Scoring de churn | FR-M5-003/010, FR-M2-008 | DQ-002 |
| UC-06 Détection de fraude | FR-M5-004, FR-M1-001, FR-M6-005 | PERF-010, SEC-001 |
| UC-07 Explication de variation de revenu | FR-M6-001/003, FR-M3-004 | PERF-007 |
| UC-08 Action avec approbation | FR-M6-006/007/017, FR-M7-008 | SEC-004/006/011 |
| UC-09 Procédure contextualisée (RAG) | FR-M6-008/004 | PERF-006 |
| UC-10 Supervision qualité & drift | FR-M4-005/010, FR-M5-012, FR-M9-001/002 | DQ-001…005 |
| UC-11 Requête libre-service | FR-M3-001/005 | PERF-004, SEC-004 |
| UC-12 Audit des agents | FR-M6-013/017, FR-M7-005 | SEC-006, OBS-003 |

---

## 6. Hypothèses et dépendances

| # | Hypothèse | Risque si invalidée |
|---|---|---|
| H1 | Les données synthétiques sont statistiquement suffisantes pour entraîner des modèles crédibles. | Performances ML non représentatives → documenté comme limite dans les model cards |
| H2 | Le free tier Groq couvre les besoins de démonstration. | Bascule sur fallback (ADR-009) |
| H3 | Docker Desktop dispose d'au moins 8 GB alloués. | Profils Compose réduits (ADR-011) |
| H4 | Les systèmes sources exposeraient en production les mêmes champs que les contrats définis. | Adaptateurs d'ingestion à ajuster — isolés dans M1 par conception |

---

## Historique des révisions

| Version | Date | Auteur | Modification |
|---|---|---|---|
| 1.0 | 2026-09-10 | M. Sanogo | Version initiale — baseline Phase 0 |
