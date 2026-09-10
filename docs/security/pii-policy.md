# Politique de protection des données personnelles (PII)

| Champ | Valeur |
|---|---|
| **Version** | 1.0 · 2026-09-10 |
| **Statut** | ✅ Baseline Phase 0 |
| **Portée** | Ensemble de la plateforme, de l'ingestion à la restitution |
| **Documents liés** | [ADR-013](../adr/ADR-013-pii-tokenization.md) · [Threat Model](threat-model.md) · [Architecture sécurité](../architecture/03-security-architecture.md) |

---

## ⚠️ Avertissement préalable

> **TelecomX est une entreprise fictive et l'intégralité des données manipulées est synthétique**,
> produite par les générateurs de `data-platform/generators/`. Aucune donnée personnelle réelle
> n'est utilisée, importée ou traitée à aucun stade du projet.
>
> Cette politique décrit néanmoins les protections **comme si** les données étaient réelles.
> C'est délibéré : concevoir une plateforme télécom sans traiter la protection des données
> reviendrait à ignorer l'une de ses contraintes les plus structurantes. Les mécanismes décrits
> ici sont réellement implémentés et testés.

---

## 1. Classification des données

| Niveau | Définition | Exemples | Traitement |
|---|---|---|---|
| 🔴 **P1 — Identifiant direct** | Identifie une personne sans recoupement | MSISDN, nom, e-mail, pièce d'identité, IMSI | **Tokenisé** dès silver ; jamais exposé |
| 🟠 **P2 — Identifiant indirect** | Identifie par recoupement | IMEI, identifiant de compte, adresse IP, coordonnées GPS précises | **Tokenisé** ou **généralisé** |
| 🟡 **P3 — Donnée personnelle sensible** | Révèle un comportement | Localisation par cellule, correspondants, transactions, historique d'usage | Conservée, **liée à un token** uniquement |
| 🟢 **P4 — Donnée non personnelle** | Ne se rapporte pas à une personne | Métriques réseau, alarmes, référentiel, capacité de cellule | Aucune restriction |

> 📍 **Le cas particulier de la localisation.** `cell_id` est en soi une donnée P4 (c'est un
> équipement). Mais **`cell_id` associé à un `subscriber_token` devient P3** : la séquence des
> cellules d'un abonné reconstitue ses déplacements. C'est pourquoi la localisation n'est jamais
> conservée plus finement que la cellule, et pourquoi les agrégats exposés aux agents portent sur
> des populations, jamais sur des trajectoires individuelles.

---

## 2. Inventaire par champ

| Champ | Source | Classe | Traitement | Présent en |
|---|---|---|---|---|
| `msisdn` (appelant/appelé/abonné) | CDR, SMS, USSD, recharge | 🔴 P1 | HMAC → `subscriber_token` | bronze¹, vault |
| `imsi` | CDR, sessions | 🔴 P1 | HMAC → `imsi_token` | bronze¹, vault |
| `customer_name`, `email` | CRM | 🔴 P1 | **Supprimé** de bronze ; vault chiffré uniquement | vault |
| `imei` / `device_id` | CDR, sessions, device | 🟠 P2 | HMAC → `device_token` | silver+ (tokenisé) |
| `momo_account` | Mobile Money | 🟠 P2 | HMAC → `account_token` | silver+ (tokenisé) |
| `gps_lat`, `gps_lon` | Sessions, Mobile Money | 🟠 P2 | **Généralisé** à `cell_id` / `site_id` | jamais conservé brut |
| `ip_address` | Sessions data | 🟠 P2 | Tronqué (/24) ou supprimé | silver (tronqué) |
| `cell_id` + `subscriber_token` | Tous | 🟡 P3 | Conservé, accès contrôlé par RBAC | silver, gold |
| `amount`, `duration`, `volume` | Transactions, CDR | 🟡 P3 | Conservé en clair | silver, gold |
| `call_content`, `sms_content` | — | 🔴 | **Jamais collecté** | ❌ nulle part |
| `cell_id` seul, métriques, alarmes | Réseau | 🟢 P4 | Aucune restriction | partout |

¹ *Bronze conserve la charge utile brute — voir §6, compromis assumé.*

---

## 3. Mécanismes de protection

### 3.1 Tokenisation (mécanisme principal)

```
msisdn "+223 7X XX XX XX"
        │
        │  HMAC-SHA256(clé secrète)      ← la clé est le sel : sans elle, la force
        ▼                                   brute sur l'espace des MSISDN est impossible
subscriber_token "v1:kJ8fD2..."
        │
        └──► circule dans silver, gold, features ML, tools, agents, UI
```

**Propriétés** : déterministe (jointures possibles), irréversible (pas de clé de déchiffrement),
rapide (applicable à 100 M événements/jour), versionné (`v1:` permet la rotation).

Détail dans [ADR-013](../adr/ADR-013-pii-tokenization.md).

### 3.2 Généralisation

La localisation GPS n'est jamais conservée : elle est immédiatement rattachée à la cellule
desservante. La précision passe ainsi de quelques mètres à quelques centaines de mètres —
suffisant pour l'analyse réseau, insuffisant pour le suivi individuel fin.

### 3.3 Minimisation

Les champs sans usage analytique identifié ne sont **pas collectés**. Le contenu des
communications, notamment, n'entre jamais dans la plateforme — ni en bronze, ni ailleurs.

### 3.4 Ré-identification contrôlée

Un `fraud_analyst` instruisant un dossier peut avoir besoin de remonter à l'abonné réel.
Cet accès existe, mais il est **exceptionnel et contrôlé** :

| Condition | Contrôle |
|---|---|
| Rôle | `fraud_analyst` ou `platform_admin` uniquement |
| Motif | Obligatoire, saisi et conservé |
| Traçabilité | Entrée d'audit immuable, non désactivable |
| Supervision | Alerte automatique sur pic d'accès |
| Périmètre | Un token à la fois — aucun export en masse |

Le vault est une table PostgreSQL chiffrée (AES-256-GCM, clé **distincte** de la clé HMAC),
alimentée de façon asynchrone, jamais présente sur le chemin d'ingestion.

---

## 4. Exposition aux agents IA

C'est le point le plus sensible : un LLM externe reçoit des données dans son contexte, et ces
données quittent l'infrastructure.

| Règle | Mise en œuvre |
|---|---|
| **Aucun identifiant direct ne peut atteindre un prompt** | Les tools ne retournent que des tokens — garantie structurelle, non déclarative |
| **Aucun accès au vault depuis un tool** | Aucun tool n'expose la ré-identification ; celle-ci passe par l'API avec contrôle humain |
| **Scan PII en sortie** | Les réponses d'agents sont balayées à la recherche de motifs de numéros avant restitution |
| **Rédaction des traces** | Les prompts enregistrés dans Langfuse sont filtrés en production |
| **Agrégats plutôt qu'individus** | Les tools d'analyse retournent des populations ; le détail individuel exige un rôle spécifique |

> 🔒 **La garantie est architecturale, pas comportementale.** On ne demande pas au modèle de ne pas
> divulguer de PII — on fait en sorte qu'il n'en reçoive jamais. Un contrôle qui dépend du bon
> vouloir d'un modèle non déterministe n'est pas un contrôle.

---

## 5. Rétention

| Zone | Contenu | Rétention prod | Rétention dev | Justification |
|---|---|---|---|---|
| Bronze | Charge utile brute (peut contenir P1) | **90 j** | 7 j | Rejeu et audit ; courte car sensible |
| Silver | Données tokenisées | 400 j | 30 j | Analyse sur 13 mois |
| Gold | Agrégats et features | 5 ans | illimité | Historique métier |
| Vault | Correspondance token → identité | Durée de la relation client + 1 an | — | Obligation légale simulée |
| Audit | Journal des accès | 5 ans | illimité | Preuve d'imputabilité |
| Traces Langfuse | Prompts et réponses | 30 j | 30 j | Débogage, minimisation |

**Purge** : exécutée par le job de maintenance, journalisée, vérifiée par un test de conformité.

---

## 6. Compromis assumés

| Compromis | Raison | Compensation |
|---|---|---|
| **Bronze conserve la charge utile brute**, donc potentiellement des identifiants directs | La zone brute doit refléter *exactement* ce qui a été reçu, sinon le rejeu et le débogage d'ingestion deviennent impossibles. Tokeniser au producteur supposerait de maîtriser des systèmes sources tiers. | Accès `platform_admin` uniquement · rétention 90 j · jamais exposé à Trino en libre-service · jamais accessible aux agents · chiffrement au repos |
| Tokenisation déterministe (vulnérable à la corrélation si la clé fuite) | Le déterminisme est indispensable aux jointures analytiques ; sans lui, aucun customer 360, aucun churn, aucune fraude | La clé n'est détenue que par le job d'ingestion, jamais par les composants de lecture |
| Les prompts transitent chez un fournisseur LLM tiers | Contrainte du projet (pas de LLM auto-hébergé viable sur 16 GB) | Aucune PII ne peut atteindre le prompt par construction ; option de rédaction ; transfert documenté |

---

## 7. Vérifications automatisées

| Vérification | Test | Fréquence |
|---|---|---|
| Aucun motif de MSISDN dans silver/gold | `test_no_pii_in_silver_gold` | CI |
| Tous les champs P1/P2 des contrats sont tokenisés | `test_pii_annotations_enforced` | CI |
| Aucun tool ne retourne de champ P1 | `test_tools_return_no_pii` | CI |
| Les réponses d'agents ne contiennent pas de PII | `test_agent_output_pii_scan` | CI |
| Tout accès au vault est audité | `test_vault_access_audited` | CI |
| La purge respecte les durées de rétention | `test_retention_policy` | Intégration |

> Le test `test_pii_annotations_enforced` mérite une mention : il vérifie que **tout champ marqué
> `telecomx.pii: direct` dans un contrat Avro est effectivement tokenisé** par le pipeline.
> Ajouter un champ sensible sans le protéger fait échouer le build — la protection est ainsi
> liée au contrat, pas à la vigilance du développeur.

---

## 8. Rôles et responsabilités

| Rôle | Responsabilité |
|---|---|
| `platform_admin` | Gestion des clés, accès break-glass, purge, conformité |
| `fraud_analyst` | Ré-identification motivée dans le cadre d'une investigation |
| Tous les autres rôles | Accès aux données tokenisées uniquement, selon la matrice RBAC |
| Développeur | Déclarer la classe PII de tout nouveau champ dans son contrat |

---

*Cette politique est révisée à chaque ajout de source de données ou de champ.*
