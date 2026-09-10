# Runbooks

Un **runbook** décrit la procédure de diagnostic et de remédiation d'une situation opérationnelle
précise. Il s'adresse à une personne d'astreinte, potentiellement à 3 h du matin, qui n'a pas
écrit le code concerné.

> 📏 **Règle** : toute règle d'alerte doit renvoyer à un runbook (NFR-OBS-005).
> Une alerte sans runbook est un défaut de la supervision, pas une fonctionnalité.

---

## Structure d'un runbook

| Section | Contenu |
|---|---|
| **Symptôme** | Ce que la personne voit — l'alerte exacte, le message, le dashboard |
| **Impact** | Ce qui est cassé pour les utilisateurs, et à quelle vitesse cela empire |
| **Sévérité** | P1 à P4, selon [SLO §6](../product/SLO.md) |
| **Diagnostic** | Commandes concrètes, dans l'ordre, avec l'interprétation de chaque sortie |
| **Remédiation** | Actions, de la moins invasive à la plus invasive |
| **Escalade** | Quand arrêter et à qui transmettre |
| **Prévention** | Ce qui éviterait la récurrence |

---

## Runbooks prévus par phase

| Phase | Runbook | Alerte associée |
|---|---|---|
| 1 | Consumer lag Kafka élevé | `KafkaConsumerLagHigh` |
| 1 | Job Spark Streaming arrêté | `SparkStreamingJobDown` |
| 1 | Fraîcheur de données dégradée | `DataFreshnessBreached` |
| 1 | Messages en DLQ au-delà du seuil | `DLQThresholdExceeded` |
| 2 | Schema Registry indisponible | `SchemaRegistryDown` |
| 2 | Violation de règle de qualité bloquante | `DataQualityGateFailed` |
| 2 | Rotation de la clé de tokenisation PII | *(procédure planifiée)* |
| 3 | Drift de modèle détecté | `ModelDriftDetected` |
| 3 | Service d'inférence dégradé | `MLServingLatencyHigh` |
| 4 | LLM indisponible / quota épuisé | `LLMProviderUnavailable` |
| 4 | Taux d'échec d'agent élevé | `AgentFailureRateHigh` |
| 4 | Approbations en attente accumulées | `PendingApprovalsBacklog` |
| 4 | Activation du kill-switch | *(procédure d'urgence)* |
| 5 | API indisponible | `APIDown` |
| 5 | Keycloak indisponible | `KeycloakDown` |
| 6 | Échec de déploiement / rollback | *(procédure)* |
| 8 | Petits fichiers Iceberg — compaction | `IcebergSmallFilesHigh` |
| 8 | Restauration après sinistre | *(procédure DR — RPO 15 min / RTO 30 min)* |

---

## Procédures d'urgence

| Situation | Action immédiate |
|---|---|
| **Un agent exécute des actions non désirées** | Activer le kill-switch : `AGENT_ACTIONS_DISABLED=true`, puis redémarrer le service d'agents. Les capacités de lecture restent disponibles. |
| **Suspicion de fuite de PII** | Couper l'accès au vault, exporter le journal d'audit, identifier le périmètre via `ops.audit_log`. |
| **Perte du catalogue Iceberg** | Restaurer le PostgreSQL du catalogue depuis la dernière sauvegarde (≤ 15 min), puis vérifier la cohérence des snapshots. |
| **Perte de la clé de tokenisation** | Les données existantes restent lisibles ; les **nouvelles** données ne seront plus jointes aux anciennes. Restaurer la clé depuis la sauvegarde hors ligne avant toute réingestion. |

---

*Les runbooks sont rédigés au fur et à mesure des phases, en même temps que les alertes qu'ils
documentent — jamais après.*
