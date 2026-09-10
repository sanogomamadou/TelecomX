# PRD — TelecomX Intelligence & Autonomous Operations Platform

| Champ | Valeur |
|---|---|
| **Document** | Product Requirements Document (PRD) |
| **Version** | 1.0 |
| **Statut** | ✅ Approuvé (baseline Phase 0) |
| **Date** | 2026-09-10 |
| **Auteur** | Mamadou Sanogo — Ingénieur Informatique, Big Data & IA |
| **Audience** | Direction Technique, Équipes Réseau/NOC, Data, Marketing, Fraude, Sécurité |
| **Documents liés** | [SRS](SRS.md) · [Architecture](../architecture/01-system-architecture.md) · [ADR](../adr/README.md) · [Threat Model](../security/threat-model.md) |

---

## 1. Résumé exécutif

TelecomX exploite un réseau mobile 2G/3G/4G/5G et une plateforme Mobile Money desservant
**12,4 millions d'abonnés** sur **4 821 cellules** réparties en **1 610 sites**. L'opérateur produit
quotidiennement plusieurs centaines de millions d'événements techniques et commerciaux, mais
**ces données sont exploitées en silos** : le NOC surveille le réseau, le marketing analyse le churn,
la fraude examine les transactions — chacun avec ses propres extractions, ses propres outils et sa
propre vérité.

La conséquence est un **temps de compréhension trop long**. Lorsqu'une dégradation réseau survient,
il faut aujourd'hui en moyenne **47 minutes** entre la détection de l'alarme et l'identification de la
cause racine, parce qu'un ingénieur doit corréler manuellement des métriques radio, des alarmes
d'équipement, l'historique de la cellule et les procédures documentaires.

Ce document spécifie une plateforme qui unifie ces données dans un Lakehouse temps réel, en dérive
des prédictions (trafic, pannes, churn, fraude), et surtout expose ces capacités à travers une
**couche d'agents IA capables non seulement d'expliquer, mais de recommander et d'exécuter des
actions opérationnelles sous contrôle humain**.

> **Proposition de valeur**
> Passer d'une exploitation **réactive** (« une alarme a sonné, cherchons pourquoi »)
> à une exploitation **prédictive et autonome** (« cette cellule saturera dans 6 h,
> voici la cause probable, voici l'action, valides-tu ? »).

---

## 2. Contexte métier

### 2.1 L'opérateur fictif

> ⚠️ **TelecomX est une entreprise entièrement fictive.** Toutes les données manipulées par cette
> plateforme sont **synthétiques**, générées par les simulateurs du dépôt (`data-platform/generators/`).
> Aucune donnée personnelle réelle n'est utilisée, à aucun stade. Voir [Politique PII](../security/pii-policy.md).

| Dimension | Valeur | Commentaire |
|---|---|---|
| Abonnés | 12 400 000 | dont 11,1 M grand public, 1,3 M entreprises |
| Cellules | 4 821 | 2G: 1 205 · 3G: 1 412 · 4G: 1 908 · 5G: 296 |
| Sites | 1 610 | urbains, périurbains, ruraux |
| Régions | 9 | Bamako, Kayes, Koulikoro, Sikasso, Ségou, Mopti, Tombouctou, Gao, Kidal |
| Comptes Mobile Money | 6 800 000 | ~55 % de la base abonnés |
| Marchands Mobile Money | 42 000 | agents, boutiques, e-commerce |
| ARPU moyen | 3 850 XOF/mois | ~5,9 € |

### 2.2 Volumétrie des flux (production cible)

| Flux | Débit moyen | Pic | Volume/jour |
|---|---|---|---|
| `network.metrics` | 80 msg/s | 240 msg/s | 6,9 M |
| `network.alarms` | 3 msg/s | 400 msg/s (orage/panne) | 260 K |
| `cdr.events` | 1 150 msg/s | 4 800 msg/s | 99 M |
| `data.sessions` | 580 msg/s | 2 100 msg/s | 50 M |
| `sms.events` | 320 msg/s | 9 000 msg/s (Nouvel An) | 28 M |
| `ussd.events` | 210 msg/s | 700 msg/s | 18 M |
| `recharge.events` | 95 msg/s | 380 msg/s | 8,2 M |
| `mobilemoney.transactions` | 25 msg/s | 190 msg/s | 2,1 M |
| `customer.events` | 12 msg/s | 60 msg/s | 1,0 M |
| **Total** | **~2 475 msg/s** | **~17 800 msg/s** | **~214 M évén./jour** |

> 📐 **Facteur d'échelle de développement.** L'environnement local ne peut pas soutenir 2 475 msg/s.
> Les générateurs exposent un paramètre `SPEED_FACTOR` (défaut `0.001` en dev, soit ~2,5 msg/s)
> qui préserve **toutes les proportions statistiques** (distributions, saisonnalité, taux d'anomalie)
> en ne réduisant que le débit. Les dimensionnements de partitions Kafka et le sizing Spark sont,
> eux, calculés pour la volumétrie de production. Voir [ADR-011](../adr/ADR-011-local-dev-strategy.md).

### 2.3 Problèmes constatés (baseline « avant plateforme »)

| # | Problème | Mesure actuelle | Impact |
|---|---|---|---|
| P1 | Diagnostic réseau manuel et lent | MTTI\* 47 min | Prolongation des incidents, pénalités SLA |
| P2 | Saturation cellulaire découverte *a posteriori* | 0 prévision | Dégradation d'expérience aux heures de pointe |
| P3 | Pannes d'équipement subies | 100 % correctif | Coût d'intervention d'urgence ×3 |
| P4 | Churn détecté après résiliation | 0 anticipation | Perte de revenu non adressable |
| P5 | Fraude Mobile Money détectée en J+2 | Règles statiques | Pertes financières, risque réglementaire |
| P6 | Analyse de revenu en silo | Reporting mensuel | Décisions commerciales tardives |
| P7 | Connaissance opérationnelle dispersée | SOP en PDF | Dépendance aux experts, formation lente |

\* *MTTI = Mean Time To Identify (délai moyen d'identification de la cause racine).*

---

## 3. Vision produit

```
   Observe  →  Understand  →  Predict  →  Decide  →  Act  →  Verify
      │            │             │           │         │        │
   Kafka       Lakehouse       ML        Agents    Tools   Feedback
   Streaming   Iceberg      MLflow     LangGraph   APIs     loop
```

La plateforme n'est pas un dashboard de plus, ni un chatbot branché sur une base.
C'est une **boucle de contrôle fermée** : chaque action exécutée est vérifiée par les données
qu'elle est censée améliorer, et ce résultat réalimente la plateforme.

### 3.1 Ce que la plateforme est

- Un **socle de données temps réel** unifiant réseau, client, financier et documentaire.
- Un **moteur de prédiction** industrialisé (versionné, monitoré, réentraînable).
- Un **système multi-agent outillé** qui interroge les données réelles via des *tools*, jamais sa mémoire.
- Un **cadre de gouvernance de l'autonomie** : chaque action a un niveau de risque et une règle d'approbation.

### 3.2 Ce que la plateforme n'est pas

- ❌ Un remplacement de l'OSS/BSS ou de l'EMS constructeur — elle les **consomme** et les **pilote**.
- ❌ Un système de facturation ou de médiation légale (pas de valeur probante).
- ❌ Un LLM répondant de mémoire — **toute affirmation chiffrée provient d'un appel outil traçable**.
- ❌ Un système autorisé à modifier la configuration radio sans validation humaine (voir §7).

---

## 4. Personas

| # | Persona | Rôle | Besoin principal | Page d'entrée |
|---|---|---|---|---|
| U1 | **Awa** — Opératrice NOC | Surveillance 24/7 | Voir l'état réseau, trier les incidents, appliquer une procédure | `/dashboard`, `/alarms` |
| U2 | **Ibrahim** — Ingénieur Réseau | Diagnostic & optimisation | Comprendre *pourquoi* une zone se dégrade, comparer à l'historique | `/network`, `/cells` |
| U3 | **Fatoumata** — Analyste Data | Analyse transverse | Requêter librement le Lakehouse, valider la qualité | `/data-quality`, Trino |
| U4 | **Moussa** — Analyste Fraude | Investigation | Identifier et instruire les activités suspectes | `/fraud` |
| U5 | **Aïssata** — Responsable Marketing | Rétention | Cibler les abonnés à risque, comprendre les causes du churn | `/customers`, `/churn` |
| U6 | **Cheick** — Ingénieur Terrain | Maintenance | Recevoir des interventions préventives priorisées | `/incidents` |
| U7 | **Salif** — Directeur Commercial | Pilotage | Expliquer une variation de revenu | `/revenue` |
| U8 | **Kadiatou** — Data/ML Engineer | Industrialisation | Surveiller pipelines, modèles, drift | `/models`, `/monitoring` |
| U9 | **Ousmane** — Administrateur | Sécurité & exploitation | Gérer accès, audit, configuration | `/admin` |

---

## 5. Objectifs & indicateurs de succès

### 5.1 Objectifs métier

| # | Objectif | Baseline | Cible | Horizon |
|---|---|---|---|---|
| O1 | Réduire le délai d'identification de cause racine | 47 min | **< 8 min** | Phase 4 |
| O2 | Anticiper la saturation cellulaire | 0 % | **85 %** des saturations prévues à H-6 | Phase 3 |
| O3 | Passer la maintenance en préventif | 0 % | **40 %** des interventions préventives | Phase 3 |
| O4 | Anticiper le churn | 0 % | **Recall ≥ 0,70** à 30 jours | Phase 3 |
| O5 | Détecter la fraude en quasi temps réel | J+2 | **< 5 min**, précision ≥ 0,85 | Phase 3 |
| O6 | Automatiser les actions à faible risque | 0 % | **60 %** des actions L1 sans intervention | Phase 4 |
| O7 | Rendre la connaissance opérationnelle accessible | SOP PDF | **RAG ≥ 0,80** de justesse ancrée | Phase 4 |

### 5.2 Indicateurs plateforme (product metrics)

| Métrique | Cible | Mesurée par |
|---|---|---|
| Fraîcheur donnée bronze (p95) | < 60 s | Prometheus / OTel |
| Fraîcheur donnée gold (p95) | < 15 min | dbt freshness |
| Disponibilité API | ≥ 99,5 % | Prometheus blackbox |
| Latence agent bout-en-bout (p95) | < 20 s | Langfuse + OTel |
| Taux de succès des agents | ≥ 95 % | Langfuse |
| Coût LLM par requête agent | < 0,01 € | Langfuse cost tracking |
| Taux d'échec des tools | < 2 % | Langfuse |
| Couverture de tests | ≥ 80 % (libs & api) | pytest-cov / CI |
| Conformité des contrats de données | 100 % | Schema Registry + DQ gate |

---

## 6. Périmètre fonctionnel

### 6.1 Modules

| Module | Description | Phase |
|---|---|---|
| **M1 — Data Ingestion** | Simulateurs des 9 systèmes sources + producteurs Kafka | 1 |
| **M2 — Streaming & Lakehouse** | Spark Structured Streaming → Iceberg (bronze/silver/gold) | 1 |
| **M3 — Analytics** | Trino + dbt : KPI réseau, customer 360, revenue | 1 |
| **M4 — Data Quality & Governance** | Contrats, validation, lineage, catalogue, PII | 2 |
| **M5 — ML Platform** | 5 modèles + MLflow + feature store + serving | 3 |
| **M6 — Agent Platform** | 8 agents LangGraph + tools + RAG + mémoire + HITL | 4 |
| **M7 — Application** | FastAPI + Next.js (NOC Dashboard, AI Command Center) | 5 |
| **M8 — Infrastructure** | Docker, K8s, Helm, Terraform, ArgoCD, CI/CD | 6 |
| **M9 — Observability** | Prometheus, Grafana, Loki, OTel, Langfuse | 7 |
| **M10 — Reliability** | Load tests, chaos engineering, DR | 8 |

### 6.2 Cas d'usage prioritaires

| ID | Cas d'usage | Persona | Priorité | Module |
|---|---|---|---|---|
| UC-01 | Diagnostiquer une dégradation de qualité réseau sur une zone | U2 | **P0** | M6 |
| UC-02 | Prévoir le trafic data à H+24 par cellule | U2 | **P0** | M5 |
| UC-03 | Détecter une anomalie réseau en temps réel | U1 | **P0** | M5 |
| UC-04 | Identifier les équipements à risque de panne | U6 | P1 | M5 |
| UC-05 | Scorer le risque de churn par abonné | U5 | P1 | M5 |
| UC-06 | Détecter une transaction Mobile Money frauduleuse | U4 | **P0** | M5 |
| UC-07 | Expliquer une variation de revenu data | U7 | P1 | M6 |
| UC-08 | Exécuter une action opérationnelle avec approbation | U1 | **P0** | M6 |
| UC-09 | Consulter une procédure (SOP) contextualisée aux métriques live | U1/U2 | P1 | M6 |
| UC-10 | Superviser la qualité des données et le drift des modèles | U8 | P1 | M4/M9 |
| UC-11 | Requêter le Lakehouse en libre-service | U3 | P2 | M3 |
| UC-12 | Auditer les actions des agents | U9 | P1 | M6/M9 |

> 📖 Chaque cas d'usage est détaillé (acteurs, préconditions, flux nominal, flux alternatifs,
> critères d'acceptation) dans [SRS §4](SRS.md#4-exigences-fonctionnelles).

### 6.3 Hors périmètre (v1)

| Exclusion | Justification |
|---|---|
| Connexion à un vrai réseau / EMS constructeur | Projet à données synthétiques par conception (conformité, reproductibilité) |
| Facturation et médiation à valeur probante | Domaine réglementé, hors sujet démonstratif |
| Application mobile abonné | Le périmètre est l'exploitation interne |
| Optimisation radio automatique (SON) | Trop risqué sans jumeau numérique validé — la plateforme *recommande*, l'EMS *applique* |
| Multi-tenant en production | Conçu et documenté (§Multi-tenancy), implémenté en isolation logique uniquement |

---

## 7. Modèle d'autonomie & gouvernance des actions

Le cœur différenciant du produit. Chaque *tool* exposé aux agents porte une **classe de risque**
qui détermine son régime d'approbation. Ce contrat est déclaratif et vérifié à l'exécution.

| Classe | Exemples de tools | Régime | Traçabilité |
|---|---|---|---|
| 🟢 **L1 — Lecture** | `query_network_metrics`, `get_cell_status`, `get_customer_profile` | Automatique | Log + trace OTel |
| 🟢 **L2 — Faible risque** | `generate_report`, `send_alert`, `create_incident` | Automatique, notifié | Audit log |
| 🟠 **L3 — Risque moyen** | `run_diagnostic`, `restart_service`, `create_maintenance_ticket` | **Approbation requise** | Audit + identité approbateur |
| 🔴 **L4 — Risque élevé** | `change_cell_configuration`, `block_subscriber`, `reverse_transaction` | **Approbation + double contrôle** | Audit + 2 identités + justification |
| ⛔ **L5 — Interdit v1** | Modification de configuration radio en masse, actions de facturation | Non exposé aux agents | — |

### 7.1 Échelle de maturité d'autonomie

| Niveau | Capacité | Livré en |
|---|---|---|
| **N0** | Detect | Phase 1 (streaming) + Phase 3 (anomalie) |
| **N1** | Detect + Explain | Phase 4 (Network Agent + RAG) |
| **N2** | + Recommend | Phase 4 (Operations Agent) |
| **N3** | + Human approval + Execute | Phase 4/5 (HITL + UI d'approbation) |
| **N4** | + Autonomous decide + Verify | Phase 8 (boucle de vérification, périmètre L1/L2 uniquement) |

> ⚠️ **Principe de conception non négociable** : le passage à N4 reste **cantonné aux actions L1/L2**.
> Aucune action L3+ ne sera jamais exécutée sans approbation humaine, quelle que soit la confiance du modèle.

---

## 8. Parcours utilisateur clé (UC-01 + UC-08)

Le scénario de démonstration bout-en-bout, qui traverse chaque couche de la plateforme :

```
[T+0s]   Le générateur injecte une dégradation progressive sur CELL-0421
         packet_loss 1,2 % → 14,8 % · latency 32 → 184 ms · drop_rate 0,8 % → 12,6 %
             │
[T+5s]   Kafka  ─ network.metrics ─────────────────────────────────► ingestion
             │
[T+20s]  Spark Structured Streaming : validation, dédup, écriture bronze → silver
             │
[T+45s]  Modèle d'anomalie : score 0,97  →  🚨 publication sur network.alarms
             │
[T+50s]  Operations Agent déclenché (event-driven, pas de prompt humain)
             │
             ├─► query_network_metrics(CELL-0421, 24h)   → dégradation confirmée
             ├─► get_network_alarms(SITE-102)            → alarme transport active
             ├─► get_equipment_status(ROUTER-082)        → CPU 94 %, erreurs CRC
             └─► rag_search("procédure packet loss élevé") → SOP-NET-014
             │
[T+62s]  Root Cause Analysis : « Congestion backhaul — confiance 91 % »
         Recommandation : run_backhaul_diagnostic()  [classe L3]
             │
[T+65s]  ⏸️  INTERRUPTION LangGraph — attente d'approbation humaine
             │
[T+2min] Awa (NOC) consulte le raisonnement tracé et clique  ✅ Approve
             │
[T+2min] Exécution run_backhaul_diagnostic() → confirmation du défaut
         create_incident() → INC-2026-009821
             │
[T+15min] Boucle de vérification : les métriques sont ré-interrogées
         packet_loss 2,1 % · latency 41 ms · drop_rate 1,3 %
             │
[T+15min] 🟢 Incident résolu — clôture automatique + rapport post-mortem généré
```

Chaque étape est **observable** (trace OTel unique de bout en bout) et **auditable**
(qui a approuvé quoi, sur la base de quelles données, à quel instant).

---

## 9. Contraintes

### 9.1 Contraintes techniques

| # | Contrainte | Conséquence de conception |
|---|---|---|
| C1 | Environnement de dev : 16 GB RAM, 12 vCPU, 54 GB disque | `docker-compose` en **profils** ; jamais toute la stack simultanément ([ADR-011](../adr/ADR-011-local-dev-strategy.md)) |
| C2 | Pas de budget cloud | Tout tourne en local ; K8s/Terraform validés sur `kind` |
| C3 | LLM sur free tier (Groq) | Rate limiting, budget tokens, cache sémantique, fallback ([ADR-009](../adr/ADR-009-llm-provider.md)) |
| C4 | Poste Windows 11 | Chemins portables, `.editorconfig` LF, scripts PowerShell **et** Bash |
| C5 | Aucune donnée réelle | Générateurs synthétiques statistiquement crédibles obligatoires |

### 9.2 Contraintes réglementaires simulées

La plateforme est conçue **comme si** elle était soumise au RGPD et à la réglementation
télécom sur le secret des correspondances :

- Les MSISDN ne circulent **jamais en clair** au-delà de l'ingestion (tokenisation HMAC-SHA256 dès bronze).
- Le contenu des communications n'est **jamais** collecté (uniquement des métadonnées : durée, statut, cellule).
- Les agents IA n'accèdent **jamais** aux identifiants directs — uniquement des tokens et agrégats.
- Rétention différenciée par zone (bronze 90 j, silver 400 j, gold 5 ans).

> 📖 Détail dans [Politique PII](../security/pii-policy.md) et [Threat Model](../security/threat-model.md).

---

## 10. Risques produit

| # | Risque | P | I | Mitigation |
|---|---|---|---|---|
| R1 | **Hallucination d'un agent** produisant une recommandation fausse | M | 🔴 Élevé | Interdiction de répondre sans appel outil ; citation obligatoire des sources ; garde-fous de sortie ; évaluation systématique ([Phase 4](../adr/ADR-008-agent-orchestration.md)) |
| R2 | **Action automatique destructrice** | F | 🔴 Élevé | Classes de risque, HITL obligatoire L3+, allowlist de tools par rôle, kill-switch global |
| R3 | Épuisement du quota LLM en démo | É | 🟠 Moyen | Cache sémantique, budget par requête, fallback provider, mode dégradé déterministe |
| R4 | Dérive des modèles ML non détectée | M | 🟠 Moyen | Monitoring de drift (PSI/KS), seuils d'alerte, pipeline de réentraînement |
| R5 | Ressources locales insuffisantes | É | 🟠 Moyen | Profils Compose, `SPEED_FACTOR`, données échantillonnées, garde-fous mémoire documentés |
| R6 | Fuite de PII vers le contexte LLM | F | 🔴 Élevé | Tokenisation en amont, scan PII en sortie de tool, tests dédiés, RBAC row-level |
| R7 | Complexité excessive nuisant à la démonstrabilité | É | 🟠 Moyen | Scénario de démo scripté (`make demo`), documentation par phase, ADR justifiant chaque brique |

*P = Probabilité (F/M/É) · I = Impact*

---

## 11. Roadmap

| Phase | Contenu | Sortie de phase (Definition of Done) |
|---|---|---|
| **0** | Product & Architecture | PRD, SRS, 17 ADR, threat model, data contracts, SLO — **validés** |
| **1** | Telecom Data Platform | 9 flux → Kafka → Spark → Iceberg → Trino → dbt ; `make demo-data` fonctionne |
| **2** | Data Quality & Governance | Schema Registry, DQ gates, lineage, catalogue, tokenisation PII |
| **3** | ML Platform | 5 modèles entraînés, MLflow registry, serving, monitoring de drift |
| **4** | Agent Platform | 8 agents, tools typés, RAG, mémoire, supervisor, HITL, évaluation |
| **5** | Application | FastAPI (JWT/RBAC) + Next.js (NOC + AI Command Center) |
| **6** | Production Infrastructure | Images Docker, Helm charts, Terraform, ArgoCD, CI/CD complète |
| **7** | Observability | 6 dashboards Grafana, traces OTel bout-en-bout, Langfuse |
| **8** | Reliability | Tests de charge, 9 expériences de chaos, plan DR testé (RPO 15 min / RTO 30 min) |

---

## 12. Critères d'acceptation globaux

La plateforme sera considérée comme livrée lorsque :

- [ ] `make up && make demo` reproduit **intégralement** le scénario du §8, sans intervention manuelle.
- [ ] Les 12 cas d'usage du §6.2 sont fonctionnels et couverts par des tests automatisés.
- [ ] Les 7 objectifs métier du §5.1 sont mesurés et atteints sur les données synthétiques.
- [ ] La CI est verte : lint, typage, tests unitaires, intégration, e2e, scan de sécurité.
- [ ] Aucun secret n'est présent dans l'historique Git (vérifié par scan automatisé).
- [ ] Aucune PII en clair au-delà de la couche d'ingestion (vérifié par test dédié).
- [ ] Chaque décision d'architecture structurante est justifiée par un ADR.
- [ ] Les runbooks permettent à un tiers de démarrer, exploiter et restaurer la plateforme.

---

## Historique des révisions

| Version | Date | Auteur | Modification |
|---|---|---|---|
| 1.0 | 2026-09-10 | M. Sanogo | Version initiale — baseline Phase 0 |
