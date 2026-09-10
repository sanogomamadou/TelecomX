# ADR-001 — Monorepo plutôt que dépôts multiples

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 0 |

## Contexte

La plateforme comprend dix modules hétérogènes : générateurs Python, jobs Spark, projet dbt,
modèles ML, agents LangGraph, API FastAPI, frontend Next.js, manifestes Kubernetes, charts Helm,
code Terraform. Ces composants partagent des artefacts transverses critiques :

- les **schémas d'événements** (utilisés par les générateurs, Spark, dbt, les modèles et les tools) ;
- les **définitions de features** (partagées entre entraînement et inférence — FR-M5-008) ;
- les **types métier** (cellule, site, abonné, incident).

Une divergence entre ces définitions produit exactement les bugs les plus coûteux du domaine :
train/serving skew, désynchronisation de contrat entre producteur et consommateur.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Cohérence atomique des changements transverses | 🔴 Élevé |
| C2 | Simplicité de mise en route pour un nouvel arrivant | 🔴 Élevé |
| C3 | Lisibilité en tant que portfolio d'ingénierie | 🟠 Moyen |
| C4 | Indépendance de déploiement des services | 🟠 Moyen |
| C5 | Durée de CI | 🟠 Moyen |

## Options envisagées

### Option A — Monorepo
Tout le code dans un dépôt unique, avec bibliothèques partagées internes.

✅ Un changement de contrat + ses consommateurs tiennent dans **un seul commit atomique**
✅ Refactoring transverse trivial, aucune gestion de versions internes
✅ Mise en route en une commande ; vision d'ensemble immédiate
❌ CI potentiellement lente si non filtrée par chemins
❌ Droits d'accès non granulaires (non bloquant ici : auteur unique)

### Option B — Polyrepo (un dépôt par module)
✅ Frontières d'équipe nettes, CI courte par dépôt, déploiement indépendant
❌ Un changement de schéma exige N pull requests coordonnées
❌ Nécessite un registre d'artefacts privé pour les libs partagées — surcoût d'infrastructure réel
❌ Pour un portfolio, le projet apparaît fragmenté et difficile à évaluer

### Option C — Monorepo avec outillage dédié (Bazel, Nx, Pants)
✅ Builds incrémentaux fins, gestion de graphe de dépendances
❌ Coût d'apprentissage et de maintenance disproportionné ici
❌ Bazel sur une stack Python + Spark + Next.js + Terraform mixte est notoirement laborieux

## Comparaison

| Critère | A. Monorepo | B. Polyrepo | C. Monorepo + Bazel |
|---|---|---|---|
| C1 Cohérence atomique | ✅✅ | ❌ | ✅✅ |
| C2 Mise en route | ✅✅ | ❌ | 🔸 |
| C3 Lisibilité portfolio | ✅✅ | 🔸 | 🔸 |
| C4 Déploiement indépendant | 🔸 (via images séparées) | ✅✅ | ✅ |
| C5 Durée CI | 🔸 (mitigeable) | ✅ | ✅✅ |

## Décision

**Monorepo (Option A)**, structuré par domaine fonctionnel, avec des bibliothèques partagées
sous `libs/`, et une CI filtrée par chemins (`paths-filter`) pour ne rejouer que les jobs impactés.

Le critère décisif est **C1** : dans une plateforme data, le contrat de données est l'interface la
plus fragile. Le monorepo transforme « coordonner 4 dépôts » en « un commit qui passe ou échoue ».
C4 est préservé malgré tout : chaque service produit sa propre image Docker et se déploie
indépendamment — le monorepo est une frontière de *source*, pas de *déploiement*.

## Conséquences

**Positives**
- Un changement de schéma casse la CI immédiatement chez tous ses consommateurs — le défaut est détecté à l'écriture, pas en production.
- `libs/telecom-common` devient la source unique de vérité des types métier et des définitions de features.
- Onboarding : `git clone && make setup && make up`.

**Négatives**
- L'historique Git mélange les domaines → **mitigation** : Conventional Commits avec *scope* obligatoire (`feat(agents):`, `fix(spark):`).
- Risque de couplage involontaire entre modules → **mitigation** : règles d'import vérifiées en CI (`import-linter`), aucun import direct entre modules applicatifs, uniquement via `libs/`.
- CI globale plus longue → **mitigation** : filtrage par chemins + cache de dépendances.

**À surveiller**
- Si le dépôt dépasse ~500 Mo ou si la CI dépasse 15 min (NFR-MAINT-007), réexaminer le filtrage.

## Notes d'implémentation

```
libs/telecom-common/       # types métier, schémas, définitions de features, utilitaires PII
├── schemas/               # source de vérité des contrats (Avro + Pydantic générés)
├── domain/                # entités : Cell, Site, Subscriber, Incident
├── features/              # définitions partagées entraînement/inférence
└── security/              # tokenisation, masquage
```

Règle d'architecture appliquée en CI :
`apps/*`, `agents/*`, `ml-platform/*` peuvent importer `libs/*` — jamais l'inverse, jamais entre eux.

## Références
- [PRD §6.1](../product/PRD.md) · [FR-M5-008](../product/SRS.md)
