# ADR-013 — Tokenisation HMAC-SHA256 des identifiants d'abonné

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-09-10 |
| **Décideur** | M. Sanogo |
| **Phase** | 2 |

## Contexte

Le MSISDN est la donnée la plus sensible d'un opérateur : identifiant direct, il relie une personne
physique à sa localisation (via la cellule), ses correspondants, ses habitudes et ses transactions
financières. Le secteur télécom est soumis au secret des correspondances en plus du RGPD.

Trois exigences s'opposent et doivent être conciliées :

| Exigence | Implication |
|---|---|
| **NFR-SEC-001** — aucun identifiant direct au-delà de l'ingestion | Il faut transformer irréversiblement... |
| **Analytique** — `customer_360`, churn, fraude exigent de suivre un abonné dans le temps et à travers les sources | ...mais de façon **déterministe et stable**, sinon aucune jointure ni aucun historique |
| **Investigation fraude** — U4 doit pouvoir remonter à un abonné réel pour instruire un dossier | ...avec une **ré-identification possible, contrôlée et auditée** |

Un simple hachage ne suffit pas : l'espace des MSISDN est petit et structuré (préfixe opérateur +
8 chiffres, soit quelques dizaines de millions de valeurs). Un `SHA256(msisdn)` est **cassable par
force brute en quelques minutes** sur un ordinateur portable — c'est une pseudonymisation illusoire.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Résistance à la force brute et aux tables arc-en-ciel | 🔴 **Bloquant** |
| C2 | Déterminisme (même entrée → même token, toujours) | 🔴 Élevé |
| C3 | Ré-identification contrôlée pour les cas légitimes | 🟠 Moyen |
| C4 | Performance (applicable à ~100 M événements/jour) | 🔴 Élevé |
| C5 | Simplicité d'exploitation (rotation de clés, sauvegarde) | 🟠 Moyen |

## Options envisagées

| Option | Analyse |
|---|---|
| **A — HMAC-SHA256 avec clé secrète** | ✅ Déterministe (C2) ✅ **La clé secrète rend la force brute impossible** sans compromission de la clé (C1) ✅ Très rapide, vectorisable en Spark (C4) ✅ Irréversible par construction ❌ Ré-identification impossible sans table de correspondance séparée (C3) ❌ La rotation de clé invalide tous les tokens existants (C5) |
| **B — SHA256 simple / avec sel fixe** | ✅ Trivial, déterministe ❌ **Cassable par force brute** sur un espace de MSISDN réduit → échec sur C1 (bloquant) |
| **C — Chiffrement réversible (AES-SIV, FF1/FPE)** | ✅ Déterministe et **réversible** avec la clé → satisfait C3 nativement ✅ FF1 préserve le format (utile pour des systèmes hérités) ❌ Réversible signifie qu'une compromission de clé expose **toutes** les données, rétroactivement ❌ Plus lent, implémentations FPE moins courantes ❌ Une donnée chiffrée réversible reste juridiquement une donnée personnelle |
| **D — Vault de tokenisation (jetons aléatoires + table de correspondance)** | ✅ Sécurité maximale : le token ne contient **aucune** information dérivée de la source ✅ Ré-identification par consultation contrôlée et auditée (C3) ❌ Nécessite une consultation pour **chaque** événement à l'ingestion → goulet d'étranglement à 100 M événements/jour (C4, éliminatoire à cette échelle) ❌ Le vault devient un point de défaillance et une cible de grande valeur |

## Décision

**HMAC-SHA256 avec clé secrète (Option A)** pour la tokenisation de masse à l'ingestion,
**complétée par un vault de ré-identification restreint (Option D) pour les seuls cas
d'investigation légitimes**.

Cette combinaison résout la tension entre C1/C4 et C3 :

```
INGESTION (100 % du volume, chemin chaud)
  msisdn ──HMAC-SHA256(clé)──► subscriber_token          irréversible, déterministe, rapide
                                     │
                                     └──► circule dans bronze, silver, gold, ML, agents

RÉ-IDENTIFICATION (cas exceptionnels, chemin froid)
  Table `vault.subscriber_identity` (PostgreSQL chiffré, accès restreint)
    subscriber_token → msisdn chiffré (AES-256-GCM, clé distincte)
  Accès conditionné à : rôle fraud_analyst OU platform_admin
                      + motif obligatoire + journalisation immuable + notification
```

Le vault n'est **jamais** sur le chemin d'ingestion : il est alimenté une seule fois par abonné
(lors de sa première apparition), de manière asynchrone. Il n'est consulté qu'à la demande
explicite d'un analyste habilité.

### Portée de la tokenisation

| Donnée | Traitement | Justification |
|---|---|---|
| MSISDN (appelant, appelé, abonné) | **HMAC** → `subscriber_token` | Identifiant direct |
| Compte Mobile Money | **HMAC** → `account_token` | Identifiant financier direct |
| IMEI / identifiant d'appareil | **HMAC** → `device_token` | Identifiant d'appareil traçable |
| IMSI | **HMAC** → `imsi_token` | Identifiant SIM |
| Nom, e-mail | **Supprimé** de bronze ; présent uniquement dans le vault | Aucun usage analytique |
| Localisation précise (GPS) | **Généralisée** à la cellule/site | Minimisation |
| Contenu des communications | **Jamais collecté** | Secret des correspondances |
| Montant, durée, volume | **Conservé en clair** | Nécessaire à l'analyse, non identifiant seul |

## Conséquences

**Positives**
- Les jointures analytiques fonctionnent normalement : `subscriber_token` est une clé stable dans le temps et entre toutes les sources.
- L'analyse de fraude, de churn et de customer 360 est possible sans jamais manipuler de MSISDN.
- Les agents IA ne voient que des tokens — la conformité à NFR-SEC-001 est structurelle, non déclarative.
- Une compromission du Lakehouse **n'expose aucune identité** sans la clé HMAC, stockée ailleurs.

**Négatives**
- **La clé HMAC devient un actif critique** : sa perte rend impossible la tokenisation cohérente de nouvelles données ; sa fuite permet de reconstruire la correspondance par force brute. → Clé hors du dépôt, injectée par variable d'environnement en développement et par secret Kubernetes en production, sauvegardée séparément avec le plan DR.
- **La rotation de clé invalide tous les tokens existants** et briserait l'historique. → Stratégie de versionnement de clé : le token porte un préfixe de version (`v1:abc123…`), permettant une transition progressive avec double écriture. Documentée en runbook.
- Le vault est un composant sensible supplémentaire à protéger, chiffrer et auditer.
- Déterminisme ⇒ **vulnérabilité aux attaques par corrélation** : un attaquant connaissant le MSISDN d'une cible et disposant d'un accès en lecture pourrait, s'il obtenait la clé, retrouver ses données. Mitigation : la clé n'est jamais accessible depuis les composants de lecture (Trino, API, agents) — seul le job Spark d'ingestion la détient.

**À surveiller**
- Tout accès au vault (métrique + alerte : un pic de consultations est un signal d'incident).
- Absence de MSISDN en clair : test automatisé balayant les tables silver/gold à la recherche de motifs de numéros (NFR-SEC-001).

## Notes d'implémentation

`libs/telecom-common/security/tokenization.py`
```python
def tokenize(value: str, key: bytes, *, version: str = "v1") -> str:
    """Tokenisation déterministe et irréversible d'un identifiant direct.

    Le sel est porté par la clé secrète (HMAC), pas par une constante publique :
    sans la clé, l'espace réduit des MSISDN ne peut pas être exploré par force brute.
    """
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
    return f"{version}:{base64.urlsafe_b64encode(digest).decode()[:32]}"
```

**Point d'application unique** : la tokenisation s'effectue dans le job Spark `bronze → silver`
(FR-M2-003), en un seul endroit du code. Aucun autre composant ne détient la clé.

> ⚠️ **Choix assumé** : la zone bronze conserve la charge utile brute (FR-M2-002), donc
> **potentiellement des identifiants directs**. C'est délibéré — bronze doit permettre le rejeu
> et le débogage d'ingestion. En contrepartie, bronze est soumis à un régime renforcé :
> accès réservé à `platform_admin`, rétention courte (90 j en production, 7 j en dev),
> jamais exposé à Trino en libre-service, jamais accessible aux agents.
> *(Dans un contexte réglementaire strict, la tokenisation serait avancée au producteur lui-même ;
> ce compromis est documenté comme tel dans le [threat model](../security/threat-model.md).)*

## Références
- [NFR-SEC-001/002](../product/SRS.md) · [FR-M2-003](../product/SRS.md) · [Politique PII](../security/pii-policy.md) · [Threat Model](../security/threat-model.md)
