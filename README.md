# 📡 TelecomX — Intelligence & Autonomous Operations Platform

> Plateforme **Data & Agentic AI** pour opérateur télécom : transformer les événements réseau,
> clients et financiers en **insights, prédictions et actions opérationnelles automatisées**.

**Observe → Understand → Predict → Decide → Act → Verify**

[![CI](https://github.com/sanogomamadou/TelecomX/actions/workflows/ci.yml/badge.svg)](https://github.com/sanogomamadou/TelecomX/actions/workflows/ci.yml)
[![Phase](https://img.shields.io/badge/phase-1%20%C2%B7%20Data%20Platform-blue)](docs/product/PRD.md)
[![Contrats](https://img.shields.io/badge/contrats-11%2F11%20valides-success)](docs/data/contracts/README.md)
[![ADR](https://img.shields.io/badge/ADR-18-informational)](docs/adr/README.md)
[![Licence](https://img.shields.io/badge/licence-MIT-lightgrey)](LICENSE)

---

## ⚠️ Données entièrement synthétiques

**TelecomX est un opérateur fictif.** Toutes les données sont générées par les simulateurs du
dépôt. Aucune donnée personnelle réelle n'est utilisée, à aucun stade. Les mécanismes de
protection (tokenisation, RBAC, audit) sont néanmoins réellement implémentés et testés —
voir la [politique PII](docs/security/pii-policy.md).

---

## 🎯 Le problème

TelecomX exploite **4 821 cellules** pour **12,4 millions d'abonnés** et produit
**~214 millions d'événements par jour**. Ces données existent, mais sont exploitées en silos :
le NOC surveille le réseau, le marketing analyse le churn, la fraude examine les transactions —
chacun avec ses propres extractions et sa propre vérité.

Conséquence mesurable : **47 minutes** en moyenne entre une alarme et l'identification de sa
cause racine, parce qu'un ingénieur doit corréler à la main des métriques radio, des alarmes
d'équipement, un historique et des procédures en PDF.

**Cible : moins de 8 minutes**, en unifiant les données et en exposant les capacités d'analyse
à des agents IA capables non seulement d'expliquer, mais de **recommander et d'exécuter** des
actions sous contrôle humain.

---

## 🏗️ Architecture

```
   SOURCES          STREAMING         LAKEHOUSE          ANALYTICS         INTELLIGENCE
   ────────         ─────────         ─────────          ─────────         ────────────
   Réseau                                                                   ML (5 modèles)
   CDR / xDR   ──►   Kafka    ──►   Spark ──► Iceberg  ──►  Trino    ──►   Agents (8)
   CRM/Billing       Schema          bronze   silver         + dbt          RAG · Tools
   Mobile Money      Registry        ────────────────► gold                 LangGraph
   Alarmes / IoT                        SeaweedFS (S3)                          │
                                                                                ▼
                                                                   Human-in-the-loop
                                                                                │
                                                                                ▼
                                                                   Actions + Vérification
```

📖 [Architecture système](docs/architecture/01-system-architecture.md) ·
[Données](docs/architecture/02-data-architecture.md) ·
[Sécurité](docs/architecture/03-security-architecture.md) ·
[IA](docs/architecture/04-ai-architecture.md)

---

## 🧰 Stack

| Domaine | Technologie | ADR |
|---|---|---|
| Streaming | Apache Kafka (KRaft) + Schema Registry | [002](docs/adr/ADR-002-event-backbone.md) · [015](docs/adr/ADR-015-data-contracts.md) |
| Lakehouse | Apache Iceberg + SeaweedFS (S3) | [003](docs/adr/ADR-003-table-format.md) · [004](docs/adr/ADR-004-object-storage.md) · [021](docs/adr/ADR-021-seaweedfs-replaces-minio.md) |
| Traitement | Apache Spark (Structured Streaming) | [005](docs/adr/ADR-005-stream-processing.md) |
| SQL | Trino + dbt | [006](docs/adr/ADR-006-query-engine.md) · [007](docs/adr/ADR-007-transformation-layer.md) |
| ML | LightGBM · XGBoost · MLflow | — |
| Agents | LangGraph + Groq (gateway multi-provider) | [008](docs/adr/ADR-008-agent-orchestration.md) · [009](docs/adr/ADR-009-llm-provider.md) |
| RAG | Qdrant (recherche hybride) | [010](docs/adr/ADR-010-vector-store.md) |
| API / Front | FastAPI · Next.js + TypeScript | — |
| Sécurité | Keycloak (OIDC/RBAC) · tokenisation HMAC | [012](docs/adr/ADR-012-identity-management.md) · [013](docs/adr/ADR-013-pii-tokenization.md) |
| Observabilité | OpenTelemetry · Prometheus · Grafana · Loki · Langfuse | [017](docs/adr/ADR-017-observability-stack.md) |
| Infrastructure | Docker · Kubernetes · Helm · Terraform · ArgoCD | [011](docs/adr/ADR-011-local-dev-strategy.md) |

---

## 🚀 Démarrage

### Prérequis

| Outil | Version | Note |
|---|---|---|
| Docker Desktop | ≥ 24 | **7 GB alloués à WSL2** (`.wslconfig`) — voir [ADR-011](docs/adr/ADR-011-local-dev-strategy.md) |
| Python | ≥ 3.11 | Installé par `uv` si absent |
| Node.js | ≥ 20 | Frontend uniquement (Phase 5) |
| Git | ≥ 2.40 | |

### Installation

```bash
git clone <repo> && cd TelecomX
make setup                 # outils Python, hooks pre-commit, dépendances
make env                   # crée .env et génère les secrets locaux ; renseigner GROQ_API_KEY (Phase 4)
```

### Profils Docker Compose

⚠️ **La stack complète ne tient pas en mémoire** (~14 GB requis pour ~7 GB disponibles).
Les services sont donc regroupés en profils — voir [ADR-011](docs/adr/ADR-011-local-dev-strategy.md).

```bash
make up-core          # Kafka, Schema Registry, SeaweedFS, PostgreSQL, Redis (2,1 GB)
make up-lakehouse     # + Iceberg, Spark, Trino                             (6,2 GB)
make up-ml            # + MLflow, model serving                             (2,7 GB)
make up-agents        # + Qdrant, agent service                             (2,8 GB)
make up-app           # + API, frontend                                     (3,2 GB)
make up-obs           # + Prometheus, Grafana, Loki, Langfuse               (2,5 GB)

make demo             # 🎬 scénario bout-en-bout complet                     (5,5 GB)
make down             # tout arrêter
make ps-mem           # RAM consommée par service
```

---

## 🎬 Le scénario de démonstration

`make demo` reproduit intégralement, sans intervention manuelle :

```
T+0s     Injection d'une dégradation sur CELL-0421
         packet_loss 1,2 % → 14,8 % · latency 32 → 184 ms · drop_rate 0,8 % → 12,6 %
T+45s    Détection d'anomalie (score 0,97) → publication sur network.alarms
T+50s    Operations Agent déclenché par l'événement (aucun prompt humain)
         → métriques · alarmes · état équipement · procédure SOP
T+62s    Cause racine : « congestion backhaul — confiance 91 % »
         Action recommandée : run_backhaul_diagnostic()  [classe L3]
T+65s    ⏸️  Interruption — attente d'approbation humaine
T+2min   ✅ Approbation → diagnostic exécuté → incident INC-2026-009821 créé
T+15min  Vérification : packet_loss 2,1 % · latency 41 ms · drop_rate 1,3 %
         🟢 Incident résolu — rapport post-mortem généré
```

Chaque étape est **tracée** (une seule trace OpenTelemetry de bout en bout) et **auditée**
(qui a approuvé quoi, sur la base de quelles données).

---

## 🗺️ Feuille de route

| Phase | Contenu | Statut |
|---|---|---|
| **0** | Product & Architecture — PRD, SRS, 17 ADR, threat model, contrats, SLO | ✅ **Terminée** |
| **1** | Data Platform — générateurs, Kafka, Spark, Iceberg, Trino, dbt | 🔄 **En cours** — lots 1.1 (bibliothèque socle), 1.2 (génération de code depuis les contrats), 1.3 (référentiels synthétiques), 1.4 (générateurs d'événements) et 1.5 (socle Docker `core`) terminés |
| **2** | Data Quality & Governance — Schema Registry, DQ gates, lineage, PII | ⏳ |
| **3** | ML Platform — 5 modèles, MLflow, serving, drift | ⏳ |
| **4** | Agent Platform — 8 agents, tools, RAG, mémoire, HITL, évaluation | ⏳ |
| **5** | Application — FastAPI, Next.js, NOC Dashboard, AI Command Center | ⏳ |
| **6** | Infrastructure — Docker, K8s, Helm, Terraform, ArgoCD, CI/CD | ⏳ |
| **7** | Observabilité — Prometheus, Grafana, Loki, OTel, Langfuse | ⏳ |
| **8** | Fiabilité — tests de charge, chaos engineering, DR | ⏳ |

---

## 📚 Documentation

### Produit & exigences
- [PRD](docs/product/PRD.md) — contexte métier, personas, objectifs, périmètre, modèle d'autonomie
- [SRS](docs/product/SRS.md) — exigences fonctionnelles et non fonctionnelles, matrice RBAC, traçabilité
- [SLO](docs/product/SLO.md) — SLI/SLO/SLA, error budgets, sévérité des incidents

### Architecture
- [Système](docs/architecture/01-system-architecture.md) — vues C4, flux, déploiement, risques
- [Données](docs/architecture/02-data-architecture.md) — zones, partitionnement, qualité, lineage
- [Sécurité](docs/architecture/03-security-architecture.md) — modèle de confiance, 5 couches de contrôle
- [IA](docs/architecture/04-ai-architecture.md) — ML vs LLM, agents, tools, RAG, évaluation
- [**21 ADR**](docs/adr/README.md) — chaque décision structurante, ses alternatives et ses conséquences

### Données
- [Contrats](docs/data/contracts/README.md) — gouvernance, évolution de schéma, DLQ
- [Dictionnaire](docs/data/data-dictionary.md) — modèle bronze/silver/gold, référentiels
- [Référentiels synthétiques](data-platform/dbt/seeds/README.md) — réseau fictif déterministe (1 610 sites, 4 821 cellules)

### Sécurité
- [Threat model](docs/security/threat-model.md) — STRIDE + menaces spécifiques aux agents IA
- [Politique PII](docs/security/pii-policy.md) — classification, tokenisation, rétention

### Contribuer
- [CONTRIBUTING](CONTRIBUTING.md) — conventions, règles d'architecture vérifiées, fichiers générés, pièges connus

---

## 🧭 Principes de conception

| # | Principe |
|---|---|
| **P1** | **Event-driven d'abord** — toute donnée entre par Kafka |
| **P2** | **La donnée brute est immuable** — bronze n'est jamais modifié |
| **P3** | **Les contrats précèdent le code** — pas de flux sans contrat Avro |
| **P4** | **Le LLM ne détient jamais la donnée** — toute affirmation chiffrée provient d'un tool |
| **P5** | **L'autonomie est graduée, jamais totale** — approbation humaine obligatoire sur les actions sensibles |
| **P6** | **Sécurité en profondeur** — aucune garantie ne repose sur une seule couche |
| **P7** | **Observable par conception** — l'instrumentation est écrite avec le code |

---

## 🧪 Qualité

```bash
make lint        # ruff + eslint
make typecheck   # mypy (--strict sur libs/)
make test        # pytest + couverture
make validate    # contrats de données + règles d'architecture
make security    # gitleaks + pip-audit + trivy
make ci          # tout ce qui précède
```

| Contrôle | Cible |
|---|---|
| Couverture de tests (libs, API, agents) | ≥ 80 % |
| Typage strict `libs/` | 0 erreur |
| Contrats de données valides | 11/11 |
| Secrets dans le dépôt | 0 |
| PII en clair au-delà de l'ingestion | 0 |
| Durée de la CI | < 15 min |

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE).

---

*Projet réalisé par **Mamadou Sanogo**, Ingénieur Informatique — Big Data & IA.
Opérateur fictif, données synthétiques, architecture réelle.*
