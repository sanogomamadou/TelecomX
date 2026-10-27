# Seeds : données de référence synthétiques

> ⚠️ **Entièrement fictif.** Aucun site, aucune cellule, aucun équipement de ces fichiers ne
> correspond à un élément réel. Les coordonnées sont des points aléatoires autour du centre
> approximatif d'une région ; les constructeurs sont inventés ; les parts régionales sont des
> ordres de grandeur choisis pour obtenir un réseau plausible, pas des statistiques.

## Ces fichiers sont générés. Ne pas les éditer.

```
python -m telecomx_generators.reference          # make refdata
python -m telecomx_generators.reference --check  # CI : échoue si les fichiers ont dérivé
```

Le générateur est **déterministe** (graine 42) : il produit toujours exactement les mêmes octets.
La CI le régénère en mémoire et compare avec ce qui est commité, donc une modification du
générateur sans régénération ne peut pas être fusionnée. Voir [ADR-018](../../../docs/adr/ADR-018-reference-data.md).

Pour changer le réseau synthétique : modifier `data-platform/generators/`, lancer `make refdata`,
commiter le code **et** les fichiers ensemble.

## Contenu

| Fichier | Lignes | Portée | Clé | Rôle |
|---|---|---|---|---|
| `tenants.csv` | 3 | globale | `tenant_id` | Opérateurs servis ([ADR-016](../../../docs/adr/ADR-016-multi-tenancy.md)) |
| `technologies.csv` | 4 | globale | `technology` | Débit et latence de référence par génération |
| `regions.csv` | 9 | par tenant | `region_code` | Régions, population, abonnés (12,4 M au total) |
| `sites.csv` | 1 610 | par tenant | `site_id` | Sites : type, position, alimentation de secours, **capacité du backhaul** |
| `cells.csv` | 4 821 | par tenant | `cell_id` | Cellules : technologie, **capacité en trafic et en utilisateurs** |
| `equipment.csv` | 8 492 | par tenant | `equipment_id` | BBU, routeurs, batteries, groupes électrogènes… |
| `bundles.csv` | 28 | globale | `bundle_code` | Offres commerciales |
| `alarm_types.csv` | 51 | globale | `alarm_code` | Catalogue d'alarmes et procédure associée (`sop_ref`) |

Les tables « par tenant » portent `tenant_id` en première colonne ; l'identifiant n'y est unique
**qu'au sein d'un tenant**. Seul `telecomx-ml` reçoit un réseau pour l'instant.

## Pourquoi ces fichiers existent

| Ils servent à | Exemple |
|---|---|
| Rendre applicable la règle `in_ref` des contrats | « cette cellule existe-t-elle ? » |
| Rendre applicable la règle `lte_ref` | « `active_users` ≤ capacité de la cellule » |
| Transformer une prévision de trafic en **risque de saturation** | prévu 128 Go/h sur une cellule de 100 Go/h |
| Donner aux générateurs d'événements un réseau cohérent | lot 1.4 |
| Rendre possible le diagnostic d'une congestion du backhaul | capacité du lien comparée à celle des cellules |

## Les « ancres » du scénario de démonstration

Le scénario du PRD cite des objets précis. Ils sont **construits explicitement** (et testés), pas
laissés au hasard :

| Objet | Propriété garantie |
|---|---|
| `CELL-0421` | Cellule 4G du site `SITE-102`, à Bamako, capacité 100 Go/h, 1 200 utilisateurs |
| `SITE-102` | Site macro, backhaul hertzien de **311 Mbps**, trois cellules 4G |
| `CELL-0721` | Cellule 4G à Bamako, capacité 100 Go/h (cible de la prévision de saturation) |
| `ROUTER-082` | Routeur du site `SITE-102` |

Le lien de 311 Mbps est **inférieur** à ce que les trois cellules peuvent demander à pleine charge
(~606 Mbps), donc il peut réellement se congestionner. À charge normale (47 %, le chiffre de
l'exemple du PRD), il tourne déjà à ~92 % : serré par construction, ce qui rend l'incident plausible.

## Utilisation par dbt (lot 1.8)

Ces fichiers sont des *seeds* dbt, chargés dans le schéma `ref`. Le projet dbt n'existe pas encore :
il arrive au lot 1.8, avec `seeds: +schema: ref` et les types de colonnes.
