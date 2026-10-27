# ADR-018 — Données de référence synthétiques, déterministes et versionnées comme seeds

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-10-27 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 (lot 1.3) |

## Contexte

Les événements ne se suffisent pas à eux-mêmes. Un enregistrement `network.metrics` dit
« la cellule `CELL-0421` a 842 utilisateurs actifs » ; encore faut-il savoir **que cette cellule
existe, sur quel site, de quelle technologie, et quelle capacité elle a**. Cette connaissance du
réseau (les *données de référence*) conditionne :

| Besoin | Sans référentiel |
|---|---|
| Règle `in_ref` des contrats (« la cellule existe ») | Inapplicable : aucune liste de cellules valides |
| Règle `lte_ref` (« `active_users` ≤ capacité de la cellule ») | Inapplicable |
| Prévision de saturation (UC-02) | Impossible : prévoir 128 Go/h ne dit rien sans la capacité de la cellule |
| Diagnostic de congestion du backhaul (UC-01) | Impossible : sans la capacité du lien, aucune congestion à constater |
| Générateurs d'événements (lot 1.4) | Aucun réseau cohérent à simuler |

Le scénario de démonstration du PRD cite des objets **précis** : `CELL-0421` sur `SITE-102`,
`ROUTER-082`, `CELL-0721`. Un réseau tiré au hasard ne les contiendrait pas, ni ne leur donnerait
les propriétés qui rendent l'histoire plausible.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | **Reproductibilité** : mêmes données pour tous, à chaque exécution | 🔴 Élevé |
| C2 | Aucune donnée réelle (contrainte du projet) | 🔴 **Bloquant** |
| C3 | Cohérence interne vérifiable (clés, parentés, capacités) | 🔴 Élevé |
| C4 | Le scénario de démonstration doit fonctionner | 🔴 Élevé |
| C5 | Cohérence avec l'architecture existante (seeds dbt, [ADR-007](ADR-007-transformation-layer.md)) | 🟠 Moyen |

## Options envisagées

### Option A — Générateur déterministe, résultat commité comme seeds dbt
Un programme construit le réseau à partir d'une graine fixe ; les CSV produits sont versionnés ; la
CI régénère et compare.

✅ Reproductible à l'octet près (C1) ; relisible en revue (un changement de réseau se voit dans le diff)
✅ Seeds dbt : déjà prévus par ADR-007, sans nouvelle brique (C5)
✅ Les propriétés se **testent** : totaux, parentés, ancres (C3, C4)
❌ ~1,2 Mo de CSV dans le dépôt, dont 760 Ko pour les équipements — à la limite de ce que dbt recommande pour des seeds
❌ Déterminisme valable **pour une version mineure de Python** (cf. conséquences)

### Option B — Génération à l'exécution, aléatoire à chaque démarrage (Faker)
✅ Aucun fichier à maintenir
❌ Deux exécutions donnent deux réseaux : démonstration non reproductible, tests instables (C1)
❌ Impossible de garantir que `CELL-0421` existe, ni ses propriétés (C4)

### Option C — Données ouvertes réelles (OpenCellID, cartes publiques)
✅ Réalisme maximal de la géographie
❌ Ce seraient des **sites réels** : contraire au parti pris « entièrement fictif » (C2)
❌ Aucune capacité, aucun équipement : il faudrait de toute façon les inventer

### Option D — CSV écrits à la main
✅ Contrôle total
❌ 1 610 sites, 4 821 cellules, 8 492 équipements : irréaliste, et aucune cohérence vérifiable (C3)

## Décision

**Option A.** Un générateur déterministe (`telecomx_generators.reference`), dont le résultat est
commité dans `data-platform/dbt/seeds/` et protégé par un contrôle de fraîcheur en CI.

### Principes de construction

1. **Chaque partie du réseau tire d'un flux aléatoire indépendant** (`Random("<graine>:<flux>")`).
   Modifier la façon d'attribuer les cellules ne remélange pas les sites : un changement local
   produit un diff local.
2. **Les totaux du PRD sont exacts** : 1 610 sites, 4 821 cellules (1 205 / 1 412 / 1 908 / 296 par
   technologie), 12,4 M d'abonnés. Une répartition proportionnelle par plus fort reste évite la dérive
   d'un arrondi simple.
3. **Les ancres du scénario sont construites, pas espérées** : `ScenarioAnchors` fixe `CELL-0421`,
   `SITE-102`, `ROUTER-082` et `CELL-0721`, avec les propriétés qui rendent l'incident plausible.
   Elles tiennent **pour toute graine**, vérifié par test.
4. **La date de référence est fixe** (2026-09-01), jamais l'horloge : un âge d'équipement calculé
   depuis la date du jour changerait les données chaque jour.
5. **Le générateur refuse d'émettre une donnée qu'il sait fausse** : le catalogue est validé avant
   toute écriture.

### Portée des tables et multi-tenancy ([ADR-016](ADR-016-multi-tenancy.md))

| Portée | Tables | Clé unique |
|---|---|---|
| **Par tenant** | régions, sites, cellules, équipements | `(tenant_id, clé)` |
| **Globale** | tenants, technologies, forfaits, catalogue d'alarmes | clé seule |

Une cellule d'un tenant **n'existe pas** pour un autre, même avec le même identifiant. Interroger
une table scopée sans tenant est une **erreur**, pas un « introuvable » : répondre « absent » cacherait
un bug de l'appelant, exactement le défaut que le pattern `TenantScopedRepository` cherche à éviter.

Seul `telecomx-ml` reçoit un réseau pour l'instant. Les deux autres tenants existent pour que les
recherches scopées et les tests d'isolation aient quelque chose à refuser ; leur donner un réseau est
différé jusqu'à ces tests.

## Conséquences

**Positives**
- Les règles `in_ref` et `lte_ref` des contrats deviennent **applicables** (`ReferenceCatalog.check_record`).
- Le scénario de démonstration dispose de ses objets, avec des propriétés vérifiées par test.
- Un changement du réseau synthétique se voit en revue, ligne par ligne.
- Le parc de 8 492 équipements est cohérent avec les ~40 msg/s annoncés pour `device.events` (un message toutes les ~3,5 minutes par équipement).

**Négatives**
- **Le déterminisme dépend de la version de Python.** La bibliothèque standard ne promet pas des
  flux aléatoires identiques entre versions mineures. Le dépôt épingle Python 3.11, ce qui suffit ici ;
  une montée de version exigerait de regénérer et de revoir les fichiers. Une génération fondée sur un
  hachage (indépendante de la bibliothèque) lèverait cette limite, au prix d'un code plus lourd.
- **~1,2 Mo de CSV commités**, dont `equipment.csv` (760 Ko). Acceptable pour 8 500 lignes ; au-delà,
  il faudrait charger les référentiels autrement que par seeds.
- **Le réalisme est limité et assumé.** Les parts régionales sont des ordres de grandeur, les positions
  des points aléatoires autour d'un centre, les constructeurs inventés. Le but est un réseau
  plausible pour construire et tester une plateforme, pas un modèle d'opérateur réel.
- Les références de procédures (`sop_ref`) sont des **références en avant** : les documents seront
  rédigés pour le corpus RAG en Phase 4, et un test vérifiera alors que chacune se résout.

**À surveiller**
- La taille de `equipment.csv` si le parc grandit.
- Que les ancres restent valides si un paramètre de profil change (le test les vérifie pour plusieurs graines).

## Notes d'implémentation

```
libs/telecom-common/
├── domain/entities.py         lignes des tables ref.* (Pydantic, immuables, validées)
└── reference/catalog.py       ReferenceCatalog : CSV, cohérence, in_ref / lte_ref

data-platform/generators/
└── src/telecomx_generators/reference/
    ├── profiles.py            hypothèses : régions, forfaits, catalogue d'alarmes (lisibles sans lire l'algorithme)
    ├── build.py               construction (flux aléatoires indépendants, ancres)
    └── cli.py                 make refdata / --check

data-platform/dbt/seeds/*.csv  le résultat, versionné
```

**Cohérence vérifiée par `ReferenceCatalog.validate()`** : clés uniques par tenant, tout site a une
région et au moins une cellule, toute cellule a un site, tout équipement a un site et n'est pas
installé avant la mise en service du site ni entretenu avant son installation, et les valeurs
énumérées (type d'équipement, domaine et sévérité d'alarme, type de forfait) appartiennent aux
énumérations **générées depuis les contrats**, pas à une copie.

## Références
- [PRD §2 et §8](../product/PRD.md) · [ADR-007](ADR-007-transformation-layer.md) · [ADR-015](ADR-015-data-contracts.md) · [ADR-016](ADR-016-multi-tenancy.md) · [Seeds](../../data-platform/dbt/seeds/README.md)
