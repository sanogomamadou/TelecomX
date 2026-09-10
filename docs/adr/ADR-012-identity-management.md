# ADR-012 — Keycloak comme fournisseur d'identité et d'autorisation

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 5 |

## Contexte

La matrice RBAC (SRS §2.2) définit 9 rôles aux permissions nettement différenciées. Cette
autorisation doit être appliquée de manière cohérente sur **quatre plans** :

1. l'**API** (FastAPI) — accès aux endpoints ;
2. les **données** (Trino) — row-level security et masquage de colonnes ;
3. les **tools des agents** — filtrage du catalogue exposé au LLM (FR-M6-007) ;
4. le **frontend** — affichage conditionnel.

L'exigence critique est qu'un `marketing_manager` ne puisse **jamais** invoquer
`restart_network_service()` — et que cette garantie ne repose pas sur la seule discipline du code
applicatif.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Standard OIDC/OAuth2 (jetons vérifiables par tous les composants) | 🔴 Élevé |
| C2 | Gestion des rôles et des groupes | 🔴 Élevé |
| C3 | Multi-tenancy (realms) | 🟠 Moyen |
| C4 | Configuration reproductible (as-code) | 🔴 Élevé |
| C5 | Empreinte ressources | 🟠 Moyen |

## Options envisagées

| Option | Analyse |
|---|---|
| **A — Keycloak** | ✅ Référence open source de l'IAM d'entreprise ✅ OIDC/OAuth2 complet, JWT signés RS256, JWKS exposé ✅ Rôles de realm et de client, groupes, mappers de claims personnalisés ✅ **Realms = isolation multi-tenant native** (C3, cf. [ADR-016](ADR-016-multi-tenancy.md)) ✅ Import de realm au démarrage ⇒ configuration versionnée dans Git (C4) ✅ Compétence recherchée en environnement d'entreprise ❌ JVM : ~700 Mo (C5) ❌ Riche donc complexe ; l'interface d'administration est déroutante au début |
| **B — Authentification maison (JWT + table utilisateurs)** | ✅ Léger, ~0 Mo supplémentaires, contrôle total ❌ **Réinventer un composant de sécurité est une mauvaise pratique majeure** — rotation de clés, révocation, expiration, refresh tokens, verrouillage de compte : autant d'occasions de se tromper ❌ Aucune valeur démonstrative, au contraire |
| **C — Auth0 / Clerk (SaaS)** | ✅ Zéro exploitation, excellente expérience développeur ❌ Service externe payant au-delà d'un seuil → PRD C2 ❌ Dépendance réseau pour une démonstration locale |
| **D — Ory Kratos/Hydra** | ✅ Léger, conception moderne en composants découplés ❌ Plusieurs services à assembler pour couvrir ce que Keycloak fait seul ❌ Moins répandu en entreprise |

## Décision

**Keycloak (Option A)**, avec un realm `telecomx` importé au démarrage depuis un fichier JSON
versionné, et le profil Compose `security` ([ADR-011](ADR-011-local-dev-strategy.md)).

### Application de l'autorisation en défense en profondeur

L'autorisation n'est jamais appliquée à un seul endroit :

| Niveau | Mécanisme | Rôle |
|---|---|---|
| **1. Jeton** | JWT signé, claims `roles`, `tenant_id` | Source de vérité de l'identité |
| **2. API** | Dépendance FastAPI vérifiant signature, audience, expiration, rôle requis | Refus par défaut sur chaque endpoint |
| **3. Tools** | Catalogue **filtré avant** transmission au LLM | Un tool non autorisé est *invisible* du modèle |
| **4. Données** | Règles Trino (row-level, masquage de colonnes) | Le moteur SQL refuse, même si l'API est contournée |
| **5. Audit** | Journalisation de toute décision d'autorisation, y compris les refus | Traçabilité (NFR-SEC-006) |

> 🔑 **Le niveau 3 est le point de conception le plus important de cet ADR.** Filtrer les tools
> *après* que le LLM les a choisis est une mauvaise approche : le modèle propose une action
> interdite, l'utilisateur la voit refusée, l'expérience est mauvaise et la surface d'attaque
> par injection de prompt reste ouverte. Filtrer *avant* signifie que le modèle n'a jamais
> connaissance de l'existence du tool — il ne peut pas être manipulé pour l'invoquer.

## Conséquences

**Positives**
- Standard OIDC ⇒ FastAPI, Trino, Grafana et le frontend valident tous le **même** jeton.
- La configuration du realm (rôles, clients, utilisateurs de démonstration) est dans Git : reproductible et revue comme du code (C4).
- Les realms fournissent une isolation multi-tenant sans code applicatif spécifique.
- Utilisateurs de démonstration pré-provisionnés pour chaque persona → la démonstration peut montrer concrètement les différences de permissions.

**Négatives**
- 700 Mo de RAM sur un budget contraint → cantonné au profil `security`, non démarré durant le développement data.
- En développement hors profil `security`, l'API doit fonctionner sans Keycloak → **mode `AUTH_MODE=dev`** avec jetons signés localement et personas simulés. Ce mode est refusé si `ENV != development` (garde-fou vérifié par test).
- Le démarrage de Keycloak prend 30–60 s → à intégrer aux health checks et à la CI.
- Courbe d'apprentissage de la configuration des mappers de claims.

**À surveiller**
- Aucune fuite du mode `dev` en production : test dédié et vérification au démarrage.
- Rotation des clés de signature et mise en cache du JWKS côté API.

## Notes d'implémentation

**Structure du realm** (`security/keycloak/realm-telecomx.json`)
```
realm: telecomx
├── clients
│   ├── telecomx-frontend   (public, PKCE, redirect vers l'UI)
│   ├── telecomx-api        (bearer-only, audience des jetons)
│   └── telecomx-agents     (confidential, service account pour les agents événementiels)
├── realm roles
│   ├── noc_operator · network_engineer · data_analyst · fraud_analyst
│   ├── marketing_manager · field_engineer · executive · ml_engineer
│   └── platform_admin
├── groups            (mapping organisationnel → rôles)
├── client scopes     (mapper personnalisé : tenant_id, allowed_tool_risk_level)
└── users             (un utilisateur de démonstration par persona U1…U9)
```

**Claims personnalisés du JWT**
```json
{
  "sub": "…", "preferred_username": "awa.noc",
  "realm_access": { "roles": ["noc_operator"] },
  "tenant_id": "telecomx-ml",
  "max_tool_risk": "L3",
  "regions": ["Bamako", "Koulikoro"]
}
```
`max_tool_risk` et `regions` alimentent directement le filtrage du catalogue de tools et la
row-level security Trino — l'autorisation métier voyage dans le jeton.

## Références
- [SRS §2.2](../product/SRS.md) · [FR-M7-002/003](../product/SRS.md) · [NFR-SEC-003/004](../product/SRS.md) · [ADR-016](ADR-016-multi-tenancy.md)
