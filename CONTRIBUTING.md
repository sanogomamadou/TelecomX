# Contribuer à TelecomX

Ce document fixe les conventions du dépôt. Elles sont **vérifiées automatiquement** chaque fois que
c'est possible : une règle écrite est une suggestion, une règle contrôlée en CI est une contrainte.

> TelecomX est un opérateur **fictif** et toutes les données sont **synthétiques**. Aucune donnée
> personnelle réelle n'est utilisée, à aucun stade.

---

## Démarrage

```powershell
.\make.ps1 setup        # outils, dépendances, hooks   (Linux/macOS : make setup)
.\make.ps1 ci           # toute la chaîne de qualité en local
.\make.ps1 help         # toutes les cibles
```

Prérequis : Docker Desktop, Python (installé par `uv` si absent), Git. Voir le [README](README.md).

---

## Conventions

### Langue
- **Documentation, ADR, runbooks : français.**
- **Code, identifiants, noms de tables/colonnes/topics, messages de commit, docstrings : anglais.**

### Code
- Python ≥ 3.11, `uv` pour les environnements, `ruff` pour lint et format, `mypy --strict` sur `libs/`.
- **Tous les horodatages sont en UTC avec fuseau explicite.** La règle `DTZ` de ruff interdit `datetime.now()` sans fuseau ; utiliser `telecom_common.timeutils.utc_now()`. Voir [ADR-014](docs/adr/ADR-014-python-tooling.md).
- **L'unité fait partie du nom d'une colonne** : `latency_ms`, jamais `latency`.
- La logique métier Spark vit dans `transformations/`, sous forme de **fonctions pures**, testables sans cluster.
- Commits au format **Conventional Commits** avec un *scope* : `feat(spark):`, `fix(agents):`, `docs(adr):`.

### Architecture (règles vérifiées par `import-linter`)
- Les composants (`apps/`, `agents/`, `ml-platform/`, `data-platform/`) importent `libs/` ; **jamais l'inverse, jamais entre eux**.
- **Seul `libs/telecom-common/llm/providers/` peut importer un SDK LLM.** Tout autre import échoue le build ([ADR-009](docs/adr/ADR-009-llm-provider.md)).
- Aucun accès direct à Trino ou aux tables Iceberg depuis le code métier : passer par un dépôt cloisonné par tenant ([ADR-016](docs/adr/ADR-016-multi-tenancy.md)).
- Ajouter une contrainte d'architecture **en même temps que le composant qu'elle protège** : un contrat qui nomme un paquet inexistant ne peut pas s'exécuter.

### Données
- **Tout flux a un contrat Avro** dans `data-platform/schemas/avro/` avant tout code.
- Un champ PII déclare `telecomx.pii` **et** `telecomx.tokenize_as` ; une mesure déclare `telecomx.unit`. Sinon le build échoue.
- La zone **bronze est immuable** : une correction se fait en silver, jamais par réécriture.
- Aucun MSISDN en clair au-delà de l'ingestion ([ADR-013](docs/adr/ADR-013-pii-tokenization.md)).

### Sécurité
- **Aucun secret dans le dépôt** : `.env` (ignoré par Git) ou secrets Kubernetes.
- Le catalogue d'outils exposé à un LLM est **filtré avant** transmission, jamais après.
- Toute action de classe L3 ou supérieure exige une approbation humaine ; l'interruption est un **nœud du graphe**, pas une consigne de prompt.

### Décisions
Toute décision d'architecture structurante donne lieu à un [ADR](docs/adr/README.md) **avant**
l'implémentation. Un ADR accepté ne se réécrit pas : on ajoute une section de révision datée, ou
on en écrit un nouveau qui le remplace.

---

## Fichiers générés : ne jamais les éditer à la main

Deux familles de fichiers sont produites par un générateur déterministe. La CI, le pre-commit et
`make validate` régénèrent en mémoire et comparent **octet par octet** : un fichier périmé ou
retouché ne peut pas être fusionné.

| Si tu modifies… | Lance | À commiter avec |
|---|---|---|
| un contrat `.avsc` | `make codegen` | `libs/telecom-common/src/telecom_common/contracts/generated/` |
| le générateur de référentiels | `make refdata` | `data-platform/dbt/seeds/*.csv` |

Voir [contrats de données](docs/data/contracts/README.md) et [seeds](data-platform/dbt/seeds/README.md).

---

## Contrainte matérielle

La stack complète demande ~14 GB de RAM pour ~7 GB disponibles en développement. **Ne jamais tout
démarrer** : utiliser les profils (`make up-core`, `up-lakehouse`, `up-ml`, `up-agents`, `up-app`,
`up-obs`, `demo`). Budget détaillé : [ADR-011](docs/adr/ADR-011-local-dev-strategy.md).

> Piège récurrent : une limite mémoire de conteneur sans `-Xmx` JVM correspondant provoque un
> `OOMKilled` silencieux. Tout service JVM borne son heap à ~70 % de la limite du conteneur.

---

## Tests

- Les tests accompagnent le code. `make test` lance les tests unitaires avec couverture.
- Un test qui ne sait pas échouer ne prouve rien : en cas de doute, casser volontairement le code
  et vérifier que le test le détecte.
- Un contrôle (règle d'architecture, garde-fou de CI) n'existe que s'il a été vu en train d'échouer
  sur un vrai défaut. Pas de `|| echo ...` qui avale un échec.

---

## Pièges connus

| Piège | Parade |
|---|---|
| `spark.sql.shuffle.partitions` par défaut à 200 | Fixer à 8 en local |
| Petits fichiers Iceberg en streaming micro-batch | Compaction planifiée |
| Heap JVM non borné en conteneur | `-Xmx` explicite sur chaque service JVM |
| Quota LLM du free tier épuisé | Cache sémantique, budget par requête, fallback |
| Un `WHERE tenant_id` oublié | Ne jamais requêter hors du dépôt cloisonné par tenant |
| Fuite temporelle dans les features ML | Découpage chronologique, features strictement antérieures à la cible |
| Évaluation paresseuse d'un générateur Python pendant `list.extend` | Figer les valeurs dépendantes de la liste avant d'étendre |

---

## Où trouver quoi

| Besoin | Document |
|---|---|
| Pourquoi telle technologie ? | [docs/adr/](docs/adr/README.md) |
| Quelle exigence couvre ce code ? | [docs/product/SRS.md](docs/product/SRS.md) |
| Comment sont structurées les données ? | [docs/data/data-dictionary.md](docs/data/data-dictionary.md) |
| Quelles cibles de performance ? | [docs/product/SLO.md](docs/product/SLO.md) |
| Quels risques de sécurité ? | [docs/security/threat-model.md](docs/security/threat-model.md) |
