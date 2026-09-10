# Architecture IA — ML & couche agentique

| Champ | Valeur |
|---|---|
| **Version** | 1.0 · 2026-09-10 |
| **Statut** | ✅ Baseline Phase 0 |
| **Documents liés** | [ADR-008](../adr/ADR-008-agent-orchestration.md) · [ADR-009](../adr/ADR-009-llm-provider.md) · [ADR-010](../adr/ADR-010-vector-store.md) · [Sécurité](03-security-architecture.md) |

---

## 1. Séparation des responsabilités : ML vs LLM

La distinction est fondamentale et détermine toute l'architecture.

| | **Modèles ML** | **LLM / Agents** |
|---|---|---|
| **Rôle** | Prédire des grandeurs numériques | Raisonner, orchestrer, expliquer |
| **Entrée** | Features structurées | Langage naturel + résultats de tools |
| **Sortie** | Nombre, probabilité, score | Analyse, recommandation, appel de tool |
| **Fiabilité** | Mesurable (AUC, MAE, recall) | Non déterministe, à encadrer |
| **Exemple** | « trafic à 18h = 71 GB » | « le trafic va saturer car… donc je recommande… » |

> 🧭 **Règle de conception centrale (principe P4) : le LLM ne calcule jamais.**
> Il ne prédit pas un trafic, ne compte pas des lignes, n'estime pas une probabilité de churn.
> Il **appelle un tool** qui interroge un modèle ou une table, puis **interprète** le résultat.
> Un chiffre présent dans une réponse d'agent sans appel de tool correspondant est un défaut,
> détecté par les tests d'ancrage factuel (FR-M6-014).

---

## 2. Couche ML

### 2.1 Les cinq modèles

| Modèle | Type | Cible | Métrique principale | Cadence |
|---|---|---|---|---|
| **Traffic Forecasting** | Régression temporelle | Volume data par cellule, H+1 à H+24 | MAPE, WAPE | Horaire |
| **Anomaly Detection** | Non supervisé + seuil | Score d'anomalie multivarié | Précision@k, taux de faux positifs | Temps réel |
| **Churn Prediction** | Classification binaire | Résiliation à 30 j | Recall ≥ 0,70, AUC-PR | Quotidienne |
| **Fraud Detection** | Classification déséquilibrée | Transaction frauduleuse | Précision ≥ 0,85 à recall fixé | Temps réel |
| **Predictive Maintenance** | Classification + survie | Panne d'équipement à 7/30 j | Recall, préavis moyen | Quotidienne |

### 2.2 Choix d'algorithmes et justification

| Modèle | Algorithme retenu | Pourquoi | Écarté |
|---|---|---|---|
| Prévision | **LightGBM** sur features temporelles | Excellent sur données tabulaires avec saisonnalité explicite ; rapide ; explicable ; supporte des milliers de séries via features catégorielles | LSTM/TFT : coûteux, données synthétiques insuffisantes pour en tirer parti — *comparaison documentée dans la model card* |
| Anomalie | **Isolation Forest + seuils statistiques robustes** | Non supervisé (pas d'étiquettes de panne réelles), rapide, interprétable par contribution de feature | Autoencodeur : moins interprétable, l'explicabilité est ici une exigence |
| Churn | **LightGBM + SHAP** | Tabulaire, déséquilibre gérable, **SHAP indispensable** : le marketing doit savoir *pourquoi* un abonné est à risque | Réseau de neurones : gain marginal, perte d'explicabilité |
| Fraude | **XGBoost + features de vélocité** | Robuste au fort déséquilibre, seuil ajustable selon le coût métier | Règles seules : ne s'adaptent pas ; deep learning : volume insuffisant |
| Maintenance | **LightGBM + analyse de survie** | La question n'est pas seulement *si* mais *quand* — la survie fournit une fenêtre | Classification seule : perd l'information temporelle |

> 📌 **Un choix assumé** : LightGBM domine ce projet. Ce n'est pas un manque d'ambition mais un
> constat — sur des données tabulaires télécom, le gradient boosting reste l'état de l'art, il est
> explicable, rapide à entraîner et servable en < 200 ms (NFR-PERF-005). Un Temporal Fusion
> Transformer serait plus impressionnant sur le papier et moins performant en pratique.
> **La comparaison sera néanmoins conduite et documentée**, car c'est la démarche qui compte.

### 2.3 Cycle de vie MLOps

```
gold.<features>  ──►  Feature engineering (définitions partagées libs/)
                            │
                            ▼
                   Découpage temporel (jamais aléatoire)
                            │
                            ▼
                   Entraînement ──► MLflow (params, métriques, artefacts, dataset, version de code)
                            │
                            ▼
                   Évaluation vs référence ──► ⛔ échec si régression
                            │
                            ▼
                   Model Registry : Staging ──[approbation]──► Production
                            │
                            ▼
                   Serving (FastAPI) ──► supervision : latence, drift, qualité
                            │
                            └──► seuil de drift dépassé ──► réentraînement
```

**Garanties**
- **Aucune fuite temporelle** : découpage chronologique, features toujours antérieures à la cible (FR-M5-009).
- **Aucun train/serving skew** : les définitions de features vivent dans `libs/telecom-common/features/` et sont utilisées à l'identique par l'entraînement et le service (FR-M5-008) — c'est la principale justification du monorepo ([ADR-001](../adr/ADR-001-monorepo.md)).
- **Reproductibilité** : chaque exécution enregistre le snapshot Iceberg du dataset, permettant de rejouer exactement l'entraînement.
- **Model card obligatoire** : données, performances, limites, biais, conditions d'usage (FR-M5-014).

---

## 3. Couche agentique

### 3.1 Les huit agents

| Agent | Question à laquelle il répond | Tools principaux | Déclenchement |
|---|---|---|---|
| **Supervisor** | Route et synthétise | délégation | Utilisateur |
| **Network Analyst** | *Pourquoi la qualité s'est dégradée ?* | métriques, alarmes, équipement, historique, RAG | Utilisateur, événement |
| **Forecast** | *Quelles cellules vont saturer ?* | prévision, capacité, historique | Utilisateur, planifié |
| **Maintenance** | *Quels équipements vont tomber en panne ?* | santé équipement, modèle de panne, ticket | Utilisateur, planifié |
| **Customer Intelligence** | *Pourquoi le churn augmente ?* | churn, customer 360, qualité vécue, réclamations | Utilisateur |
| **Fraud** | *Quelles activités sont suspectes ?* | score de fraude, transactions, historique, géographie | Utilisateur, événement |
| **Revenue** | *Pourquoi le revenu a baissé ?* | revenu, recharges, bundles, disponibilité réseau | Utilisateur |
| **Operations** | *Que faire, et le faire* | diagnostic, incident, alerte, vérification | **Événement** (alarme) |

> L'**Operations Agent** est le seul déclenché principalement par événement plutôt que par requête.
> C'est lui qui porte la boucle *Detect → Explain → Recommend → Act → Verify* et qui donne son sens
> au terme « autonomous operations ».

### 3.2 Topologie d'orchestration

Voir [ADR-008 §Topologie](../adr/ADR-008-agent-orchestration.md) pour le graphe détaillé.

Points de conception :

| Nœud | Responsabilité | Modèle utilisé |
|---|---|---|
| `guardrails_in` | Assainissement, détection d'injection | Heuristiques + petit modèle |
| `classify` | Domaine, intention, urgence | Petit modèle rapide |
| `plan` | Décomposition, sélection d'agents | Modèle à tool calling |
| `<agents>` | Exécution spécialisée, en **parallèle** | Modèle à tool calling |
| `synthesize` | Agrégation, analyse de cause racine | Modèle le plus capable |
| `recommend` | Proposition d'action + classe de risque | Modèle à tool calling |
| `hitl` | `interrupt()` — persistance et attente | *(aucun LLM)* |
| `execute` | Invocation du tool approuvé | *(aucun LLM)* |
| `verify` | Re-mesure et confirmation | *(déterministe + synthèse)* |

**L'exécution parallèle des agents spécialisés** est ce qui rend le SLO de 20 s (NFR-PERF-007)
atteignable : interroger séquentiellement quatre agents coûterait quatre fois plus.

### 3.3 Catalogue de tools

| Tool | Classe | Retourne |
|---|---|---|
| `query_network_metrics(cell_id, from, to, granularity)` | 🟢 L1 | Séries de KPI |
| `get_cell_status(cell_id)` | 🟢 L1 | État courant, santé, capacité |
| `get_network_alarms(scope, since, severity)` | 🟢 L1 | Alarmes actives et historiques |
| `get_equipment_status(equipment_id)` | 🟢 L1 | Télémétrie, uptime, historique |
| `run_traffic_forecast(cell_id, horizon)` | 🟢 L1 | Prévision + intervalle de confiance |
| `detect_network_anomaly(cell_id, window)` | 🟢 L1 | Score + features contributives |
| `predict_churn(subscriber_token)` | 🟢 L1 | Probabilité + facteurs SHAP |
| `detect_fraud(transaction_id \| account_token)` | 🟢 L1 | Score + signaux déclencheurs |
| `predict_equipment_failure(equipment_id)` | 🟢 L1 | Probabilité + fenêtre |
| `get_customer_profile(subscriber_token)` | 🟢 L1 | Customer 360 (tokenisé) |
| `get_revenue_metrics(scope, period)` | 🟢 L1 | Revenu et décomposition |
| `rag_search(query, domain, doc_type)` | 🟢 L1 | Extraits sourcés |
| `generate_report(type, scope, period)` | 🟢 L2 | Rapport structuré |
| `send_alert(channel, severity, message)` | 🟢 L2 | Confirmation d'envoi |
| `create_incident(title, severity, context)` | 🟢 L2 | Identifiant d'incident |
| `create_maintenance_ticket(equipment_id, priority, reason)` | 🟠 L3 | Identifiant de ticket |
| `run_diagnostic(target, type)` | 🟠 L3 | Résultat de diagnostic |
| `restart_service(service_id)` | 🟠 L3 | Statut d'exécution |
| `change_cell_configuration(cell_id, params)` | 🔴 L4 | Statut + rollback disponible |
| `block_subscriber(subscriber_token, reason)` | 🔴 L4 | Confirmation |

**Contrat commun à tout tool**

```python
@tool(risk=RiskClass.L1, roles=["network_engineer", "noc_operator"], domain="network")
def query_network_metrics(
    ctx: ToolContext,                    # tenant + identité, injecté — jamais fourni par le LLM
    cell_id: CellId,                     # type validé (format CELL-NNNN)
    from_ts: datetime, to_ts: datetime,  # fenêtre OBLIGATOIRE — pas de requête non bornée
    granularity: Granularity = "1h",
) -> ToolResult[list[NetworkKpi]]:
    """Retourne les KPI réseau d'une cellule sur une fenêtre temporelle."""
```

Cinq garanties portées par ce contrat :
1. Le `tenant_id` provient du contexte vérifié, **pas** des arguments du modèle ([ADR-016](../adr/ADR-016-multi-tenancy.md)).
2. Tous les paramètres sont **typés et validés** — pas de SQL, pas de chaîne libre.
3. La fenêtre temporelle est **obligatoire** — aucune requête non bornée possible ([ADR-006](../adr/ADR-006-query-engine.md), risque A4).
4. `ToolResult` porte systématiquement la **provenance** (source, horodatage, nombre de lignes), permettant la citation.
5. La classe de risque et les rôles sont **déclaratifs** et exploités par le filtrage du catalogue.

### 3.4 RAG

```
Corpus (Git + MinIO)  ──► Parsing ──► Chunking structurel ──► Embedding ──► Qdrant
                                                                              │
Requête d'agent  ──► Embedding ──► Recherche hybride ──[filtres obligatoires]──┘
                                          │
                                   Reranking ──► Extraits sourcés ──► Agent
```

| Aspect | Choix | Justification |
|---|---|---|
| **Chunking** | Guidé par la structure (titres, étapes) | Une SOP en 5 étapes ne doit jamais être coupée en plein milieu |
| **Contexte** | Titre de section préfixé à chaque chunk | Un chunk isolé « Vérifier le lien transport » est inutilisable sans son contexte |
| **Recherche** | Hybride dense + lexical | Le jargon télécom (codes d'alarme, références SOP) est mal capturé par le dense seul |
| **Filtres** | Rôle **et** validité, non contournables | Sécurité et jamais de procédure périmée ([ADR-010](../adr/ADR-010-vector-store.md)) |
| **Citation** | Obligatoire (document, section, version) | Un ingénieur doit pouvoir vérifier la source |

**Corpus prévu** (~40 documents, rédigés pour le projet) : SOP réseau, procédures d'incident,
manuels de maintenance, guides de dépannage, architecture réseau, catalogue produit, politiques
commerciales, procédures de fraude, procédures de support.

### 3.5 Mémoire

| Type | Portée | Stockage | Usage |
|---|---|---|---|
| **Court terme** | Une exécution | État LangGraph | Contexte de la conversation en cours |
| **Conversation** | Une session | PostgreSQL (checkpointer) | Suivi multi-tours |
| **Long terme** | Persistante | PostgreSQL + Qdrant | Incidents résolus, décisions passées, causes racines confirmées |

La mémoire long terme sert un objectif précis : lorsqu'une dégradation similaire se reproduit,
l'agent retrouve l'incident antérieur, la cause identifiée et l'action qui avait fonctionné.
C'est la différence entre un système qui analyse et un système qui **apprend de son exploitation**.

---

## 4. Évaluation

Un système agentique non évalué est ingouvernable : chaque modification de prompt peut améliorer
un cas et en dégrader dix autres, sans que personne ne s'en aperçoive.

| Dimension | Mesure | Seuil |
|---|---|---|
| **Justesse** | Réponse correcte sur un jeu de questions à vérité connue | ≥ 0,85 |
| **Ancrage factuel** | Tout chiffre cité provient d'un appel de tool | **1,00** (non négociable) |
| **Choix de tools** | Tools appropriés appelés, sans superflu | ≥ 0,90 |
| **Qualité RAG** | Extraits pertinents et cités correctement | ≥ 0,80 |
| **Sécurité** | Résistance à un jeu de tentatives d'injection | **1,00** |
| **Respect des permissions** | Aucun tool hors périmètre proposé | **1,00** |
| **Coût** | Tokens par requête | < budget |
| **Latence** | p95 par type de requête | NFR-PERF-006/007 |

Le jeu d'évaluation est **versionné avec le code** et exécuté en CI sur un sous-ensemble rapide,
puis intégralement avant toute modification de prompt ou de modèle.

---

## 5. Garde-fous

| Position | Contrôle |
|---|---|
| **Entrée** | Détection d'injection, limite de longueur, filtrage de contenu |
| **Pendant** | `MAX_ITERATIONS = 12`, budget de tokens, timeout, circuit breaker |
| **Appel de tool** | Validation de schéma, autorisation, limitation de débit, classe de risque |
| **Sortie** | Scan PII, vérification de cohérence des chiffres cités, refus de conseil hors domaine |
| **Action** | Classe de risque, approbation, expiration, audit |

---

## 6. Dégradation gracieuse

| Panne | Comportement |
|---|---|
| LLM primaire indisponible | Bascule sur le fallback ([ADR-009](../adr/ADR-009-llm-provider.md)) |
| Tous les LLM indisponibles | **Mode déterministe** : les tools restent appelables, les résultats sont présentés sous forme structurée sans synthèse en langage naturel (NFR-AVAIL-006) |
| Un tool échoue | Retry avec backoff, puis dégradation **explicite** : l'agent indique la donnée manquante — jamais d'invention (FR-M6-010) |
| Modèle ML indisponible | L'agent le signale et poursuit avec les données historiques disponibles |
| Qdrant indisponible | Réponse sans appui documentaire, mention explicite de l'absence de procédure |

> 🔑 La règle qui gouverne toute dégradation : **dire ce qui manque plutôt que combler le vide.**
> Un agent qui invente une métrique parce que le tool a échoué est plus dangereux qu'un agent
> qui répond « la donnée est indisponible ».

---

*Les prompts, définitions de tools et jeux d'évaluation sont versionnés dans `agents/`.*
