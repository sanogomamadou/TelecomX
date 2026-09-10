# ADR-011 — Profils Docker Compose pour tenir dans 16 GB de RAM

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 |
| **À réexaminer si** | l'environnement de développement dépasse 32 GB de RAM |

## Contexte

La plateforme compte **une vingtaine de services**. L'environnement de développement dispose de :

| Ressource | Valeur | Disponible pour Docker |
|---|---|---|
| RAM | 15,6 GB | ~8–10 GB (Windows + WSL2 + IDE consomment le reste) |
| CPU | 12 vCPU (i5-1235U) | ~8 vCPU |
| Disque | 54 GB libres | ~35 GB (images + volumes) |

Un inventaire honnête des empreintes mémoire montre l'impossibilité de tout démarrer :

| Service | RAM | Service | RAM |
|---|---|---|---|
| Kafka (KRaft) | 1,0 GB | MLflow | 0,3 GB |
| Schema Registry | 0,4 GB | Qdrant | 0,3 GB |
| MinIO | 0,3 GB | Keycloak | 0,7 GB |
| PostgreSQL | 0,3 GB | Prometheus | 0,3 GB |
| Redis | 0,1 GB | Grafana | 0,2 GB |
| Iceberg REST catalog | 0,3 GB | Loki | 0,2 GB |
| Spark (master+worker) | 4,0 GB | OTel Collector | 0,15 GB |
| Trino (coord.+worker) | 2,0 GB | Langfuse (+ClickHouse) | 1,5 GB |
| API + agents + frontend | 1,1 GB | OpenMetadata (+ES+MySQL) | 3,0 GB |
| | | **TOTAL** | **~16,2 GB** |

> **Conclusion : ~16 GB nécessaires pour ~9 GB disponibles.** Tout démarrer est physiquement
> impossible. Ce n'est pas un détail d'exploitation, c'est une **contrainte d'architecture**
> qui doit être traitée par conception.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Permettre de travailler sur n'importe quelle phase sans saturer la machine | 🔴 Élevé |
| C2 | Permettre au scénario de démonstration bout-en-bout de fonctionner | 🔴 Élevé |
| C3 | Rester fidèle à l'architecture cible (ne pas la dénaturer) | 🔴 Élevé |
| C4 | Simplicité d'usage (une commande) | 🟠 Moyen |

## Options envisagées

| Option | Analyse |
|---|---|
| **A — Profils Docker Compose** | ✅ Démarrage sélectif par groupe de services, un seul fichier de composition, limites mémoire explicites par conteneur ✅ Aucune altération de l'architecture : les services sont les mêmes, seul leur *cycle de vie* est piloté ✅ Natif à Docker Compose, aucun outil supplémentaire ❌ Discipline requise : il faut savoir quel profil correspond à quelle tâche ❌ Les dépendances inter-profils doivent être pensées |
| **B — Remplacer les composants lourds par des équivalents légers** (Redpanda, DuckDB, pas de Spark) | ✅ Tout tiendrait en mémoire simultanément ❌ **Dénature l'architecture** : le projet ne démontrerait plus Kafka, Spark ni Trino — c'est-à-dire l'essentiel de sa valeur (C3, éliminatoire) |
| **C — Machine virtuelle cloud** | ✅ Aucune contrainte de ressources ❌ Coût récurrent → interdit par PRD C2 |
| **D — Kubernetes local (kind/minikube) avec autoscaling** | ✅ Plus proche de la production ❌ Surcoût de ressources de l'orchestrateur lui-même, sans résoudre le problème de fond ❌ Boucle de développement bien plus lente qu'avec Compose *(retenu néanmoins en Phase 6 pour valider les manifestes, pas pour le développement quotidien)* |

## Décision

**Profils Docker Compose (Option A)**, avec **limites mémoire déclarées sur chaque conteneur**
et un budget documenté par profil.

Le principe : les profils **composent**. `lakehouse` inclut `core` ; on ne démarre jamais un
profil isolé qui manquerait de ses dépendances.

### Profils définis

| Profil | Services | RAM | Usage |
|---|---|---|---|
| `core` | Kafka, Schema Registry, MinIO, PostgreSQL, Redis | **2,1 GB** | Socle — inclus par tous les autres |
| `lakehouse` | core + Iceberg REST + Spark + Trino | **8,4 GB** | Phases 1-2 : ingestion, transformations, dbt |
| `ml` | core + MLflow + model serving | **2,7 GB** | Phase 3 : entraînement et service de modèles |
| `agents` | core + Qdrant + agent service | **2,8 GB** | Phase 4 : développement des agents |
| `app` | core + API + frontend | **3,2 GB** | Phase 5 : développement applicatif |
| `observability` | Prometheus, Grafana, Loki, OTel, Langfuse | **2,5 GB** | Phase 7 (ajoutable à un autre profil) |
| `security` | Keycloak | **0,8 GB** | Phase 5 : authentification |
| `governance` | OpenMetadata + Elasticsearch + MySQL | **3,2 GB** | Phase 2 : catalogue et lineage (ponctuel) |
| **`demo`** | core + Trino + agents + API + frontend + Prometheus/Grafana | **7,6 GB** | **Scénario bout-en-bout du PRD §8** |

### Résolution de la contrainte C2 (le profil `demo`)

Le scénario de démonstration exige Kafka **et** Spark **et** Trino **et** les agents **et** l'UI —
soit 12+ GB en démarrage simultané. La solution est un **découplage temporel** :

```
Étape 1 (préparation, hors démo)    profil lakehouse
        Spark alimente le Lakehouse en données historiques  →  puis Spark est ARRÊTÉ
Étape 2 (démo en direct)            profil demo
        Le détecteur d'anomalie temps réel est un consommateur Kafka Python léger (~200 Mo),
        et non un job Spark. Trino sert les requêtes des agents sur les données déjà présentes.
```

> 🎯 Ce découplage n'est **pas un artifice de démonstration** : il correspond à une pratique
> réelle. Une détection d'anomalie sur flux à faible latence n'a pas besoin d'un cluster Spark ;
> un consommateur dédié appelant un modèle servi est plus léger, plus rapide et plus simple
> à exploiter. Spark reste l'outil du traitement lourd et du batch.

## Conséquences

**Positives**
- Toutes les phases restent développables sur la machine cible sans compromis d'architecture.
- Les limites mémoire explicites révèlent les fuites et les mauvais dimensionnements dès le développement.
- Le découplage détection temps réel / traitement lourd est une **meilleure architecture** que le tout-Spark initialement envisagé.
- Les mêmes profils servent en CI pour ne démarrer que les services requis par chaque suite de tests.

**Négatives**
- **Discipline permanente** : un `docker compose up` sans profil ne démarre rien d'utile → mitigé par un `Makefile` explicite (`make up-lakehouse`, `make demo`).
- Certains tests d'intégration ont besoin de plusieurs profils simultanément → identifiés, et exécutés sur runner CI (plus de RAM) plutôt qu'en local.
- Le profil `lakehouse` (8,4 GB) est à la limite : il ne peut coexister avec `observability`.
- Le développement multi-couches simultané (ex. Spark **et** agents) impose des arrêts/redémarrages.

**À surveiller**
- Consommation disque des volumes : rétention bronze réduite à 7 jours, `make clean-data` fourni.
- La configuration WSL2 (`.wslconfig`) doit allouer au moins 10 GB, sinon le profil `lakehouse` échoue.

## Notes d'implémentation

**Configuration WSL2 requise** (`%USERPROFILE%\.wslconfig`) :
```ini
[wsl2]
memory=10GB
processors=8
swap=4GB
```

**Limites par conteneur** — systématiques, jamais implicites :
```yaml
services:
  kafka:
    profiles: ["core", "lakehouse", "ml", "agents", "app", "demo"]
    deploy:
      resources:
        limits:   { memory: 1G,   cpus: "2" }
        reservations: { memory: 512M }
    environment:
      KAFKA_HEAP_OPTS: "-Xmx512m -Xms512m"    # le heap JVM DOIT être inférieur à la limite conteneur
```

> ⚠️ **Piège classique** : fixer une limite mémoire de conteneur sans borner le heap JVM provoque
> un `OOMKilled` silencieux — la JVM tente de grossir jusqu'à la mémoire *de l'hôte*, pas celle du
> conteneur. Chaque service JVM (Kafka, Schema Registry, Trino, Spark, Keycloak) doit avoir son
> `-Xmx` explicitement fixé à ~70 % de la limite du conteneur.

**Interface Makefile**
```
make up-core            make up-lakehouse       make up-ml
make up-agents          make up-app             make up-obs
make demo               # orchestre le scénario complet
make down               make clean-data         make ps-mem   # RAM consommée par service
```

## Références
- [PRD §9.1 C1](../product/PRD.md) · [NFR-ENV-001](../product/SRS.md) · [ADR-002](ADR-002-event-backbone.md) · [ADR-005](ADR-005-stream-processing.md)
