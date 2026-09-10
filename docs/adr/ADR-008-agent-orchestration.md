# ADR-008 — LangGraph pour l'orchestration multi-agent

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 4 |

## Contexte

La couche agentique doit orchestrer 8 agents (FR-M6-001) avec des exigences qui la distinguent
nettement d'un simple assistant conversationnel :

| Exigence | Implication technique |
|---|---|
| FR-M6-006 — approbation humaine avant action L3+ | **Interruption durable** du flux d'exécution, potentiellement pendant des minutes ou des heures, puis reprise exacte |
| FR-M6-002 — état typé et persistance | L'état doit survivre à un redémarrage de service |
| FR-M6-003 — supervision et délégation | Routage conditionnel entre agents, pas une chaîne linéaire |
| FR-M6-015 — déclenchement par événement | L'entrée peut être une alarme Kafka, sans utilisateur |
| FR-M6-013 — traçabilité complète | Chaque transition d'état observable |
| FR-M6-016 — budget de tours borné | Contrôle explicite des cycles |

L'exigence **FR-M6-006 est structurante** : un agent qui propose `run_backhaul_diagnostic()` doit
s'arrêter, persister son état complet, rendre la main à l'interface, et reprendre exactement où il
s'était arrêté après approbation — éventuellement dans un autre processus.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Interruption/reprise durable (human-in-the-loop) | 🔴 **Bloquant** |
| C2 | État typé et persistant (checkpointing) | 🔴 Élevé |
| C3 | Flux de contrôle explicite (cycles, branchements conditionnels) | 🔴 Élevé |
| C4 | Observabilité et débogage des trajectoires | 🟠 Moyen |
| C5 | Maîtrise du prompt et des appels (pas de magie cachée) | 🟠 Moyen |

## Options envisagées

### Option A — LangGraph
✅ **Modèle de graphe d'états** : nœuds, arêtes conditionnelles, cycles explicites — le flux est du code, pas une émergence du LLM
✅ **`interrupt()` + checkpointer** : mécanisme natif d'interruption durable, exactement ce qu'exige C1
✅ État typé (`TypedDict`/Pydantic) avec réducteurs — persistable en PostgreSQL
✅ Intégration native Langfuse/OTel ; contrôle total des prompts
✅ Time-travel dans l'exécution : rejouer un graphe depuis un état antérieur (utile en évaluation et en débogage)
❌ Courbe d'apprentissage : penser en machine à états plutôt qu'en script
❌ API encore en évolution rapide → épinglage de version strict nécessaire

### Option B — CrewAI
✅ Abstraction très lisible (rôles, tâches, équipages), démarrage rapide
❌ **Pas de mécanisme d'interruption durable de qualité production** → échec sur C1 (bloquant)
❌ Flux de contrôle largement implicite, difficile à contraindre et à auditer

### Option C — AutoGen
✅ Conversation multi-agent riche, patterns de collaboration éprouvés
❌ Orchestration guidée par la conversation ⇒ non déterministe et difficile à borner (C3)
❌ Persistance d'état et reprise non natives (C1)

### Option D — Orchestration maison (machine à états + appels LLM directs)
✅ Contrôle total, aucune dépendance, empreinte minimale
✅ C1/C2/C3 réalisables — mais entièrement à écrire
❌ Réimplémenter checkpointing, reprise, streaming, gestion d'état : plusieurs semaines pour retrouver l'existant
❌ Aucune valeur de démonstration : LangGraph est le standard reconnu de ce domaine

### Option E — Appels directs au SDK du fournisseur (tool-use natif)
✅ Simplicité maximale, latence minimale
❌ Adapté à un agent unique, pas à une supervision multi-agent avec approbation humaine

## Comparaison

| Critère | A. LangGraph | B. CrewAI | C. AutoGen | D. Maison | E. SDK direct |
|---|---|---|---|---|---|
| C1 HITL durable | ✅✅ | ❌ | ❌ | ✅ (à écrire) | ❌ |
| C2 État persistant | ✅✅ | 🔸 | 🔸 | ✅ (à écrire) | ❌ |
| C3 Contrôle explicite | ✅✅ | 🔸 | ❌ | ✅✅ | ✅ |
| C4 Observabilité | ✅✅ | 🔸 | 🔸 | 🔸 | 🔸 |
| C5 Maîtrise du prompt | ✅✅ | ❌ | 🔸 | ✅✅ | ✅✅ |

## Décision

**LangGraph (Option A)**, avec checkpointer PostgreSQL pour la persistance des exécutions.

C1 est éliminatoire et seuls LangGraph et une implémentation maison y répondent. LangGraph fournit
en standard ce qui prendrait des semaines à écrire correctement, tout en laissant le **contrôle
total du flux** : contrairement à CrewAI ou AutoGen, ce n'est pas le LLM qui décide de l'architecture
d'exécution — les branchements sont du code Python auditable.

### Topologie retenue

```
                      START
                        │
                 ┌──────▼──────┐
                 │  Guardrails  │  ← filtrage d'entrée, injection de prompt
                 └──────┬──────┘
                 ┌──────▼──────┐
                 │  Classify   │  ← nature de la demande, domaine, urgence
                 └──────┬──────┘
                 ┌──────▼──────┐
                 │    Plan     │  ← décomposition, choix des agents
                 └──────┬──────┘
        ┌───────────────┼───────────────┬───────────────┐
        ▼               ▼               ▼               ▼
    Network         Customer         Fraud          Revenue    (exécution parallèle)
    Forecast       Maintenance                                  
        └───────────────┼───────────────┴───────────────┘
                 ┌──────▼──────┐
                 │  Synthesize │  ← agrégation, analyse de cause racine
                 └──────┬──────┘
                 ┌──────▼──────┐
                 │  Recommend  │  ← proposition d'action + classe de risque
                 └──────┬──────┘
                        │ action de classe L3+ ?
                 ┌──────▼──────┐
              non│   Decide    │oui
        ┌────────┴─────────────┴────────┐
        ▼                               ▼
   ┌─────────┐                  ┌───────────────┐
   │ Execute │◄────approuvé─────│  interrupt()  │  ⏸️ état persisté
   └────┬────┘                  │  HITL         │
        │                       └───────┬───────┘
   ┌────▼────┐                          │ refusé
   │ Verify  │                          ▼
   └────┬────┘                        END
        └──────────────► END
```

## Conséquences

**Positives**
- Le human-in-the-loop est natif, pas bricolé : `interrupt()` persiste l'état, l'API le restitue, l'approbation reprend l'exécution.
- Le graphe est **testable sans LLM** : en injectant un modèle factice, on valide le routage de manière déterministe.
- La topologie est lisible et défendable en entretien — c'est de l'ingénierie, pas de l'invocation.
- Le checkpointer donne gratuitement l'historique des exécutions, exigé par l'audit (FR-M6-017).

**Négatives**
- **API en évolution rapide** : les versions mineures introduisent des ruptures. → Version épinglée exactement, mise à jour délibérée et testée.
- Verbosité : un graphe d'états demande plus de code qu'un `crew.kickoff()`. C'est le prix du contrôle.
- Le checkpointer PostgreSQL ajoute une dépendance sur le chemin critique des agents → repli documenté sur checkpointer mémoire en mode dégradé.
- Risque de sur-ingénierie du graphe → règle : un nœud a une seule responsabilité, tout branchement est justifié par une exigence.

**À surveiller**
- Croissance de la table de checkpoints (purge des exécutions terminées > 30 j).
- Latence ajoutée par la persistance à chaque transition.

## Notes d'implémentation

**Structure de l'état** (`agents/core/state.py`)
```python
class AgentState(TypedDict):
    # Entrée
    request: str
    trigger: Literal["user", "event", "schedule"]
    user_context: UserContext          # identité, rôles, tenant → filtrage des tools
    # Exécution
    plan: list[PlannedStep]
    tool_calls: Annotated[list[ToolCall], operator.add]   # réducteur : accumulation
    findings: Annotated[list[Finding], operator.add]
    # Décision
    recommendation: Recommendation | None
    approval: ApprovalDecision | None
    # Garde-fous
    token_budget: TokenBudget
    iteration: int                     # borné par MAX_ITERATIONS (FR-M6-016)
    # Traçabilité
    trace_id: str
```

**Règles de conception**
1. Un nœud ne fait **jamais** d'appel de tool implicite : les tools sont invoqués via un `ToolNode` explicite dont les résultats sont enregistrés dans l'état.
2. Le catalogue de tools transmis au LLM est **filtré par `user_context`** avant la construction du graphe (FR-M6-007) : un tool non autorisé est invisible du modèle.
3. Tout nœud appelant un LLM passe par la **LLM Gateway** ([ADR-009](ADR-009-llm-provider.md)) — jamais d'appel direct au SDK.
4. `MAX_ITERATIONS = 12` et budget de tokens par exécution : dépassement ⇒ terminaison propre avec résultat partiel explicite.

## Références
- [FR-M6-001…017](../product/SRS.md) · [PRD §7](../product/PRD.md) · [ADR-009](ADR-009-llm-provider.md) · [ADR-010](ADR-010-vector-store.md)
