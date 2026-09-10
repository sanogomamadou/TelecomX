# Contrats de données — gouvernance

| Champ | Valeur |
|---|---|
| **Version** | 1.0 · 2026-09-10 |
| **Emplacement des contrats** | [`data-platform/schemas/avro/`](../../../data-platform/schemas/avro/) |
| **Documents liés** | [ADR-015](../../adr/ADR-015-data-contracts.md) · [Architecture données](../../architecture/02-data-architecture.md) · [Dictionnaire](../data-dictionary.md) |

---

## 1. Ce qu'est un contrat de données ici

Un contrat n'est pas une simple définition de schéma. C'est un **engagement entre un producteur et
ses consommateurs**, portant sur :

| Dimension | Où elle est déclarée |
|---|---|
| Structure et types | Champs Avro standards |
| Sémantique | Attribut `doc` de chaque champ — obligatoire |
| Unités | `telecomx.unit` |
| Règles de validité | `telecomx.constraint` |
| Sensibilité PII | `telecomx.pii`, `telecomx.tokenize_as` |
| Propriétaire | `telecomx.owner` |
| Fraîcheur attendue | `telecomx.sla_freshness_seconds` |
| Politique d'évolution | `telecomx.compatibility` |

> 🔑 **Le contrat est exécutable.** Les attributs `telecomx.*` ne sont pas de la documentation
> décorative : ils sont lus par le générateur de code et produisent les validations Pydantic,
> les assertions Spark, les tests dbt et la tokenisation PII. Déclarer une règle dans le contrat
> suffit à la faire appliquer — et ajouter un champ PII sans le déclarer fait échouer le build.

---

## 2. Catalogue des contrats

| Contrat | Topic | Débit prod | PII | Compatibilité | Fraîcheur |
|---|---|---|---|---|---|
| [`network-metrics-v1`](../../../data-platform/schemas/avro/network-metrics-v1.avsc) | `network.metrics` | 80/s | 🟢 P4 | BACKWARD | 120 s |
| [`network-alarms-v1`](../../../data-platform/schemas/avro/network-alarms-v1.avsc) | `network.alarms` | 3/s | 🟢 P4 | **FULL** | 60 s |
| [`cdr-v1`](../../../data-platform/schemas/avro/cdr-v1.avsc) | `cdr.events` | 1 150/s | 🔴 P1 | BACKWARD | 300 s |
| [`data-session-v1`](../../../data-platform/schemas/avro/data-session-v1.avsc) | `data.sessions` | 580/s | 🔴 P1 | BACKWARD | 300 s |
| [`sms-v1`](../../../data-platform/schemas/avro/sms-v1.avsc) | `sms.events` | 320/s | 🔴 P1 | BACKWARD | 300 s |
| [`ussd-v1`](../../../data-platform/schemas/avro/ussd-v1.avsc) | `ussd.events` | 210/s | 🔴 P1 | BACKWARD | 300 s |
| [`recharge-v1`](../../../data-platform/schemas/avro/recharge-v1.avsc) | `recharge.events` | 95/s | 🔴 P1 | BACKWARD | 120 s |
| [`bundle-v1`](../../../data-platform/schemas/avro/bundle-v1.avsc) | `bundle.events` | ~30/s | 🔴 P1 | BACKWARD | 300 s |
| [`mobilemoney-transaction-v1`](../../../data-platform/schemas/avro/mobilemoney-transaction-v1.avsc) | `mobilemoney.transactions` | 25/s | 🔴 P1 | **FULL** | 60 s |
| [`customer-event-v1`](../../../data-platform/schemas/avro/customer-event-v1.avsc) | `customer.events` | 12/s | 🔴 P1 | BACKWARD | 600 s |
| [`equipment-telemetry-v1`](../../../data-platform/schemas/avro/equipment-telemetry-v1.avsc) | `device.events` | ~40/s | 🟢 P4 | BACKWARD | 300 s |

**Pourquoi `FULL` sur alarmes et Mobile Money** : ces topics ont des consommateurs dont le cycle
de déploiement n'est pas maîtrisé (systèmes externes, obligations d'audit financier). La
compatibilité dans les deux sens est le seul régime qui garantit qu'aucun consommateur ne casse,
quel que soit son ordre de mise à jour.

---

## 3. Champs d'enveloppe communs

Tout contrat porte obligatoirement (FR-M1-006) :

| Champ | Rôle |
|---|---|
| `event_id` | UUID v7 — clé de déduplication |
| `event_timestamp` | Instant réel de l'événement (UTC) — partitionnement et fenêtrage |
| `ingestion_timestamp` | Instant d'entrée (UTC) — mesure de fraîcheur |
| `schema_version` | Version portée par l'événement — débogage |
| `producer_id` | Système émetteur — traçabilité |
| `tenant_id` | Isolation multi-tenant — **rejet en DLQ si absent** |

> L'UUID v7 est préféré à l'UUID v4 car il est **ordonnable dans le temps** : la déduplication et
> les index bénéficient de la localité temporelle, ce qui n'est pas anecdotique à 99 M
> d'événements par jour.

---

## 4. Politique d'évolution

### 4.1 Ce qui est autorisé (compatibilité `BACKWARD`)

| Évolution | Conditions |
|---|---|
| Ajouter un champ | **Avec valeur par défaut obligatoire** |
| Supprimer un champ | Uniquement s'il avait une valeur par défaut |
| Élargir un type | `int → long`, `float → double` |
| Ajouter un symbole d'énumération | Avec valeur par défaut d'énumération définie |
| Enrichir la documentation | Toujours autorisé |

### 4.2 Ce qui est interdit

| Évolution | Alternative |
|---|---|
| Ajouter un champ obligatoire | Ajouter avec valeur par défaut, puis migrer |
| Renommer un champ | Utiliser un `alias` Avro |
| Restreindre un type | Créer un nouveau champ et déprécier l'ancien |
| Changer la sémantique sans renommer | **Toujours renommer** — c'est le pire des changements silencieux |
| Modifier une unité | Nouveau champ (`latency_ms` → `latency_us`) |

> ⚠️ **Le changement le plus dangereux n'est pas structurel mais sémantique.** Passer `traffic_mb`
> de « mégaoctets » à « mébioctets », ou `drop_rate` d'un ratio [0,1] à un pourcentage [0,100],
> ne casse aucun schéma — et fausse silencieusement tous les KPI, tous les modèles et toutes les
> réponses d'agents. C'est pourquoi `telecomx.unit` est **obligatoire** sur toute mesure et
> pourquoi tout changement d'unité impose un nouveau nom de champ.

### 4.3 Procédure de changement

```
1. Rédiger le nouveau .avsc (v2)
2. Ouvrir une PR   →  la CI vérifie la compatibilité auprès du Schema Registry
                   →  ⛔ échec du build si incompatible
3. Revue par le propriétaire du contrat (telecomx.owner)
4. Merge           →  enregistrement de la nouvelle version au registry
5. Déployer les CONSOMMATEURS d'abord (compatibilité BACKWARD)
6. Déployer le PRODUCTEUR ensuite
7. Faire évoluer les tables Iceberg (ADD COLUMN — sans réécriture)
```

L'ordre de l'étape 5 avant l'étape 6 n'est pas un détail : c'est ce que la compatibilité
`BACKWARD` autorise, et l'inverse casserait les consommateurs.

---

## 5. Traitement des non-conformités

```
Message produit
     │
     ├── conforme ──────────────────────► topic principal
     │
     └── non conforme ──────────────────► <topic>.dlq
                                            + rejection_reason
                                            + raw_payload
                                            + producer_id, timestamp
```

| Motif de rejet | Action |
|---|---|
| Schéma inconnu ou incompatible | Alerte immédiate — indique un producteur non déployé correctement |
| `tenant_id` absent ou inconnu | Alerte — erreur de configuration du producteur |
| Contrainte violée (borne, format) | Compté, alerté si le taux dépasse 0,1 % (NFR-DQ-005) |
| Charge utile illisible | Alerte — problème de sérialisation |

Les messages en DLQ sont **conservés 30 jours et rejouables** après correction. Aucun message
n'est jamais perdu silencieusement.

---

## 6. Cas de test d'évolution (Phase 2)

Le scénario du [PRD §16](../../product/PRD.md) sert de test d'acceptation à la Phase 2 :

```
network.metrics v1 → v2
  + carrier_aggregation_enabled : boolean, défaut false     (ajout compatible)
  + traffic_mb : float → double                             (élargissement compatible)

Résultat attendu :
  ✅ Le registry accepte la v2
  ✅ Les producteurs v1 continuent d'émettre sans erreur
  ✅ Les consommateurs v2 lisent les messages v1 (défaut appliqué)
  ✅ La table Iceberg évolue par ALTER TABLE ADD COLUMN, sans réécriture
  ✅ Les requêtes existantes continuent de fonctionner
  ✅ Aucune interruption du pipeline
```

---

## 7. Vérifications automatisées

| Vérification | Étape |
|---|---|
| Les `.avsc` sont syntaxiquement valides | pre-commit + CI |
| Tout champ possède un `doc` non vide | CI |
| Toute mesure possède une `telecomx.unit` | CI |
| Tout champ PII déclare `telecomx.tokenize_as` | CI |
| Les champs d'enveloppe sont présents | CI |
| L'évolution est compatible avec la version enregistrée | CI |
| Le code généré est à jour vis-à-vis des `.avsc` | CI |
