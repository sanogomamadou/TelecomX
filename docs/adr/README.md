# Architecture Decision Records (ADR)

Un ADR documente une **décision d'architecture structurante** : son contexte, les options réellement
envisagées, la décision retenue et ses conséquences — y compris négatives.

> **Pourquoi des ADR ?** Six mois après, personne ne se souvient pourquoi Iceberg plutôt que Delta.
> Sans trace écrite, l'équipe soit re-débat la décision, soit la subit sans la comprendre.
> Un ADR rend la décision **contestable de manière informée** : pour la remettre en cause,
> il faut invalider le contexte ou les critères, pas seulement avoir un avis.

## Convention

- **Immuables** : un ADR accepté n'est jamais modifié sur le fond. Pour changer d'avis, on écrit un
  nouvel ADR qui `Supersedes` le précédent, lequel passe en statut `Superseded`.
- **Numérotation** continue, jamais réutilisée.
- **Statuts** : `Proposed` · `Accepted` · `Deprecated` · `Superseded by ADR-XXX`
- Modèle : [`TEMPLATE.md`](TEMPLATE.md)

## Index

| # | Titre | Statut | Domaine | Phase |
|---|---|---|---|---|
| [001](ADR-001-monorepo.md) | Monorepo plutôt que dépôts multiples | ✅ Accepted | Organisation | 0 |
| [002](ADR-002-event-backbone.md) | Apache Kafka (mode KRaft) comme bus d'événements | ✅ Accepted | Streaming | 1 |
| [003](ADR-003-table-format.md) | Apache Iceberg comme format de table du Lakehouse | ✅ Accepted | Lakehouse | 1 |
| [004](ADR-004-object-storage.md) | MinIO comme stockage objet compatible S3 | ⚠️ Produit remplacé par ADR-021 | Stockage | 1 |
| [005](ADR-005-stream-processing.md) | Spark Structured Streaming comme moteur de traitement | ✅ Accepted | Traitement | 1 |
| [006](ADR-006-query-engine.md) | Trino comme moteur SQL d'interrogation | ✅ Accepted | Analytics | 1 |
| [007](ADR-007-transformation-layer.md) | dbt pour les transformations silver → gold | ✅ Accepted | Analytics | 1 |
| [008](ADR-008-agent-orchestration.md) | LangGraph pour l'orchestration multi-agent | ✅ Accepted | IA | 4 |
| [009](ADR-009-llm-provider.md) | Groq comme fournisseur LLM primaire derrière une gateway | ✅ Accepted | IA | 4 |
| [010](ADR-010-vector-store.md) | Qdrant comme base vectorielle du RAG | ✅ Accepted | IA | 4 |
| [011](ADR-011-local-dev-strategy.md) | Profils Docker Compose pour tenir en 16 GB de RAM | ✅ Accepted | Infrastructure | 1 |
| [012](ADR-012-identity-management.md) | Keycloak comme fournisseur d'identité et d'autorisation | ✅ Accepted | Sécurité | 5 |
| [013](ADR-013-pii-tokenization.md) | Tokenisation HMAC-SHA256 des identifiants d'abonné | ✅ Accepted | Sécurité | 2 |
| [014](ADR-014-python-tooling.md) | uv + ruff + mypy comme chaîne d'outils Python | ✅ Accepted | Ingénierie | 0 |
| [015](ADR-015-data-contracts.md) | Avro + Schema Registry pour les contrats de données | ✅ Accepted | Gouvernance | 2 |
| [016](ADR-016-multi-tenancy.md) | Multi-tenancy par isolation logique avec `tenant_id` | ✅ Accepted | Architecture | 0 |
| [017](ADR-017-observability-stack.md) | OpenTelemetry comme standard d'instrumentation unique | ✅ Accepted | Observabilité | 7 |
| [018](ADR-018-reference-data.md) | Données de référence synthétiques, déterministes et versionnées comme seeds | ✅ Accepted | Données | 1 |
| [019](ADR-019-event-generators.md) | Générateurs d'événements déterministes, couplés par un modèle d'indicateurs, avec vérité terrain | ✅ Accepted | Données | 1 |
| [020](ADR-020-core-runtime.md) | Socle d'exécution local : profil `core`, garde-fous vérifiés sans Docker | ✅ Accepted | Infrastructure | 1 |
| [021](ADR-021-seaweedfs-replaces-minio.md) | SeaweedFS remplace MinIO derrière l'API S3 | ✅ Accepted | Stockage | 1 |
| [022](ADR-022-event-publisher.md) | Publieur d'événements : valider avant d'envoyer, rejeter vers une file de rebut sous contrat | ✅ Accepted | Données | 1 |

## Décisions à réexaminer

| ADR | Condition de réexamen |
|---|---|
| 009 | Si les quotas Groq deviennent bloquants ou si un budget LLM est alloué |
| 011 | Si l'environnement de développement dispose de plus de 32 GB de RAM |
| 005 | Si la latence de traitement devient un facteur bloquant (< 1 s exigé) → réévaluer Flink |
| 016 | Si un besoin d'isolation physique par client apparaît (contrainte réglementaire) |
