# ADR-016 — Multi-tenancy par isolation logique avec `tenant_id`

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 0 (conception) / 5 (application) |
| **À réexaminer si** | une contrainte réglementaire impose une isolation physique |

## Contexte

Le PRD §41 identifie la multi-tenancy comme une capacité démontrant l'aptitude à concevoir une
plateforme SaaS scalable. Deux usages réalistes la justifient :

1. **Groupe multi-pays** — un opérateur présent dans plusieurs pays (TelecomX Mali, Sénégal,
   Burkina) souhaite mutualiser la plateforme tout en cloisonnant strictement les données,
   chaque pays ayant son propre régulateur.
2. **Séparation d'environnements** — production, pré-production et bac à sable partageant la
   même infrastructure.

La difficulté : **une décision de multi-tenancy prise après coup est extrêmement coûteuse.** Ajouter
un `tenant_id` à des tables de plusieurs milliards de lignes, à des topics Kafka en production et à
des modèles ML déjà entraînés relève de la réécriture. La décision doit donc être prise en Phase 0,
même si l'implémentation complète intervient plus tard.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Force de l'isolation des données | 🔴 Élevé |
| C2 | Coût en ressources (contrainte 16 GB) | 🔴 Élevé |
| C3 | Complexité opérationnelle | 🟠 Moyen |
| C4 | Réversibilité (possibilité de durcir plus tard) | 🟠 Moyen |

## Options envisagées

| Option | Analyse |
|---|---|
| **A — Isolation logique** (`tenant_id` sur chaque entité, filtrage appliqué à tous les niveaux) | ✅ Une seule instance de chaque service ⇒ coût de ressources constant (C2) ✅ Exploitation simple : un déploiement, une supervision (C3) ✅ Élasticité : ajouter un tenant est une opération de configuration ❌ **L'isolation dépend de la rigueur du code** : un `WHERE tenant_id = ?` oublié provoque une fuite inter-tenant (C1) ❌ Voisin bruyant : un tenant intensif dégrade les autres |
| **B — Isolation physique** (une instance complète de la stack par tenant) | ✅ Isolation absolue, y compris en cas de bug applicatif (C1) ✅ Facturation et dimensionnement par tenant naturels ❌ **Coût multiplié par le nombre de tenants** → strictement impossible sur 16 GB (C2, éliminatoire) ❌ N déploiements à exploiter et mettre à jour (C3) |
| **C — Hybride** (calcul mutualisé, stockage séparé par tenant) | ✅ Bon compromis isolation/coût, séparation des données au repos ✅ Modèle courant en SaaS d'entreprise ❌ Complexité de routage : chaque composant doit résoudre le stockage cible ❌ Coût de stockage supérieur, mutualisation partielle |

## Décision

**Isolation logique (Option A)**, avec un `tenant_id` **obligatoire et appliqué à quatre niveaux**,
et une conception qui préserve la possibilité de migrer vers le modèle hybride (C4).

### Propagation du `tenant_id`

| Niveau | Mécanisme |
|---|---|
| **Événements** | Champ `tenant_id` **obligatoire** dans chaque contrat Avro ([ADR-015](ADR-015-data-contracts.md)) — un message sans tenant est rejeté en DLQ |
| **Kafka** | Un seul jeu de topics, `tenant_id` dans la charge utile *(alternative documentée : préfixage par tenant si l'isolation de débit devient nécessaire)* |
| **Lakehouse** | `tenant_id` en **première colonne de partitionnement** des tables Iceberg — l'élagage de partitions rend le filtrage à la fois sûr et performant |
| **Requêtes** | Row-level security Trino à partir du claim `tenant_id` du JWT ([ADR-012](ADR-012-identity-management.md)) |
| **Identité** | Un **realm Keycloak par tenant** — l'isolation des identités est physique même si celle des données est logique |
| **Agents** | `tenant_id` dans `AgentState.user_context` ; tout tool le reçoit et l'applique obligatoirement |
| **ML** | Modèles entraînés et versionnés par tenant (un modèle de trafic malien ne prédit pas le trafic sénégalais) |

### Le point de conception décisif

Le risque C1 (un filtre oublié) ne se traite pas par de la discipline mais par de la **conception** :

```python
# ❌ INTERDIT — le tenant est un paramètre parmi d'autres, donc oubliable
def get_cell_metrics(cell_id: str, tenant_id: str) -> DataFrame: ...

# ✅ IMPOSÉ — le tenant est porté par le contexte, injecté par le framework,
#             et le repository ne peut PAS construire de requête sans lui
class TenantScopedRepository:
    def __init__(self, ctx: TenantContext) -> None:  # ctx provient du JWT vérifié
        self._tenant = ctx.tenant_id
    def get_cell_metrics(self, cell_id: str) -> DataFrame:
        return self._query(..., tenant=self._tenant)   # filtre systématique, non contournable
```

**Aucun accès direct au client Trino ou à la table Iceberg n'est autorisé depuis le code métier** —
règle vérifiée par `import-linter` ([ADR-014](ADR-014-python-tooling.md)) et par un test
d'intégration dédié qui tente une fuite inter-tenant et vérifie qu'elle échoue.

## Conséquences

**Positives**
- Coût de ressources constant, compatible avec la contrainte matérielle.
- L'ajout d'un tenant est une opération de configuration (realm + seed de référentiel), sans déploiement.
- Le `tenant_id` en tête de partitionnement Iceberg fait de l'isolation un gain de performance plutôt qu'un surcoût.
- Migration vers le modèle hybride possible sans changement de modèle de données : il suffirait de router le stockage par tenant.

**Négatives**
- **L'isolation repose sur le code** : c'est le risque principal, assumé et traité par le pattern `TenantScopedRepository` + tests de fuite automatisés. Ce n'est pas équivalent à une isolation physique et **ne conviendrait pas à un contexte réglementaire imposant la séparation** (condition de réexamen).
- Voisin bruyant non traité en v1 : pas de quota par tenant → documenté comme limite connue.
- Toute entité porte une colonne supplémentaire, y compris en mono-tenant (léger surcoût de stockage assumé pour la réversibilité).
- Complexité accrue des tests : chaque test métier doit exister en variante multi-tenant.

**À surveiller**
- Test de fuite inter-tenant exécuté à chaque build (non négociable).
- Répartition du volume par tenant, pour anticiper le besoin d'isolation de débit.

## Notes d'implémentation

**Tenants de référence pour le développement**
```
telecomx-ml   TelecomX Mali      (tenant principal, jeu de données complet)
telecomx-sn   TelecomX Sénégal   (tenant secondaire, volume réduit — sert aux tests d'isolation)
telecomx-dev  Bac à sable        (données de test, non représentatif)
```

**Partitionnement Iceberg**
```sql
PARTITIONED BY (tenant_id, days(event_timestamp), region)
```

**Test d'isolation obligatoire** (`tests/integration/test_tenant_isolation.py`)
```
Pour chaque endpoint API, chaque tool d'agent et chaque modèle dbt :
  authentifié en tant que tenant A, tenter d'accéder à une ressource du tenant B
  → attendu : 404 (et non 403, qui confirmerait l'existence de la ressource)
  → et : aucune ligne du tenant B ne figure dans le résultat
```

> 🔒 Le choix de **404 plutôt que 403** est délibéré : un 403 révèle qu'une ressource existe chez
> un autre tenant, ce qui constitue déjà une fuite d'information exploitable pour l'énumération.

## Références
- [PRD §41](../product/PRD.md) · [ADR-012](ADR-012-identity-management.md) · [ADR-015](ADR-015-data-contracts.md) · [Threat Model](../security/threat-model.md)
