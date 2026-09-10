# ADR-017 — OpenTelemetry comme standard d'instrumentation unique

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 7 |

## Contexte

Le PRD §48 pose l'exigence centrale : tracer une requête de bout en bout et **identifier où les
8 secondes ont été consommées**. Le chemin d'une requête traverse des technologies hétérogènes :

```
Frontend Next.js → API FastAPI → Supervisor LangGraph → Network Agent
   → Tool SQL → Trino → Iceberg/MinIO
   → Tool ML → service d'inférence
   → LLM Gateway → Groq
→ Réponse
```

Sans identifiant de corrélation unique, diagnostiquer une lenteur consiste à comparer
manuellement des horodatages entre six systèmes de logs — ce qui, en pratique, ne se fait pas.

Difficulté supplémentaire propre à cette plateforme : **les agents IA ont des besoins
d'observabilité que les outils APM classiques ne couvrent pas** — trajectoire de raisonnement,
prompts, appels de tools, consommation de tokens, coût. Prometheus sait mesurer une latence,
pas évaluer la qualité d'un prompt.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Corrélation traces / logs / métriques par identifiant unique | 🔴 Élevé |
| C2 | Absence de verrouillage fournisseur | 🟠 Moyen |
| C3 | Observabilité spécifique aux LLM (tokens, coût, trajectoires) | 🔴 Élevé |
| C4 | Empreinte ressources | 🟠 Moyen |
| C5 | Instrumentation automatique des bibliothèques courantes | 🟠 Moyen |

## Décision

**OpenTelemetry comme unique standard d'instrumentation applicative**, avec un collecteur central
qui route vers des backends spécialisés :

```
   Applications (API, agents, ML, générateurs)
        │  SDK OpenTelemetry — un seul standard d'instrumentation
        ▼
   ┌─────────────────────────────────┐
   │     OpenTelemetry Collector      │  ← point de routage unique
   └──┬──────────┬──────────┬────────┘
      │ métriques│ logs     │ traces
      ▼          ▼          ▼
  Prometheus   Loki      Tempo/Jaeger
      │          │          │
      └──────────┴──────────┘
                 ▼
             Grafana        ← visualisation unifiée, corrélation par trace_id

   Agents LangGraph ──────► Langfuse   (spécifique LLM, complémentaire — pas redondant)
```

### Répartition des responsabilités

| Outil | Répond à la question | Ne répond pas à |
|---|---|---|
| **Prometheus** | *Combien ? À quelle fréquence ?* — consumer lag, taux d'erreur, latences agrégées | Pourquoi cette requête-ci a été lente |
| **Loki** | *Que s'est-il passé ?* — logs applicatifs corrélés par `trace_id` | Vue d'ensemble |
| **Tempo/Jaeger** | *Où est passé le temps ?* — décomposition de la latence par span | Contenu métier |
| **Langfuse** | *Comment l'agent a-t-il raisonné ?* — prompts, tools, tokens, coût, trajectoire | Santé de l'infrastructure |
| **Grafana** | Visualisation et corrélation de l'ensemble | — |

> **Langfuse n'est pas redondant avec le tracing OTel.** Une trace OTel indique qu'un appel LLM a
> duré 3,2 s ; Langfuse indique quel prompt a été envoyé, quels tools le modèle a choisis, pourquoi
> il a bouclé, combien de tokens il a consommés et ce que cela a coûté. Ce sont deux plans d'analyse
> distincts, tous deux nécessaires — le second est celui qui permet réellement d'améliorer un agent.

### Pourquoi OpenTelemetry plutôt qu'une instrumentation propre à chaque outil

| Alternative | Raison du rejet |
|---|---|
| Client Prometheus + logs structurés séparés | Aucune corrélation automatique ; le `trace_id` devrait être propagé à la main partout (C1) |
| APM propriétaire (Datadog, New Relic) | Coût prohibitif, verrouillage fournisseur (C2), inadapté à une démonstration locale |
| Instrumentation manuelle | Effort considérable, couverture incomplète, aucune propagation de contexte inter-services (C5) |

OpenTelemetry est le **standard CNCF** : une seule instrumentation, des backends interchangeables.
Remplacer Tempo par Jaeger, ou Prometheus par un service managé, ne touche aucune ligne de code
applicatif — uniquement la configuration du collecteur (C2).

## Conséquences

**Positives**
- Un `trace_id` unique traverse frontend, API, agents, tools, Trino et LLM : le diagnostic devient direct.
- L'instrumentation automatique (FastAPI, SQLAlchemy, requests, Kafka) couvre l'essentiel sans code.
- La corrélation logs ↔ traces dans Grafana permet de passer d'un span lent aux logs correspondants en un clic.
- Les backends sont interchangeables : la stack locale et une stack cloud managée partagent le même code.
- Les **spans personnalisés métier** (`agent.node.plan`, `tool.query_network_metrics`, `llm.call`) rendent la trajectoire d'un agent lisible dans le waterfall de la trace.

**Négatives**
- **Le collecteur est un point de défaillance** et un goulet potentiel → configuration en mode agent local + traitement par lots, avec dégradation silencieuse (une perte de télémétrie ne doit **jamais** faire échouer une requête métier).
- Le surcoût d'instrumentation est réel (~3–8 % de latence) → échantillonnage adaptatif : 100 % des erreurs et des exécutions d'agents, taux réduit pour le trafic nominal.
- Stack d'observabilité complète ≈ 2,5 GB → profil Compose `observability` dédié, non démarré en permanence ([ADR-011](ADR-011-local-dev-strategy.md)).
- Deux systèmes de traçage à consulter (Tempo et Langfuse) → mitigation : le `trace_id` OTel est **injecté comme métadonnée Langfuse**, permettant de naviguer de l'un à l'autre.

**À surveiller**
- Volume de télémétrie généré par les agents (les prompts sont volumineux) — rétention Langfuse limitée à 30 j en développement.
- Absence de PII dans les traces et les prompts enregistrés (NFR-SEC-010) : les attributs de span sont filtrés par un processeur dédié.

## Notes d'implémentation

**Convention de nommage des spans**
```
http.server.request              instrumentation automatique
agent.graph.<nom_du_graphe>      span racine d'une exécution d'agent
agent.node.<nom_du_noeud>        un nœud LangGraph (classify, plan, synthesize…)
tool.<nom_du_tool>               une invocation de tool
llm.<provider>.<modele>          un appel LLM (attributs : tokens_in/out, coût, cache_hit)
db.trino.query                   une requête Trino (attributs : requête tronquée, lignes, octets)
ml.predict.<modele>              une inférence
```

**Attributs obligatoires sur tout span** (NFR-OBS-002)
```
tenant_id · user_id (si applicable) · trace_id · span_id · service.name · deployment.environment
```

**Filtrage PII des traces** — un processeur de span rejette systématiquement :
- tout attribut correspondant à un motif de MSISDN ;
- le contenu intégral des prompts si `OTEL_REDACT_PROMPTS=true` (défaut en production) ;
- les valeurs des en-têtes d'autorisation.

**Métriques métier exposées** (au-delà de l'instrumentation automatique)
```
telecomx_kafka_consumer_lag{topic,group}
telecomx_data_freshness_seconds{table,zone}
telecomx_dq_violations_total{table,rule,severity}
telecomx_dlq_messages_total{topic,reason}
telecomx_model_prediction_latency_seconds{model,version}
telecomx_model_drift_score{model,feature}
telecomx_agent_executions_total{agent,status}
telecomx_agent_tool_calls_total{agent,tool,status}
telecomx_llm_tokens_total{provider,model,direction}
telecomx_llm_cost_total{provider,model}
telecomx_approvals_pending{risk_level}
```

> 📌 Ces métriques sont définies **dès la Phase 1** dans `libs/telecom-common/observability/`,
> même si les dashboards n'arrivent qu'en Phase 7. Instrumenter au moment de l'écriture du code
> coûte quelques minutes ; réinstrumenter six mois plus tard coûte des jours.

## Références
- [PRD §46-49](../product/PRD.md) · [NFR-OBS-001…005](../product/SRS.md) · [FR-M9-001…005](../product/SRS.md) · [ADR-009](ADR-009-llm-provider.md) · [ADR-011](ADR-011-local-dev-strategy.md)
