# Contrats d'API

| Champ | Valeur |
|---|---|
| **Version** | 1.0 · 2026-09-10 |
| **Statut** | 📐 Spécification Phase 0 — l'OpenAPI complet est généré en Phase 5 |
| **Documents liés** | [SRS §3.7](../product/SRS.md) · [ADR-012](../adr/ADR-012-identity-management.md) · [Sécurité](../architecture/03-security-architecture.md) |

> ℹ️ Ce document fixe les **conventions** que l'API devra respecter. La spécification OpenAPI 3.1
> faisant foi sera générée par FastAPI depuis le code annoté (FR-M7-001), garantissant qu'elle ne
> diverge jamais de l'implémentation.

---

## 1. Principes

| # | Principe |
|---|---|
| **A1** | Versionnée par chemin : `/api/v1/...`. Une rupture de contrat impose `/api/v2`. |
| **A2** | **Refus par défaut** : tout endpoint exige une authentification, sauf `/health` et `/api/v1/docs`. |
| **A3** | Le `tenant_id` provient **exclusivement du JWT** — jamais d'un paramètre de requête ([ADR-016](../adr/ADR-016-multi-tenancy.md)). |
| **A4** | Ressources au pluriel, identifiants dans le chemin : `/cells/{cell_id}/metrics`. |
| **A5** | Toute liste est paginée et bornée par défaut — aucune requête non bornée. |
| **A6** | Toute fenêtre temporelle est obligatoire sur les endpoints de séries. |
| **A7** | Les erreurs suivent RFC 9457 (*Problem Details*). |
| **A8** | Toute requête mutante est auditée (FR-M7-005). |
| **A9** | Une ressource hors tenant renvoie **404**, jamais 403 (évite l'énumération). |

---

## 2. Ressources

| Préfixe | Domaine | Rôles requis (extrait) |
|---|---|---|
| `/api/v1/network` | Cellules, sites, KPI, alarmes, équipements | `noc_operator`, `network_engineer` |
| `/api/v1/customers` | Customer 360, segments | `marketing_manager`, `data_analyst` |
| `/api/v1/churn` | Scores et facteurs de churn | `marketing_manager` |
| `/api/v1/fraud` | Transactions suspectes, investigations | `fraud_analyst` |
| `/api/v1/forecast` | Prévisions de trafic, risques de saturation | `network_engineer` |
| `/api/v1/agents` | Exécution d'agents, streaming, historique | tous (catalogue de tools filtré) |
| `/api/v1/approvals` | Demandes d'approbation HITL | selon la classe de risque |
| `/api/v1/incidents` | Incidents et tickets de maintenance | `noc_operator`, `field_engineer` |
| `/api/v1/operations` | Actions opérationnelles, kill-switch | `platform_admin`, `noc_operator` |
| `/api/v1/models` | Registre, métriques, drift | `ml_engineer` |
| `/api/v1/data-quality` | Conformité, fraîcheur, DLQ | `data_analyst`, `ml_engineer` |
| `/api/v1/admin` | Configuration, audit, tenants | `platform_admin` |

---

## 3. Endpoints structurants

### 3.1 Exécution d'agent (streaming)

```
POST /api/v1/agents/query
Content-Type: application/json
Accept: text/event-stream

{ "question": "Pourquoi la qualité réseau s'est dégradée à Bamako ?",
  "context": { "region": "Bamako", "time_window": "24h" } }
```

Réponse en **Server-Sent Events** (FR-M7-007, FR-M7-009) — l'utilisateur voit le raisonnement
se construire plutôt qu'un écran figé pendant 20 s :

```
event: classification   data: {"domain":"network","urgency":"high"}
event: plan             data: {"agents":["network","forecast"],"steps":[...]}
event: tool_call        data: {"tool":"query_network_metrics","args":{...}}
event: tool_result      data: {"tool":"query_network_metrics","rows":24,"source":"gold.network_kpis_hourly","as_of":"..."}
event: finding          data: {"text":"drop_rate +182% sur CELL-0421","confidence":0.94}
event: recommendation   data: {"action":"run_backhaul_diagnostic","risk":"L3","requires_approval":true}
event: approval_required data: {"approval_id":"APR-...","expires_at":"..."}
event: done             data: {"execution_id":"EXE-...","trace_id":"...","tokens":8421,"cost_eur":0.004}
```

> Chaque `tool_result` porte **sa source et son horodatage** : c'est ce qui rend une affirmation
> d'agent vérifiable, conformément au principe P4.

### 3.2 Approbation d'une action (HITL)

```
GET  /api/v1/approvals/pending           → demandes en attente pour l'utilisateur
GET  /api/v1/approvals/{approval_id}     → action, justification, DONNÉES SOUS-JACENTES, risque
POST /api/v1/approvals/{approval_id}/decide
     { "decision": "approve" | "reject", "justification": "..." }
```

**Règles** — l'approbation est le dernier rempart de sécurité ([threat model §5.2](../security/threat-model.md)) :

- L'approbateur doit avoir le rôle **et** le niveau de risque suffisants.
- Une classe **L4 exige deux approbations d'identités distinctes**.
- La demande **expire** après 15 min ; une demande expirée n'est jamais exécutée par défaut.
- L'approbation est liée à un **`execution_id` unique** — elle ne peut pas être rejouée.
- La justification est obligatoire pour un rejet et pour toute approbation L4.

### 3.3 Kill-switch

```
POST /api/v1/operations/kill-switch
     { "enabled": true, "reason": "..." }        → platform_admin uniquement
```

Désactive **immédiatement** toute exécution d'action par les agents, en préservant les
capacités de lecture et d'analyse (NFR-SEC-011).

---

## 4. Format d'erreur (RFC 9457)

```json
{
  "type": "https://telecomx.example/errors/insufficient-role",
  "title": "Insufficient permissions",
  "status": 403,
  "detail": "This action requires the 'network_engineer' role.",
  "instance": "/api/v1/operations/restart-service",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

Le `trace_id` permet de relier l'erreur vue par l'utilisateur à la trace OTel complète
côté serveur ([ADR-017](../adr/ADR-017-observability-stack.md)).

| Code | Signification | Note |
|---|---|---|
| 400 | Requête invalide | Détail de validation fourni |
| 401 | Non authentifié | Jeton absent, invalide ou expiré |
| 403 | Authentifié mais non autorisé | Audité (NFR-SEC-006) |
| 404 | Introuvable **ou hors tenant** | Volontairement indiscernable (A9) |
| 409 | Conflit | Ex. approbation déjà décidée |
| 410 | Expiré | Ex. demande d'approbation périmée |
| 422 | Entité non traitable | Ex. fenêtre temporelle incohérente |
| 429 | Débit dépassé | En-tête `Retry-After` fourni |
| 503 | Dépendance indisponible | Ex. LLM et fallbacks hors service |

---

## 5. Conventions transverses

| Aspect | Convention |
|---|---|
| Pagination | `?limit=50&cursor=<opaque>` — curseur, jamais d'offset (stable sous écriture concurrente) |
| Fenêtre temporelle | `?from=<ISO8601>&to=<ISO8601>` — **obligatoire**, en UTC, bornée à 90 j |
| Tri | `?sort=-timestamp` (préfixe `-` = descendant) |
| Filtres | `?region=Bamako&technology=4G` |
| Granularité | `?granularity=5m|1h|1d` |
| Idempotence | En-tête `Idempotency-Key` sur toute écriture |
| Limitation de débit | En-têtes `X-RateLimit-*` sur chaque réponse |
| Corrélation | En-tête `X-Trace-Id` renvoyé systématiquement |
| Versionnement | `/api/v1` ; dépréciation annoncée par en-tête `Sunset` |
