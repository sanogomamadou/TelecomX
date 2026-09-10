# Architecture système

| Champ | Valeur |
|---|---|
| **Version** | 1.0 · 2026-09-10 |
| **Statut** | ✅ Baseline Phase 0 |
| **Modèle de description** | C4 (Contexte → Conteneurs → Composants) |
| **Documents liés** | [PRD](../product/PRD.md) · [SRS](../product/SRS.md) · [ADR](../adr/README.md) · [Data](02-data-architecture.md) · [Sécurité](03-security-architecture.md) · [IA](04-ai-architecture.md) |

---

## 1. Principes directeurs

Sept principes structurent l'ensemble des décisions. Ils sont cités en référence dans les ADR.

| # | Principe | Conséquence concrète |
|---|---|---|
| **P1** | **Event-driven d'abord** | Toute donnée entre par Kafka. Aucun composant n'appelle directement un système source. Le découplage temporel est garanti par construction. |
| **P2** | **La donnée brute est immuable** | Bronze n'est jamais modifié. Toute correction produit une nouvelle version en aval. Le rejeu est toujours possible. |
| **P3** | **Les contrats précèdent le code** | Un flux sans contrat Avro ne peut pas être produit. Le schéma est revu avant l'implémentation. ([ADR-015](../adr/ADR-015-data-contracts.md)) |
| **P4** | **Le LLM ne détient jamais la donnée** | Un agent ne répond jamais de mémoire. Toute affirmation chiffrée provient d'un appel de tool horodaté et cité. ([ADR-008](../adr/ADR-008-agent-orchestration.md)) |
| **P5** | **L'autonomie est graduée, jamais totale** | Chaque action porte une classe de risque. L3+ exige une approbation humaine, quelle que soit la confiance du modèle. ([PRD §7](../product/PRD.md)) |
| **P6** | **Sécurité en profondeur** | Aucune garantie ne repose sur une seule couche. L'autorisation est appliquée au jeton, à l'API, au catalogue de tools et au moteur SQL. ([ADR-012](../adr/ADR-012-identity-management.md)) |
| **P7** | **Observable par conception** | L'instrumentation est écrite avec le code, pas après. Un composant non instrumenté est incomplet. ([ADR-017](../adr/ADR-017-observability-stack.md)) |

---

## 2. Niveau 1 — Contexte système

```
        ┌───────────────────────────── UTILISATEURS ─────────────────────────────┐
        │  NOC · Ingénieurs réseau · Analystes data/fraude · Marketing            │
        │  Terrain · Direction · ML Engineers · Administrateurs                   │
        └──────────────────────────────────┬─────────────────────────────────────┘
                                           │ HTTPS / OIDC
                                           ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │                                                                              │
   │            TELECOMX INTELLIGENCE & AUTONOMOUS OPERATIONS PLATFORM            │
   │                                                                              │
   │     Observe  →  Understand  →  Predict  →  Decide  →  Act  →  Verify         │
   │                                                                              │
   └───────▲──────────────────────────────────────────────────────────┬───────────┘
           │ événements (Kafka)                       actions (API)   │
           │                                                          ▼
   ┌───────┴──────────────────────────┐          ┌────────────────────────────────┐
   │   SYSTÈMES SOURCES (simulés)     │          │   SYSTÈMES CIBLES (simulés)    │
   │  EMS Radio · OSS · CRM · Billing │          │  Ticketing · EMS (diagnostic)  │
   │  Mobile Money · USSD/SMS · IoT   │          │  Notification · Reporting      │
   └──────────────────────────────────┘          └────────────────────────────────┘
                                           │
                                           ▼
                              ┌──────────────────────────┐
                              │  SERVICES EXTERNES       │
                              │  Fournisseur LLM (Groq)  │
                              └──────────────────────────┘
```

> ⚠️ **Systèmes sources et cibles sont simulés** par les générateurs et des services factices
> (`data-platform/generators/`, `apps/api/mocks/`). La plateforme réelle s'y connecterait par des
> adaptateurs isolés dans le module M1, qui est le seul point à modifier pour un branchement réel.

---

## 3. Niveau 2 — Conteneurs

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ COUCHE PRÉSENTATION                                                                  │
│  ┌────────────────────────────────┐  ┌──────────────────────────────────┐            │
│  │ Frontend Next.js / TypeScript  │  │ Grafana                          │            │
│  │ NOC Dashboard · AI Command     │  │ Dashboards techniques            │            │
│  │ Center · Approbations          │  │                                  │            │
│  └───────────────┬────────────────┘  └──────────────────────────────────┘            │
└──────────────────┼───────────────────────────────────────────────────────────────────┘
                   │ REST + SSE, JWT
┌──────────────────┼───────────────────────────────────────────────────────────────────┐
│ COUCHE APPLICATIVE                                                                    │
│  ┌───────────────▼────────────────┐  ┌──────────────────────────────────┐            │
│  │ API FastAPI                    │  │ Keycloak                          │            │
│  │ REST · OpenAPI · RBAC · Audit  │◄─┤ OIDC · Realms · Rôles             │            │
│  └───────┬────────────────┬───────┘  └──────────────────────────────────┘            │
└──────────┼────────────────┼──────────────────────────────────────────────────────────┘
           │                │
┌──────────┼────────────────┼──────────────────────────────────────────────────────────┐
│ COUCHE INTELLIGENCE       │                                                           │
│  ┌───────▼────────────────▼───────────────────────────────────┐                      │
│  │ Agent Service (LangGraph)                                   │                      │
│  │  Supervisor · Network · Forecast · Maintenance              │                      │
│  │  Customer · Fraud · Revenue · Operations                    │                      │
│  └──┬──────────────┬──────────────┬──────────────┬────────────┘                      │
│     │ tools SQL    │ tools ML     │ tools RAG    │ tools action                       │
│     │              │              │              │                                    │
│  ┌──▼───────┐  ┌───▼─────────┐  ┌─▼──────────┐  │  ┌────────────────────────────┐   │
│  │ (Trino)  │  │ ML Serving  │  │ Qdrant     │  │  │ LLM Gateway                │   │
│  │          │  │ 5 modèles   │  │ RAG        │  │  │ routage · cache · fallback │   │
│  └──────────┘  └───┬─────────┘  └────────────┘  │  └─────────────┬──────────────┘   │
│                    │                             │                │ Groq / fallback  │
│                ┌───▼─────────┐                   │                ▼                  │
│                │ MLflow      │                   └──────► Systèmes cibles (actions)  │
│                │ Registry    │                                                        │
│                └─────────────┘                                                        │
└──────────────────────────────────────────────────────────────────────────────────────┘
           │
┌──────────┼──────────────────────────────────────────────────────────────────────────┐
│ COUCHE DONNÉES                                                                       │
│  ┌───────▼──────────┐   ┌──────────────────┐   ┌────────────────────────────────┐   │
│  │ Trino            │   │ Spark            │   │ Kafka + Schema Registry        │   │
│  │ SQL interactif   │   │ Streaming+batch  │◄──┤ 10 topics + DLQ                │   │
│  │ + dbt            │   │ bronze→silver    │   │                                │   │
│  └───────┬──────────┘   └────────┬─────────┘   └────────────▲───────────────────┘   │
│          │                       │                          │                        │
│          └───────────┬───────────┘                          │                        │
│              ┌───────▼────────────────────┐     ┌────────────┴──────────────────┐    │
│              │ Iceberg (catalogue REST)   │     │ Générateurs / Adaptateurs     │    │
│              │ bronze · silver · gold     │     │ 9 systèmes sources simulés    │    │
│              └───────┬────────────────────┘     └───────────────────────────────┘    │
│              ┌───────▼────────────────────┐     ┌───────────────────────────────┐    │
│              │ MinIO (S3)                 │     │ PostgreSQL                    │    │
│              │ lake · warehouse · backup  │     │ ops · audit · checkpoints     │    │
│              └────────────────────────────┘     │ catalogue · vault PII         │    │
│                                                 └───────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────────┘
           │
┌──────────┼──────────────────────────────────────────────────────────────────────────┐
│ COUCHE OBSERVABILITÉ (transverse)                                                    │
│  OTel Collector → Prometheus · Loki · Tempo → Grafana     │     Langfuse (LLM)       │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Inventaire des conteneurs

| Conteneur | Technologie | Responsabilité | Profil | ADR |
|---|---|---|---|---|
| Frontend | Next.js 15, TypeScript | NOC Dashboard, AI Command Center, approbations | `app` | — |
| API | FastAPI, Python | REST, authz, audit, orchestration des appels | `app` | — |
| Agent Service | LangGraph, Python | Orchestration multi-agent, tools, HITL | `agents` | [008](../adr/ADR-008-agent-orchestration.md) |
| LLM Gateway | Python (lib partagée) | Routage, cache, budget, fallback, traçage | `agents` | [009](../adr/ADR-009-llm-provider.md) |
| ML Serving | FastAPI + MLflow | Inférence des 5 modèles | `ml` | — |
| MLflow | MLflow | Suivi d'expériences, registre de modèles | `ml` | — |
| Qdrant | Qdrant | Index vectoriel du RAG | `agents` | [010](../adr/ADR-010-vector-store.md) |
| Trino | Trino | SQL interactif fédéré | `lakehouse`, `demo` | [006](../adr/ADR-006-query-engine.md) |
| Spark | Spark 3.5 | Streaming et batch | `lakehouse` | [005](../adr/ADR-005-stream-processing.md) |
| Iceberg REST | Catalogue REST | Métadonnées de tables | `lakehouse` | [003](../adr/ADR-003-table-format.md) |
| MinIO | MinIO | Stockage objet S3 | `core` | [004](../adr/ADR-004-object-storage.md) |
| Kafka | Kafka KRaft | Bus d'événements | `core` | [002](../adr/ADR-002-event-backbone.md) |
| Schema Registry | Confluent SR | Contrats et compatibilité | `core` | [015](../adr/ADR-015-data-contracts.md) |
| PostgreSQL | PostgreSQL 16 | Ops, audit, checkpoints, vault | `core` | — |
| Redis | Redis | Cache sémantique, limitation de débit | `core` | — |
| Keycloak | Keycloak | Identité et autorisation | `security` | [012](../adr/ADR-012-identity-management.md) |
| Générateurs | Python | Simulation des systèmes sources | `core` | — |
| Anomaly Consumer | Python | Détection temps réel légère | `demo` | [011](../adr/ADR-011-local-dev-strategy.md) |
| Observabilité | OTel, Prom, Loki, Tempo, Grafana, Langfuse | Télémétrie | `observability` | [017](../adr/ADR-017-observability-stack.md) |

---

## 4. Flux de bout en bout

### 4.1 Flux d'ingestion (chemin des données)

```
Générateur ──Avro──► Kafka ──► Spark Streaming ──► Iceberg bronze
                       │                                  │
                       │                          Spark (validation, dédup,
                       │                           tokenisation PII, enrichissement)
                       │                                  ▼
                       │                           Iceberg silver
                       │                                  │
                       │                              dbt (Trino)
                       │                                  ▼
                       │                           Iceberg gold
                       │                                  │
                       └──► Anomaly Consumer ──► ML ──► network.alarms
                                                            │
                                                            ▼
                                                   Operations Agent (événementiel)
```

### 4.2 Flux de requête utilisateur (chemin de l'intelligence)

```
Utilisateur ──► Frontend ──► API ──► [authz] ──► Agent Service
                                                      │
                                          ┌───────────┼───────────┐
                                          ▼           ▼           ▼
                                    LLM Gateway   Tools SQL   Tools ML
                                          │           │           │
                                        Groq       Trino    ML Serving
                                          │           │           │
                                          └───────────┼───────────┘
                                                      ▼
                                             Synthèse + recommandation
                                                      │
                                          action L3+ ?│
                                          ┌───────────┴───────────┐
                                          ▼ oui                   ▼ non
                                   interrupt() + persistance   Exécution
                                          │                       │
                                   Approbation humaine ───────────┘
                                          │
                                    Exécution + vérification + audit
```

**Une seule trace OTel** couvre l'intégralité de ce parcours, y compris la période d'attente
d'approbation (le span reste ouvert), rendant mesurable le temps humain vs le temps machine.

---

## 5. Vues de déploiement

### 5.1 Développement — Docker Compose par profils

Voir [ADR-011](../adr/ADR-011-local-dev-strategy.md) pour le budget mémoire détaillé.

| Profil | RAM | Usage |
|---|---|---|
| `core` | 2,1 GB | Socle commun |
| `lakehouse` | 8,4 GB | Ingestion et transformations |
| `ml` | 2,7 GB | Entraînement et service |
| `agents` | 2,8 GB | Développement des agents |
| `app` | 3,2 GB | API et frontend |
| `demo` | 7,6 GB | Scénario bout-en-bout |

### 5.2 Production cible — Kubernetes

```
                        Ingress (TLS) ──► Keycloak (OIDC)
                              │
        ┌─────────────────────┼─────────────────────┬──────────────────┐
        ▼                     ▼                     ▼                  ▼
   ns: frontend          ns: api              ns: agents          ns: ml
   Next.js (2-4 rép.)    FastAPI (3-8 HPA)    Agent svc (2-6)     Serving (2-4)
        │                     │                     │                  │
        └─────────────────────┴──────────┬──────────┴──────────────────┘
                                          ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  ns: data-platform                                                    │
   │  Kafka (StatefulSet ×3, RF=3) · Schema Registry ×2                    │
   │  Spark (Spark Operator) · Trino (coord + N workers HPA)               │
   │  MinIO (distribué, erasure coding) · PostgreSQL (primaire + réplique) │
   └──────────────────────────────────────────────────────────────────────┘
   ┌──────────────────────────────────────────────────────────────────────┐
   │  ns: observability   OTel · Prometheus · Loki · Tempo · Grafana        │
   └──────────────────────────────────────────────────────────────────────┘

   GitOps : Git ──► ArgoCD ──► Cluster        IaC : Terraform ──► Infrastructure
```

**Différences assumées dev ↔ production**

| Aspect | Développement | Production |
|---|---|---|
| Kafka | 1 broker, RF=1 | 3 brokers, RF=3, `min.insync.replicas=2` |
| MinIO | mono-nœud | distribué, erasure coding |
| PostgreSQL | instance unique | primaire + réplique, sauvegarde continue (PITR) |
| Spark | 1 worker | Spark Operator, autoscaling |
| Trino | 1 worker | N workers avec HPA |
| Secrets | fichier `.env` | External Secrets Operator |
| TLS | désactivé | obligatoire partout |

---

## 6. Attributs de qualité et tactiques

| Attribut | Tactique architecturale | Vérification |
|---|---|---|
| **Performance** | Séparation écriture (Spark) / lecture (Trino) ; partitionnement Iceberg aligné sur les prédicats ; cache sémantique LLM ; routage par complexité de modèle | NFR-PERF-001…010 |
| **Scalabilité** | Partitionnement Kafka dimensionné pour la production ; consommateurs sans état ; HPA sur API/agents/Trino | NFR-SCAL-001…005 |
| **Disponibilité** | Découplage par Kafka ; checkpoints Spark ; fallback LLM en cascade ; circuit breakers ; mode dégradé déterministe | NFR-AVAIL-001…007 |
| **Sécurité** | Défense en profondeur à 5 niveaux ; tokenisation à l'ingestion ; filtrage des tools avant exposition au LLM ; audit immuable | NFR-SEC-001…012 |
| **Évolutivité** | Contrats versionnés + compatibilité vérifiée en CI ; évolution de schéma Iceberg sans réécriture ; adaptateurs de sources isolés | FR-M2-006, FR-M4-002 |
| **Observabilité** | OTel unique, corrélation par `trace_id`, métriques métier définies dès la Phase 1 | NFR-OBS-001…005 |
| **Testabilité** | Logique métier en fonctions pures ; graphe d'agents testable sans LLM ; générateurs déterministes à seed fixée | NFR-MAINT-001 |

---

## 7. Risques architecturaux et parades

| # | Risque | Parade architecturale |
|---|---|---|
| A1 | Le Schema Registry est un point de défaillance de l'ingestion | Cache local de schémas côté clients ; sauvegarde du topic `_schemas` ; runbook de restauration |
| A2 | Le catalogue Iceberg est un point de défaillance de la lecture | PostgreSQL du catalogue inclus au plan DR ; sauvegarde toutes les 15 min (RPO) |
| A3 | Le streaming micro-batch génère des petits fichiers | Compaction planifiée ; taille de fichier cible 128 Mo ; supervision du nombre de fichiers par partition |
| A4 | Un agent génère une requête Trino ruineuse | Aucun SQL libre exposé au LLM : tools paramétrés uniquement ; limites mémoire et timeout par requête |
| A5 | Une boucle d'agent non bornée épuise le budget LLM | `MAX_ITERATIONS` + budget de tokens par exécution + circuit breaker |
| A6 | Fuite inter-tenant par filtre oublié | Pattern `TenantScopedRepository` non contournable + test de fuite en CI |
| A7 | L'indisponibilité du LLM bloque la plateforme | Fallback en cascade + mode dégradé déterministe (les tools restent disponibles sans synthèse) |
| A8 | La télémétrie fait échouer des requêtes métier | Export asynchrone, dégradation silencieuse, jamais bloquant |

---

## 8. Évolutions envisagées (hors périmètre v1)

| Évolution | Déclencheur |
|---|---|
| Passage à Flink pour la détection sub-seconde | Si une exigence de latence < 1 s apparaît |
| Isolation physique par tenant | Contrainte réglementaire de séparation |
| Feature store dédié (Feast) | Si le nombre de modèles dépasse ~10 |
| Fine-tuning d'un modèle sur le corpus télécom | Si le RAG plafonne en justesse |
| Streaming vers un moteur temps réel (Pinot/Druid) | Si les dashboards exigent une latence sub-seconde |

---

*Document maintenu avec le code. Toute modification structurante exige un ADR préalable.*
