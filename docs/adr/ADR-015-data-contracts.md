# ADR-015 — Avro + Schema Registry pour les contrats de données

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 2 |

## Contexte

Le PRD §16 pose le problème central : *« Si le système réseau change le schéma v1 → v2, ta
plateforme doit gérer l'évolution. »* C'est le scénario le plus courant et le plus destructeur
d'une plateforme data — un équipementier met à jour un firmware, un champ change, et les pipelines
tombent en cascade… ou pire, produisent silencieusement des données fausses.

Un contrat de données doit garantir quatre choses :

1. **Validation à la production** — un message non conforme ne doit jamais entrer dans le système.
2. **Compatibilité vérifiable** — une évolution de schéma doit être refusée *avant* le déploiement si elle casse les consommateurs.
3. **Source unique de vérité** — le même schéma doit régir le générateur, Kafka, Spark, les tables Iceberg et les modèles Pydantic.
4. **Documentation exploitable** — sémantique, unité, propriétaire, classification de sensibilité.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Vérification automatique de la compatibilité de schéma | 🔴 Élevé |
| C2 | Efficacité de sérialisation (99 M messages/jour) | 🔴 Élevé |
| C3 | Support natif de l'évolution de schéma | 🔴 Élevé |
| C4 | Intégration Kafka + Spark + Iceberg | 🔴 Élevé |
| C5 | Lisibilité et outillage | 🟠 Moyen |

## Options envisagées

| Option | Analyse |
|---|---|
| **A — Avro + Confluent Schema Registry** | ✅ **Conçu précisément pour ce problème** : l'évolution de schéma et les règles de compatibilité sont au cœur du format ✅ Binaire compact : ~40–60 % plus petit que JSON, décisif à 99 M msg/j (C2) ✅ Le schéma n'est pas transporté dans le message (seul un identifiant de 5 octets l'est) ✅ Registry natif Kafka, sérialiseurs Spark, conversion Avro → Iceberg directe (C4) ✅ Règles de compatibilité applicables **côté serveur** : le registry refuse une évolution incompatible (C1) ❌ Binaire donc non lisible sans outil ❌ Un service supplémentaire (~400 Mo JVM) |
| **B — JSON Schema + validation applicative** | ✅ Lisible, universel, excellent outillage ✅ Intégration Pydantic naturelle ❌ Verbeux sur le fil (C2) ❌ **Pas de notion native de compatibilité d'évolution** : il faudrait l'implémenter (C1, C3) |
| **C — Protobuf + Schema Registry** | ✅ Très compact et rapide, typage fort, compatibilité gérée ✅ Excellent en RPC ❌ Intégration Spark/Iceberg moins fluide qu'Avro dans l'écosystème data ❌ Les règles d'évolution Protobuf (numéros de champs) sont plus contraignantes pour de l'analytique |
| **D — Aucun contrat (JSON libre)** | ❌ Élimine C1 et C3 ; c'est exactement le problème à résoudre |

## Décision

**Avro comme format de sérialisation sur Kafka, avec Confluent Schema Registry**, complété par
une **génération de code** vers les autres représentations :

```
            data-platform/schemas/avro/<flux>-v<N>.avsc     ← SOURCE UNIQUE DE VÉRITÉ (versionnée)
                                   │
        ┌──────────────────┬───────┴────────┬──────────────────┐
        ▼                  ▼                ▼                  ▼
  Schema Registry    Modèles Pydantic   Schéma Iceberg   Documentation
  (validation        (générateurs,      (DDL bronze)      (dictionnaire
   à la production)   API, tools)                          de données)
```

Le fichier `.avsc` est écrit **à la main** et revu en pull request : c'est un contrat, il mérite une
discussion explicite. Tout le reste en est **dérivé automatiquement**, ce qui rend la divergence
structurellement impossible.

### Politique de compatibilité

| Zone | Mode registry | Signification |
|---|---|---|
| Topics d'ingestion | **`BACKWARD`** | Un consommateur v2 peut lire des données v1 → on peut déployer les consommateurs avant les producteurs |
| Topics de sortie (alarmes, scores) | **`FULL`** | Compatibilité dans les deux sens : contrat le plus strict, car ces topics ont des consommateurs externes |

**Évolutions autorisées** (compatibilité `BACKWARD`) : ajouter un champ **avec valeur par défaut**,
supprimer un champ **qui avait une valeur par défaut**, élargir un type (int → long, float → double).

**Évolutions interdites** : ajouter un champ obligatoire, renommer un champ (⇒ utiliser des `aliases`),
restreindre un type, changer la sémantique d'un champ sans le renommer.

> 🔬 **Le cas `network.metrics` v1 → v2 du PRD §16** est traité comme test d'acceptation de la Phase 2 :
> ajout du champ `carrier_aggregation_enabled` (booléen, défaut `false`) et élargissement de
> `traffic_mb` de `float` à `double`. Le pipeline doit continuer sans interruption, les données v1
> rester lisibles, et les tables Iceberg évoluer sans réécriture ([ADR-003](ADR-003-table-format.md)).

## Conséquences

**Positives**
- Un message non conforme est rejeté **à la production**, jamais après ingestion (FR-M1-008 → DLQ).
- La CI refuse toute pull request introduisant une évolution incompatible (FR-M4-002) : le problème est détecté à la revue, pas en production à 3 h du matin.
- Gain de volume Kafka et de bande passante significatif (C2).
- La classification de sensibilité PII est **portée par le schéma** (attribut personnalisé sur chaque champ), donc exploitable automatiquement par la tokenisation ([ADR-013](ADR-013-pii-tokenization.md)) et le catalogue.

**Négatives**
- **Le Schema Registry devient un point de défaillance critique** : sans lui, ni production ni consommation. → Cache local de schémas côté clients, health check, sauvegarde du topic `_schemas`, et procédure de restauration en runbook.
- Le débogage exige des outils (`kafka-avro-console-consumer`) : un `cat` sur le topic ne donne rien de lisible. → Script utilitaire `scripts/kafka-peek.py` fourni.
- La génération de code ajoute une étape au build → intégrée à `make codegen` et vérifiée en CI (échec si le code généré n'est pas à jour).
- 400 Mo de RAM dans le profil `core`.

**À surveiller**
- Prolifération de versions de schémas (politique : nettoyage des versions non utilisées après 90 j).
- Cohérence entre le `.avsc` et le code généré : test CI comparant la génération à l'état commité.

## Notes d'implémentation

**Structure d'un contrat** (extrait `network-metrics-v1.avsc`)
```json
{
  "type": "record",
  "name": "NetworkMetrics",
  "namespace": "com.telecomx.network.v1",
  "doc": "Métriques de performance radio, agrégées par cellule sur une fenêtre de 60 s.",
  "telecomx.owner": "network-operations",
  "telecomx.sla_freshness_seconds": 120,
  "fields": [
    { "name": "event_id",   "type": "string", "doc": "UUID v7 — clé de déduplication" },
    { "name": "cell_id",    "type": "string", "doc": "Identifiant de cellule, format CELL-NNNN",
      "telecomx.pii": "none" },
    { "name": "drop_rate",  "type": "double", "doc": "Taux d'échec d'appel en pourcentage",
      "telecomx.unit": "percent", "telecomx.range": "[0, 100]" },
    { "name": "carrier_aggregation_enabled", "type": "boolean", "default": false,
      "doc": "Ajouté en v2 — valeur par défaut assurant la compatibilité BACKWARD" }
  ]
}
```

Les attributs personnalisés `telecomx.*` sont ignorés par Avro mais exploités par l'outillage :
- `telecomx.pii` → pilote la tokenisation automatique en silver ([ADR-013](ADR-013-pii-tokenization.md)) ;
- `telecomx.unit` et `telecomx.range` → génèrent les tests de qualité dbt (FR-M4-003) ;
- `telecomx.owner` et `telecomx.sla_freshness_seconds` → alimentent le catalogue et l'alerting.

> 💡 Conséquence importante : **les règles de qualité des données ne sont pas écrites deux fois.**
> Elles sont déclarées dans le contrat, puis générées en tests dbt, en validations Pydantic et en
> assertions Spark. Le contrat est réellement exécutable.

## Références
- [PRD §16](../product/PRD.md) · [FR-M4-001/002/003/009](../product/SRS.md) · [FR-M1-008](../product/SRS.md) · [ADR-003](ADR-003-table-format.md) · [ADR-013](ADR-013-pii-tokenization.md)
