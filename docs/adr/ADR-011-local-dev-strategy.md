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
| RAM | 15,6 GB | **~7 GB** (Windows + VS Code + outils de développement consomment ~8,6 GB — *mesuré*, cf. §Révision) |
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
| Spark (master+worker) | 2,3 GB | OTel Collector | 0,15 GB |
| Trino (coord.+worker) | 1,5 GB | Langfuse (+ClickHouse) | 1,5 GB |
| API + agents + frontend | 1,1 GB | OpenMetadata (+ES+MySQL) | 3,0 GB |
| | | **TOTAL** | **~14,0 GB** |

> **Conclusion : ~14 GB nécessaires pour ~7 GB disponibles.** Tout démarrer est physiquement
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
| `lakehouse` | core + Iceberg REST + Spark + Trino | **6,2 GB** | Phases 1-2 : ingestion, transformations, dbt |
| `ml` | core + MLflow + model serving | **2,7 GB** | Phase 3 : entraînement et service de modèles |
| `agents` | core + Qdrant + agent service | **2,8 GB** | Phase 4 : développement des agents |
| `app` | core + API + frontend | **3,2 GB** | Phase 5 : développement applicatif |
| `observability` | Prometheus, Grafana, Loki, OTel, Langfuse | **2,5 GB** | Phase 7 (ajoutable à un autre profil) |
| `security` | Keycloak | **0,8 GB** | Phase 5 : authentification |
| `governance` | OpenMetadata + Elasticsearch + MySQL | **3,2 GB** | Phase 2 : catalogue et lineage (ponctuel) |
| **`demo`** | core + Trino + agents + API + frontend + Prometheus/Grafana | **5,5 GB** | **Scénario bout-en-bout du PRD §8** |

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
- Le profil `lakehouse` (6,2 GB) reste le plus lourd : il ne peut coexister avec `observability` (2,5 GB).
- Le développement multi-couches simultané (ex. Spark **et** agents) impose des arrêts/redémarrages.

**À surveiller**
- Consommation disque des volumes : rétention bronze réduite à 7 jours, `make clean-data` fourni.
- La configuration WSL2 (`.wslconfig`) doit allouer 7 GB, sinon le profil `lakehouse` échoue.

## Notes d'implémentation

**Configuration WSL2 requise** (`%USERPROFILE%\.wslconfig`) :
```ini
[wsl2]
memory=7GB
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

---

## Révision — 2026-10-24

La **décision** (profils Docker Compose) est confirmée par l'usage. Le **dimensionnement** qui
l'accompagnait était faux sur deux points, découverts à la première tentative de démarrage réel.
Les chiffres du corps de cet ADR ont été corrigés ; cette section conserve la trace de l'erreur.

### Erreur 1 — budget hôte surestimé

| | Estimation initiale | Mesure réelle |
|---|---|---|
| RAM disponible pour Docker | ~8–10 GB | **~7 GB** |
| Allocation `.wslconfig` | `memory=10GB` | **`memory=7GB`** |

L'hypothèse « Windows + IDE consomment ~5 GB » était optimiste. Mesure sur l'environnement cible,
Docker arrêté et navigateur fermé :

| Application | RAM |
|---|---|
| VS Code (32 processus) | **5,74 GB** |
| Outils annexes (kilo, ChatGPT, webview) | 2,78 GB |
| Windows (svchost, Defender) | 1,63 GB |
| Autres outils de développement | 0,89 GB |

VS Code seul consomme davantage que ce qui avait été budgété pour tout Windows. Avec
`memory=10GB`, WSL ne pouvait pas obtenir son allocation : la distro démarrait en état
zombie (`Running` mais ne répondant pas, `vmmemWSL` à 40 Mo), et le moteur Docker renvoyait
`Internal Server Error` sur chaque requête.

### Erreur 2 — Spark et Trino surdimensionnés

Spark avait été dimensionné sur le débit de **production** (2 475 msg/s) alors que
l'environnement de développement tourne à `SPEED_FACTOR=0.001`, soit **~2,5 msg/s** —
**mille fois moins**. Allouer 4 Go à un executor traitant 2,5 messages par seconde n'avait
aucun fondement. *(Mesuré au lot 1.4 sur le générateur réel : ~4 msg/s de trafic transactionnel, auxquels s'ajoutent ~2 msg/s de flux périodiques `network.metrics` et `device.events`, soit ~6 msg/s au total. L'ordre de grandeur et la conclusion sont inchangés.)*

| Service | Avant | Après | Configuration |
|---|---|---|---|
| Spark | 4,0 GB | **2,3 GB** | driver 768 Mo + 1 executor 1,5 Go |
| Trino | 2,0 GB | **1,5 GB** | coordinateur + 1 worker |
| Profil `lakehouse` | 8,4 GB | **6,2 GB** | |
| Profil `demo` | 7,6 GB | **5,5 GB** | |
| Inventaire total | ~16,2 GB | **~14,0 GB** | |

### Effet

Le profil `lakehouse` (6,2 GB) tient désormais dans les 7 GB alloués **sans fermer VS Code**,
ce qui supprime la contrainte d'exploitation la plus pénible de la version initiale. Le
découplage temporel décrit au §Décision (préparation sous `lakehouse`, démonstration sous `demo`)
reste valable et devient confortable.

### Enseignement

> Un budget de ressources estimé n'est qu'une hypothèse. Celui-ci a tenu une phase entière avant
> d'être confronté au réel, et s'est révélé faux dans les deux sens à la fois : trop optimiste sur
> l'hôte, trop pessimiste sur les services. **Mesurer avant de dimensionner** — et, lorsque la
> charge de développement est mille fois inférieure à celle de production, dimensionner pour la
> charge de développement, pas pour la production.

---

## Références
- [PRD §9.1 C1](../product/PRD.md) · [NFR-ENV-001](../product/SRS.md) · [ADR-002](ADR-002-event-backbone.md) · [ADR-005](ADR-005-stream-processing.md)
