# ADR-010 — Qdrant comme base vectorielle du RAG

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 4 |

## Contexte

Le RAG indexe le corpus opérationnel de TelecomX (FR-M6-008) : procédures d'exploitation (SOP),
guides de dépannage, manuels de maintenance, architecture réseau, catalogue produit, politiques
commerciales et procédures de fraude — de l'ordre de quelques milliers de chunks.

Deux besoins dictent le choix, au-delà de la simple recherche vectorielle :

1. **Filtrage par métadonnées obligatoire.** Une procédure destinée aux équipes radio ne doit pas
   remonter pour une question sur Mobile Money, et une SOP obsolète ne doit jamais être citée.
   La recherche doit filtrer par `domain`, `technology`, `validity`, `access_level`.
2. **Cloisonnement par rôle.** Un `marketing_manager` ne doit pas récupérer une procédure
   d'intervention réseau — le filtrage de sécurité doit s'appliquer **pendant** la recherche,
   pas après (sinon les scores de pertinence sont faussés et la fuite reste possible).

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Filtrage par métadonnées performant **pendant** la recherche | 🔴 Élevé |
| C2 | Empreinte ressources | 🔴 Élevé (contrainte 16 GB) |
| C3 | Simplicité d'exploitation | 🟠 Moyen |
| C4 | Recherche hybride (dense + lexical) | 🟠 Moyen |
| C5 | Composant supplémentaire dans la stack | 🟠 Moyen |

## Options envisagées

### Option A — Qdrant
✅ **Filtrage pré-recherche natif** : les filtres sont appliqués dans le parcours du graphe HNSW,
pas en post-traitement — exactement ce qu'exige C1 sans dégrader le rappel
✅ Écrit en Rust : **~150–300 Mo**, démarrage rapide, pas de JVM (C2)
✅ Recherche hybride (vecteurs denses + creux) et quantification scalaire intégrées
✅ API REST et gRPC, client Python officiel, collections avec schéma de payload
❌ Un service de plus à exploiter, sauvegarder et monitorer (C5)

### Option B — pgvector (extension PostgreSQL)
✅ **Aucun composant supplémentaire** : PostgreSQL est déjà présent (checkpoints LangGraph, données opérationnelles, catalogue Iceberg) — meilleur score sur C5 et C2
✅ Transactions ACID, jointures SQL entre vecteurs et données métier, sauvegarde unifiée
✅ Filtrage par `WHERE` naturel et expressif
❌ **Le filtrage combiné à la recherche ANN est le point faible connu** : selon la sélectivité, PostgreSQL choisit soit un scan séquentiel coûteux, soit un parcours d'index HNSW qui **perd du rappel** après filtrage — précisément le compromis que C1 cherche à éviter
❌ Performances inférieures à grande échelle ; pas de recherche hybride native
❌ Charge la même instance PostgreSQL que le chemin critique des agents

### Option C — Elasticsearch / OpenSearch
✅ Recherche hybride excellente, filtrage mature, écosystème riche
❌ **JVM, ~1,5–2 Go minimum** → rédhibitoire sur 16 GB partagés (C2)

### Option D — FAISS / ChromaDB en mémoire
✅ Extrêmement simple, zéro service
❌ Pas de persistance robuste, pas de filtrage riche, mono-processus → inadapté à un service partagé

## Comparaison

| Critère | A. Qdrant | B. pgvector | C. Elasticsearch | D. FAISS |
|---|---|---|---|---|
| C1 Filtrage + ANN | ✅✅ | 🔸 | ✅✅ | ❌ |
| C2 Ressources | ✅✅ | ✅✅✅ | ❌ | ✅✅✅ |
| C3 Exploitation | ✅ | ✅✅ | 🔸 | ✅✅ |
| C4 Hybride | ✅✅ | ❌ | ✅✅✅ | ❌ |
| C5 Composant en plus | 🔸 | ✅✅✅ | ❌ | ✅✅ |

## Décision

**Qdrant (Option A)**.

pgvector est objectivement plus économe (C2, C5) et l'argument « une base de moins » est réel.
Le critère qui tranche est **C1** : le filtrage par rôle et par domaine n'est pas ici un confort
mais une **exigence de sécurité** (SRS §2.2, NFR-SEC-004). Un filtrage appliqué *après* la
recherche vectorielle signifie qu'un document non autorisé a été lu, scoré et potentiellement
classé avant d'être écarté — une fuite dépendant uniquement de la rigueur du code applicatif.
Qdrant applique la contrainte **dans** la recherche.

Le coût de C5 est atténué par le fait que Qdrant n'appartient qu'au profil Compose `agents`
([ADR-011](ADR-011-local-dev-strategy.md)) : il n'est jamais démarré en même temps que Spark.

## Conséquences

**Positives**
- Le filtrage de sécurité est structurel, pas applicatif — défense en profondeur.
- Empreinte compatible avec le budget mémoire du profil `agents`.
- La recherche hybride améliore le rappel sur les requêtes riches en jargon télécom (codes d'alarme, références de SOP) que le dense seul capture mal.
- PostgreSQL n'est pas surchargé sur le chemin critique des agents.

**Négatives**
- Un service supplémentaire à déployer, sauvegarder et monitorer → sauvegarde des collections intégrée au plan DR (Phase 8).
- La cohérence entre le corpus source (MinIO) et l'index vectoriel doit être gérée explicitement : un document mis à jour exige une réindexation → pipeline d'ingestion RAG idempotent avec empreinte de contenu.
- Duplication de la donnée documentaire (source dans MinIO, index dans Qdrant).

**À surveiller**
- Fraîcheur de l'index vs corpus source.
- Qualité du rappel mesurée par le jeu d'évaluation RAG (FR-M6-014).

## Notes d'implémentation

**Schéma de collection**
```
collection: telecom_knowledge
  vector:  dense (modèle d'embedding configurable, distance cosinus)
           + sparse (BM25) pour la recherche hybride
  payload:
    doc_id, chunk_id, title, source_uri, content
    domain        : network | customer | fraud | revenue | commercial | security
    doc_type      : sop | runbook | manual | policy | catalog | architecture
    technology    : [2G, 3G, 4G, 5G, core, transport, mobile_money]
    access_level  : public | internal | restricted
    allowed_roles : [noc_operator, network_engineer, ...]     # ← filtre de sécurité
    version, valid_from, valid_until, updated_at
```

**Règle de recherche non contournable** — toute requête inclut obligatoirement :
```python
must=[
    FieldCondition(key="allowed_roles", match=MatchAny(any=user_context.roles)),
    FieldCondition(key="valid_until",   range=Range(gte=now)),   # jamais de SOP périmée
]
```
Cette contrainte est appliquée dans le client RAG, pas dans le code appelant : il est
**impossible** d'émettre une recherche sans filtre de rôle.

**Stratégie de chunking** : découpage guidé par la structure (titres, étapes numérotées des SOP)
plutôt qu'à taille fixe — une procédure en 5 étapes ne doit jamais être coupée au milieu.
Chaque chunk conserve son titre de section en préfixe pour préserver le contexte.

## Références
- [FR-M6-008](../product/SRS.md) · [NFR-SEC-004/010](../product/SRS.md) · [ADR-008](ADR-008-agent-orchestration.md) · [ADR-011](ADR-011-local-dev-strategy.md)
