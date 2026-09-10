# ADR-009 — Groq comme fournisseur LLM primaire derrière une gateway d'abstraction

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 4 |
| **À réexaminer si** | les quotas deviennent bloquants ou si un budget LLM est alloué |

## Contexte

La couche agentique repose sur des appels LLM avec des caractéristiques exigeantes :

- **Volume d'appels élevé par requête** : le Supervisor classifie, planifie, délègue à 2–4 agents
  qui appellent chacun 2–5 tools, puis synthétise. Une requête utilisateur = **10 à 30 appels LLM**.
- **Tool calling fiable et obligatoire** : un agent qui échoue à produire un appel de fonction
  structuré est inutilisable. C'est le critère de sélection principal.
- **Latence cumulée** : avec un SLO Supervisor de 20 s (NFR-PERF-007) et jusqu'à 30 appels
  séquentiels ou parallèles, chaque appel dispose d'un budget de quelques centaines de millisecondes.
- **Contrainte budgétaire absolue** : aucun budget alloué (PRD C2/C3). Le projet doit fonctionner
  sur un *free tier*.
- **Résilience exigée** : FR-M6-011 et le scénario de chaos « LLM unavailable → fallback →
  continue operation » (PRD §54) imposent explicitement de survivre à la perte du fournisseur.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Fiabilité du tool calling (function calling structuré) | 🔴 **Bloquant** |
| C2 | Coût pour un usage de démonstration soutenu | 🔴 Élevé |
| C3 | Latence par appel (impact direct sur NFR-PERF-006/007) | 🔴 Élevé |
| C4 | Qualité de raisonnement (analyse de cause racine, synthèse) | 🟠 Moyen |
| C5 | Absence de verrouillage fournisseur | 🟠 Moyen |

## Options envisagées

### Option A — Groq (retenue comme primaire)
Inférence sur matériel LPU dédié, servant des modèles à poids ouverts (Llama, GPT-OSS, Kimi, Qwen)
via une **API compatible OpenAI**.

✅ **Latence exceptionnelle** — l'atout décisif : le débit de génération est d'un ordre de grandeur
supérieur aux GPU classiques. Avec 10 à 30 appels par requête, c'est le seul critère qui distingue
une démonstration fluide d'une démonstration pénible (C3)
✅ **Free tier réellement utilisable** pour un projet de démonstration (C2)
✅ Tool calling supporté et fiable sur les modèles récents (`llama-3.3-70b-versatile`, `openai/gpt-oss-120b`, `moonshotai/kimi-k2-instruct`) (C1)
✅ API compatible OpenAI ⇒ intégration `langchain-groq` immédiate, et **bascule de fournisseur triviale** (C5)
✅ Modèles à poids ouverts : le même modèle peut être auto-hébergé en cas de besoin
❌ **Limites de débit sur le free tier** (requêtes/minute, tokens/jour) — contrainte structurante pour un système multi-agent
❌ Pas de mise en cache de prompt aussi aboutie que chez certains concurrents
❌ Raisonnement de très haut niveau en retrait par rapport aux modèles propriétaires de pointe (C4)

### Option B — Gemini (Google)
✅ Free tier généreux, très longue fenêtre de contexte, bon raisonnement
❌ Latence sensiblement supérieure à Groq → pénalise fortement C3 sur des chaînes de 30 appels
❌ Le tool calling a un formalisme propre, moins directement interchangeable

### Option C — Claude (Anthropic)
✅ **Meilleur tool calling de la catégorie**, excellent raisonnement analytique — idéal pour l'analyse de cause racine
❌ Pas de free tier : incompatible avec C2, qui est une contrainte dure du projet
*(Choix naturel si un budget était alloué — voir condition de réexamen.)*

### Option D — OpenAI
✅ Écosystème LangGraph le mieux documenté, tool calling mature
❌ Payant → C2

### Option E — Ollama en local (Llama/Qwen quantisés)
✅ Gratuit, aucune limite de débit, aucune dépendance réseau, confidentialité totale
❌ **Sur 16 GB de RAM partagés avec Kafka, Spark et Trino, c'est irréaliste** : un modèle 7B quantisé occuperait ~5 Go et resterait lent sur CPU
❌ Tool calling nettement moins fiable sur les petits modèles → risque sur C1 (bloquant)
*(Conservé comme fallback de dernier recours, hors exécution simultanée de la stack data.)*

## Comparaison

| Critère | A. Groq | B. Gemini | C. Claude | D. OpenAI | E. Ollama |
|---|---|---|---|---|---|
| C1 Tool calling | ✅✅ | ✅ | ✅✅✅ | ✅✅ | 🔸 |
| C2 Coût | ✅✅ | ✅✅ | ❌ | ❌ | ✅✅✅ |
| C3 Latence | ✅✅✅ | 🔸 | ✅ | ✅ | ❌ |
| C4 Raisonnement | ✅ | ✅✅ | ✅✅✅ | ✅✅ | 🔸 |
| C5 Portabilité | ✅✅ | 🔸 | 🔸 | ✅ | ✅✅ |

## Décision

**Groq comme fournisseur primaire**, accédé **exclusivement** au travers d'une
**LLM Gateway interne** (`libs/telecom-common/llm/`) qui abstrait le fournisseur.

Aucun code de la plateforme n'importe `groq` ni `langchain_groq` directement. Cette contrainte est
vérifiée en CI par une règle d'import.

### Architecture de la gateway

```
                    ┌──────────────────────────────────────┐
   agents/  ───────►│           LLM Gateway                │
   apps/api ───────►│                                      │
                    │  1. Routage par tâche (complexité)   │
                    │  2. Cache sémantique (Redis)         │
                    │  3. Budget & rate limiting           │
                    │  4. Retry + circuit breaker          │
                    │  5. Fallback en cascade              │
                    │  6. Instrumentation (Langfuse/OTel)  │
                    └───────┬──────────┬──────────┬────────┘
                            ▼          ▼          ▼
                         Groq      Gemini      Ollama
                       (primaire) (fallback 1) (fallback 2)
                            │
                    ┌───────▼────────────────────────────┐
                    │  Mode dégradé déterministe          │
                    │  (NFR-AVAIL-006) : réponse issue    │
                    │  des tools, sans synthèse LLM       │
                    └────────────────────────────────────┘
```

### Routage par complexité de tâche

Tous les appels ne requièrent pas le même modèle. Router réduit le coût, la latence **et** la
consommation de quota :

| Type de tâche | Modèle cible | Justification |
|---|---|---|
| Classification d'intention, routage | Petit modèle rapide | Tâche simple, appelée à chaque requête |
| Sélection et appel de tools | Modèle 70B à tool calling fiable | C1 est critique ici |
| Analyse de cause racine, synthèse | Modèle le plus capable disponible | C4 prime sur la latence |
| Reformulation, résumé de document | Petit modèle | Faible exigence de raisonnement |

> ⚙️ Les identifiants de modèles sont **configurables** (`config/llm.yaml`), jamais codés en dur :
> l'offre des fournisseurs évolue plus vite que le code.

## Conséquences

**Positives**
- Le changement de fournisseur est une modification de configuration, pas de code (C5).
- Le fallback en cascade satisfait FR-M6-011 et le scénario de chaos « LLM unavailable » **par conception**, pas après coup.
- Le cache sémantique et le routage par complexité réduisent significativement la consommation de quota — deux questions récurrentes en entretien sur les systèmes agentiques en production.
- L'instrumentation étant centralisée dans la gateway, **aucun appel LLM ne peut échapper à Langfuse**.

**Négatives**
- La gateway est une **abstraction supplémentaire** à concevoir, tester et maintenir. Justifiée uniquement parce qu'elle porte quatre exigences (résilience, budget, observabilité, portabilité) — pas par principe.
- Le dénominateur commun entre fournisseurs limite l'usage de fonctionnalités spécifiques (caching propriétaire, sorties structurées natives) → assumé.
- Le cache sémantique introduit un risque de **réponse périmée** sur des données temps réel → mitigation : le cache porte sur les *étapes de raisonnement*, jamais sur les résultats de tools ; TTL court et clé incluant la fenêtre temporelle.
- La dépendance au free tier reste un **risque de démonstration** (PRD R3) → budget par requête, quotas surveillés, et mode dégradé déterministe testé.

**À surveiller**
- Consommation de quota par jour de démonstration (métrique Prometheus dédiée).
- Taux de succès du tool calling par modèle — un modèle qui régresse sur C1 doit être déclassé.

## Notes d'implémentation

**Configuration** (`config/llm.yaml`, sans aucun secret)
```yaml
providers:
  primary:   { name: groq,   enabled: true }
  fallbacks: [ { name: gemini, enabled: false }, { name: ollama, enabled: false } ]

routing:
  classification: { provider: groq, model: "${LLM_MODEL_SMALL}",     max_tokens: 256 }
  tool_calling:   { provider: groq, model: "${LLM_MODEL_TOOLS}",     max_tokens: 2048 }
  reasoning:      { provider: groq, model: "${LLM_MODEL_REASONING}", max_tokens: 4096 }

budget:
  max_tokens_per_request: 60000
  max_calls_per_request: 30          # aligné sur MAX_ITERATIONS (ADR-008)
  daily_token_budget: 2000000

resilience:
  retry: { max_attempts: 3, backoff: exponential, initial_delay_ms: 500 }
  circuit_breaker: { failure_threshold: 5, reset_timeout_s: 60 }
  timeout_s: 30

cache:
  semantic: { enabled: true, ttl_s: 300, similarity_threshold: 0.95 }
```

**Gestion des secrets** : la clé API provient exclusivement de la variable d'environnement
`GROQ_API_KEY`, injectée via `.env` (ignoré par Git) en développement et via un secret Kubernetes
en production. Un scan CI vérifie l'absence de clé dans l'historique (NFR-SEC-007).

**Règle d'import vérifiée en CI**
```
Seul libs/telecom-common/llm/providers/ peut importer un SDK de fournisseur.
Tout autre import de groq | openai | google.generativeai | anthropic échoue le build.
```

## Références
- [FR-M6-011/016](../product/SRS.md) · [NFR-AVAIL-006](../product/SRS.md) · [PRD §10 R3](../product/PRD.md) · [ADR-008](ADR-008-agent-orchestration.md)
