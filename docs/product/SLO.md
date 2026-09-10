# SLA / SLO / SLI

| Champ | Valeur |
|---|---|
| **Version** | 1.0 · 2026-09-10 |
| **Statut** | ✅ Baseline Phase 0 |
| **Documents liés** | [SRS §4](SRS.md) · [PRD §5.2](PRD.md) · [ADR-017](../adr/ADR-017-observability-stack.md) · [Runbooks](../runbooks/README.md) |

---

## 1. Vocabulaire

| Terme | Définition | Exemple |
|---|---|---|
| **SLI** | *Service Level Indicator* — la mesure | Part des requêtes API servies en moins de 500 ms |
| **SLO** | *Service Level Objective* — la cible interne | 95 % des requêtes en moins de 500 ms |
| **SLA** | *Service Level Agreement* — l'engagement contractuel | Disponibilité de 99,5 %, sous peine de pénalité |
| **Error budget** | Le droit à l'échec restant | 0,5 % d'indisponibilité = 3 h 39 min par mois |

> 💡 **L'error budget est l'outil de pilotage le plus utile de cette liste.** Un SLO de 99,5 %
> n'est pas un objectif de perfection : c'est l'autorisation explicite d'échouer 0,5 % du temps.
> Tant que le budget n'est pas consommé, on livre. Lorsqu'il l'est, on arrête les nouveautés et
> l'on consacre le sprint à la fiabilité. Cela transforme un débat d'opinion (« faut-il livrer
> cette fonctionnalité ? ») en décision fondée sur une mesure.

---

## 2. Services et niveaux de criticité

| Niveau | Signification | Services concernés |
|---|---|---|
| **Tier 1** | Une panne interrompt l'exploitation | Ingestion Kafka, pipeline bronze, API, détection d'anomalie |
| **Tier 2** | Une panne dégrade fortement | Trino, silver/gold, agents, ML serving |
| **Tier 3** | Une panne est tolérable à court terme | MLflow, Langfuse, catalogue, dashboards |

---

## 3. SLO par domaine

### 3.1 Ingestion et pipeline de données — Tier 1

| SLI | SLO | Fenêtre | Error budget | Alerte |
|---|---|---|---|---|
| Disponibilité de l'ingestion | **99,9 %** | 30 j | 43 min | Immédiate |
| Perte de messages | **0** | — | 0 | Immédiate (P1) |
| Latence production → bronze (p95) | **< 60 s** | 24 h | 5 % | > 120 s pendant 5 min |
| Consumer lag (p99) | **< 30 000 msg** | 1 h | — | > 50 000 pendant 10 min |
| Taux de messages en DLQ | **< 0,1 %** | 24 h | — | > 0,5 % |
| Latence bronze → silver (p95) | **< 3 min** | 24 h | 5 % | > 10 min |
| Fraîcheur des tables gold (p95) | **< 15 min** | 24 h | 5 % | > 30 min |
| Succès des jobs dbt | **≥ 99 %** | 7 j | 1 % | 2 échecs consécutifs |

### 3.2 Qualité des données — Tier 1

| SLI | SLO | Alerte |
|---|---|---|
| Conformité au contrat de schéma | **100 %** | Toute non-conformité |
| Complétude des champs obligatoires (silver) | **≥ 99,9 %** | < 99,5 % |
| Taux de doublons résiduels | **< 0,01 %** | > 0,05 % |
| Intégrité référentielle | **100 %** | Toute violation |
| Écart de volume vs attendu | **< 3σ** | > 3σ pendant 2 fenêtres |

> Une chute brutale de volume est aussi préoccupante qu'un pic : elle signale généralement un
> producteur tombé, et se traduit par un silence — le mode de panne le plus difficile à détecter
> sans SLO de volumétrie.

### 3.3 Requêtes analytiques — Tier 2

| SLI | SLO | Alerte |
|---|---|---|
| Disponibilité Trino | **99,5 %** | Indisponible > 2 min |
| Latence des requêtes sur KPI agrégés 30 j (p95) | **< 5 s** | > 15 s |
| Latence des requêtes d'agents (p95) | **< 3 s** | > 8 s |
| Taux d'échec des requêtes | **< 1 %** | > 5 % |

### 3.4 Plateforme ML — Tier 2

| SLI | SLO | Alerte |
|---|---|---|
| Disponibilité du service d'inférence | **99,5 %** | Indisponible > 2 min |
| Latence d'inférence unitaire (p95) | **< 200 ms** | > 500 ms |
| Latence d'inférence par lot 1 000 (p95) | **< 5 s** | > 15 s |
| Fraîcheur des prédictions batch | **< 6 h** | > 12 h |
| Drift des features (PSI) | **< 0,2** | > 0,25 |
| Dégradation de performance vs référence | **< 10 %** | > 15 % |

### 3.5 Agents IA — Tier 2

| SLI | SLO | Alerte |
|---|---|---|
| Taux de succès des exécutions | **≥ 95 %** | < 90 % |
| Latence d'un agent simple (p95) | **< 8 s** | > 20 s |
| Latence du Supervisor multi-agent (p95) | **< 20 s** | > 45 s |
| Premier octet en streaming | **< 2 s** | > 5 s |
| Taux d'échec des appels de tools | **< 2 %** | > 5 % |
| Ancrage factuel (chiffres issus d'un tool) | **100 %** | Toute violation |
| Coût LLM par requête | **< 0,01 €** | > 0,05 € |
| Disponibilité du LLM (fallback inclus) | **99,5 %** | Bascule sur fallback |

### 3.6 API et frontend — Tier 1

| SLI | SLO | Alerte |
|---|---|---|
| Disponibilité de l'API | **99,5 %** | Indisponible > 1 min |
| Latence des endpoints de lecture (p95) | **< 500 ms** | > 1,5 s |
| Latence des endpoints de lecture (p99) | **< 2 s** | > 5 s |
| Taux d'erreurs 5xx | **< 0,5 %** | > 2 % |
| Chargement initial du frontend (p95) | **< 3 s** | > 6 s |

### 3.7 Détection et réaction — l'objectif métier central

| SLI | SLO | Justification |
|---|---|---|
| Détection d'anomalie après l'événement source | **< 90 s** | Chaîne : ingestion (60 s) + scoring (30 s) |
| Délai d'identification de la cause racine (MTTI) | **< 8 min** | Objectif O1 du PRD — baseline 47 min |
| Délai de proposition d'action | **< 10 min** | Détection + diagnostic + recommandation |
| Délai de vérification post-action | **< 20 min** | Deux cycles d'agrégation KPI |

---

## 4. Disponibilité et reprise

| Métrique | Cible | Vérification |
|---|---|---|
| **RPO** (perte de données maximale) | **≤ 15 min** | Test de restauration trimestriel |
| **RTO** (délai de rétablissement) | **≤ 30 min** | Exercice de reprise documenté |
| Reprise automatique d'un consommateur | < 60 s, sans perte | Expérience de chaos |
| Bascule LLM sur fallback | < 5 s | Expérience de chaos |
| Sauvegarde du catalogue Iceberg | Toutes les 15 min | Automatisée |

> Le RPO de 15 minutes n'est pas un chiffre arbitraire : c'est l'intervalle de sauvegarde du
> catalogue Iceberg et des métadonnées PostgreSQL. Les données elles-mêmes sont protégées par
> la rétention Kafka (rejouable), ce qui rend la perte effective proche de zéro pour les
> événements — le RPO borne la perte de *métadonnées*, qui est la véritable contrainte.

---

## 5. Politique d'error budget

| Consommation | Décision |
|---|---|
| **< 50 %** | Livraison normale |
| **50–80 %** | Vigilance : revue des incidents en rétrospective |
| **80–100 %** | Gel des nouvelles fonctionnalités non critiques ; priorité à la fiabilité |
| **> 100 %** | **Gel complet.** Le sprint entier est consacré à la fiabilité jusqu'à retour sous 80 % |

Le budget se reconstitue sur une fenêtre glissante de 30 jours.

---

## 6. Sévérité des incidents

| Sévérité | Critère | Réaction | Communication |
|---|---|---|---|
| **P1 — Critique** | Perte de données, ingestion arrêtée, API indisponible, action erronée exécutée | Immédiate, 24/7 | Notification immédiate + point toutes les 30 min |
| **P2 — Majeure** | SLO Tier 1 non tenu, agents indisponibles, pipeline en échec | < 30 min, heures ouvrées étendues | Notification + point quotidien |
| **P3 — Mineure** | SLO Tier 2/3 non tenu, dégradation partielle | < 4 h ouvrées | Ticket |
| **P4 — Faible** | Anomalie sans impact utilisateur | Prochaine itération | Backlog |

Chaque type d'alerte renvoie à un **runbook** décrivant le diagnostic et la remédiation
(NFR-OBS-005). Une alerte sans runbook est considérée comme un défaut de la supervision.

---

## 7. Mesure

Tous les SLI sont dérivés de métriques Prometheus définies dans
[`libs/telecom-common/observability/`](../../libs/telecom-common/) et exposées dès la Phase 1,
même si les dashboards n'arrivent qu'en Phase 7 ([ADR-017](../adr/ADR-017-observability-stack.md)).

**Convention de mesure de disponibilité** : ratio des requêtes réussies sur les requêtes totales,
et non le temps d'activité du processus. Un service qui répond mais renvoie des erreurs n'est pas
disponible.

**Revue** : les SLO sont revus à la clôture de chaque phase. Un SLO systématiquement tenu à 100 %
est probablement trop laxiste ; un SLO jamais tenu est irréaliste ou signale un défaut de conception.
