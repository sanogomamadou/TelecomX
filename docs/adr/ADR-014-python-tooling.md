# ADR-014 — uv + ruff + mypy comme chaîne d'outils Python

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 0 |

## Contexte

Le monorepo comporte plusieurs composants Python aux dépendances **partiellement incompatibles** :

| Composant | Contrainte notable |
|---|---|
| `data-platform/generators` | Léger : `confluent-kafka`, `faker`, `pydantic` |
| `data-platform/spark` | **PySpark impose une version de Python bornée** |
| `ml-platform` | `lightgbm`, `xgboost`, `scikit-learn`, `mlflow` |
| `agents` | `langgraph`, `langchain-*`, clients LLM — écosystème à évolution rapide |
| `apps/api` | `fastapi`, `uvicorn`, `sqlalchemy` |

⚠️ **Contrainte détectée sur l'environnement cible** : la machine dispose de **Python 3.13**, or
PySpark ne supporte officiellement pas encore cette version. Les jobs Spark s'exécutant en
conteneur, ce n'est pas bloquant — mais cela impose une règle : **la version de Python est
déclarée et verrouillée par composant**, jamais héritée implicitement de la machine.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Environnements isolés par composant avec versions Python distinctes | 🔴 Élevé |
| C2 | Builds reproductibles (verrouillage des dépendances) | 🔴 Élevé |
| C3 | Vitesse (impact direct sur la durée de CI, NFR-MAINT-007 < 15 min) | 🟠 Moyen |
| C4 | Qualité : lint, format, typage statique | 🔴 Élevé |

## Décision

| Besoin | Outil retenu | Alternative écartée |
|---|---|---|
| Gestion de paquets et environnements | **uv** | Poetry (plus lent), pip-tools (moins intégré), conda (lourd) |
| Lint + formatage | **ruff** | flake8 + black + isort + pyupgrade (4 outils, ~50× plus lent) |
| Typage statique | **mypy** (`--strict` sur `libs/`) | pyright (excellent mais dépendance Node) |
| Tests | **pytest** + pytest-cov + pytest-asyncio | unittest (trop verbeux) |
| Validation de données | **Pydantic v2** | dataclasses (pas de validation), attrs |
| Hooks de pré-commit | **pre-commit** | scripts Git manuels |
| Règles d'architecture | **import-linter** | revue manuelle (non fiable) |

### Pourquoi `uv`

`uv` (écrit en Rust) résout l'installation de dépendances 10 à 100 fois plus vite que pip/Poetry,
gère les **espaces de travail** (workspaces) — donc plusieurs composants avec des dépendances
distinctes dans un seul dépôt, exactement le besoin C1 — et **installe lui-même les versions de
Python** requises, ce qui règle proprement le problème PySpark/Python 3.13.

### Pourquoi `ruff`

Un seul outil remplace flake8, black, isort, pyupgrade, bandit (partiellement) et pydocstyle,
avec une exécution quasi instantanée. Sur un monorepo, la différence entre un lint de 2 s et de
90 s détermine si les développeurs l'exécutent avant chaque commit ou l'évitent.

### Pourquoi `mypy --strict` uniquement sur `libs/`

Le typage strict est coûteux là où il apporte peu (scripts d'exploration, notebooks) et
extrêmement rentable là où le code est partagé. `libs/telecom-common` étant la source de vérité
des types métier et des contrats ([ADR-001](ADR-001-monorepo.md)), une erreur de type s'y propage
partout. Ailleurs, mypy s'applique en mode standard.

## Conséquences

**Positives**
- Chaque composant déclare sa propre version de Python et ses dépendances : les conflits deviennent impossibles.
- Un seul fichier de verrouillage garantit des builds identiques en local, en CI et en image Docker (C2).
- La chaîne complète de qualité s'exécute en quelques secondes → adoption réelle des hooks pre-commit.
- `import-linter` transforme les règles d'architecture d'ADR-001 en contraintes **vérifiées**, pas en intentions.

**Négatives**
- `uv` est un outil récent : l'écosystème est moins documenté que Poetry et l'API évolue → version épinglée dans la CI.
- Les espaces de travail multi-composants demandent une organisation rigoureuse des `pyproject.toml`.
- `mypy --strict` sur du code manipulant des DataFrames Spark ou des dictionnaires LLM exige des annotations parfois lourdes → périmètre volontairement limité à `libs/`.

## Notes d'implémentation

**Organisation des espaces de travail**
```
pyproject.toml                      # racine : workspace uv + configuration ruff/mypy partagée
├── libs/telecom-common/            # py>=3.11  — mypy --strict, couverture ≥ 90 %
├── data-platform/generators/       # py>=3.11
├── data-platform/spark/            # py==3.11  ← version bornée par PySpark
├── ml-platform/                    # py>=3.11
├── agents/                         # py>=3.11
└── apps/api/                       # py>=3.11
```

**Configuration ruff** (extraits significatifs)
```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E","W","F","I","N","UP","B","A","C4","DTZ","T20","SIM","ARG","PTH","PD","RUF","S"]
# DTZ : interdit les datetime naïfs — critique sur une plateforme temporelle
# S   : règles de sécurité (bandit)
# T20 : interdit les print() en dehors des scripts
ignore = ["S101"]   # assert autorisé dans les tests
```

> 🕐 **La règle `DTZ` est délibérément activée.** Sur une plateforme dont tout repose sur des
> horodatages (fenêtres de streaming, watermarks, partitionnement, séries temporelles),
> un `datetime.now()` sans fuseau est une source de bugs silencieux et coûteux.
> **Convention : tous les horodatages sont en UTC, avec fuseau explicite, du producteur au stockage.**
> La conversion en heure locale (UTC+0 pour le Mali) n'a lieu qu'à l'affichage.

**Règles d'architecture** (`.importlinter`)
```ini
[importlinter:contract:layers]
name = Architecture en couches
type = layers
layers =
    apps
    agents
    ml-platform
    data-platform
    libs
# Une couche ne peut importer que les couches inférieures.

[importlinter:contract:llm-isolation]
name = Isolation des SDK LLM (ADR-009)
type = forbidden
source_modules = agents, apps, ml_platform
forbidden_modules = groq, openai, anthropic, google.generativeai
# Seul libs.telecom_common.llm.providers est autorisé.
```

## Références
- [NFR-MAINT-001…005](../product/SRS.md) · [ADR-001](ADR-001-monorepo.md) · [ADR-009](ADR-009-llm-provider.md)
