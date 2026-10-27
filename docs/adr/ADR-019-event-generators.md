# ADR-019 — Générateurs d'événements : déterministes, couplés par un modèle d'indicateurs, avec vérité terrain

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-10-27 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 (lot 1.4) |

## Contexte

La plateforme n'a aucune source réelle : tout flux est simulé. La qualité de la simulation
conditionne tout le reste. Une détection d'anomalie, une prévision de saturation ou un score de
fraude ne valent que par les données sur lesquelles on les éprouve. Trois dangers :

| Danger | Conséquence |
|---|---|
| Flux **indépendants** (chaque générateur tire ses propres pourcentages) | Un incident réseau n'a aucun écho sur les appels, les sessions ou les plaintes : la corrélation multi-sources, cœur du cas d'usage UC-01, est introuvable |
| Données **non reproductibles** | Impossible de déboguer, de comparer deux versions d'un modèle, ou de figer un test |
| Anomalies injectées **sans trace** | Aucun moyen honnête de mesurer si un détecteur trouve ce qu'on a caché |

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Reproductibilité exacte, quel que soit le découpage en fenêtres | 🔴 Élevé |
| C2 | Cohérence entre flux : un incident se voit partout à la fois | 🔴 Élevé |
| C3 | Vérité terrain exploitable pour évaluer les détecteurs (Phase 3) | 🔴 Élevé |
| C4 | Retards d'arrivée réalistes pour exercer le watermark (FR-M1-010) | 🟠 Moyen |
| C5 | Testable sans Docker, rapide | 🟠 Moyen |
| C6 | Conforme aux contrats (lot 1.2) et au référentiel (lot 1.3) | 🔴 Élevé |

## Options envisagées

### Option A — Générer des fichiers de données à l'avance et les rejouer
Simple, mais fige le scénario, pèse sur le dépôt et interdit de varier la durée ou le rythme.

### Option B — Un générateur par flux, tirages indépendants
Rapide à écrire. Rejeté : viole C2. Les anomalies seraient cinq anomalies sans rapport.

### Option C — Des fonctions pures du temps, un modèle d'indicateurs partagé *(retenue)*
Chaque minute simulée est une « tranche » (*bucket*) produite par une fonction de
`(graine, minute)`. Tous les flux lisent le même modèle d'indicateurs de cellule.

## Décision

**Option C**, avec les règles suivantes.

1. **Une graine par tranche et par flux.** `Random("<graine>:<flux>:<minute>")`. Les événements
   d'une minute ne dépendent pas de la fenêtre qui les demande : deux découpages d'une même
   période donnent les mêmes événements (testé pour chacun des 11 flux).
2. **Fenêtre d'arrivée.** `generate(début, fin)` renvoie les événements qui *parviennent* à la
   plateforme dans `[début, fin)`, classés par `ingestion_timestamp`. Un événement survenu à
   11:59:50 et arrivé à 12:03 appartient à la fenêtre de midi, comme dans un vrai flux. Le moteur
   regarde donc en arrière de `very_late_max + 60 s`.
3. **Un modèle d'indicateurs unique (`KpiModel`).** Fonction pure `(cellule, minute) → indicateurs`.
   `network.metrics` en publie la valeur bruitée ; les appels tirent leur échec du `drop_rate`
   de la cellule servante ; les sessions de données, de son `packet_loss` ; les USSD, de sa
   latence ; le routeur voit sa charge monter avec le trafic de son site. **Aucun flux ne reçoit
   de dégradation propre** : ils la subissent tous parce qu'ils lisent la même fonction.
4. **Deux politiques de mise à l'échelle.** Les flux *transactionnels* sont des processus de
   Poisson dont le débit est `débit de production × SPEED_FACTOR` (formes horaire,
   hebdomadaire, début de mois, jour de l'An). Les flux *périodiques* (`network.metrics`,
   `device.events`) ne peuvent pas être éclaircis sans détruire les séries temporelles : on réduit
   le **nombre d'entités** (`periodic_fraction`), jamais la cadence. Les cellules et équipements du
   scénario de démonstration sont toujours inclus.
5. **Scénarios = fonctions du temps + vérité terrain.** `CellDegradation` (CELL-0421 : perte de
   paquets 1,2 % → 14,8 %, latence 184 ms…, valeurs du PRD) et `FraudBurst` (SIM swap puis neuf
   transactions croissantes jusqu'à 950 000 XOF, depuis un appareil et une région inconnus).
   `GroundTruth` enregistre ce qui a été injecté, **sans doublon** si une tranche est regénérée.
   Les contrats interdisent tout champ d'étiquette : la vérité terrain est un fichier à part.
6. **Retards.** 2 % des événements arrivent en retard (≤ 300 s) ; 0,1 % très en retard (≤ 900 s,
   au-delà du watermark de 5 min), pour exercer la table des arrivées tardives.
7. **Sorties.** Fichiers JSON Lines pour l'instant (`make gen-events`) ; Kafka au lot 1.6. Le
   moteur ignore la destination (`sinks.py`).

## Conséquences

| ✅ Positives | ⚠️ Négatives / limites assumées |
|---|---|
| Reproductible octet pour octet ; tests rapides (~17 s pour 83 tests) | Le modèle d'indicateurs est un modèle : il ressemble à un réseau, il n'en est pas un |
| Une panne apparaît cohérente dans cinq flux sans code de corrélation | Le cycle de vie des offres (`bundle.events`) est plausible *en agrégat*, pas par abonné : un `EXPIRED` ne suit pas un `PURCHASED` du même abonné |
| Vérité terrain exploitable pour mesurer rappel et précision | Un détecteur entraîné sur ces scénarios apprend *ces* scénarios : l'évaluation reste optimiste (à dire en Phase 3) |
| Le volume se règle sans casser les séries temporelles | Les appels ne sont générés qu'en sortant (MO) : une ligne par appel |

## Notes d'implémentation

- Les identifiants métier (`CALL-…`, `ALM-…`, `TRX-…`) dérivent de la minute et de la position
  dans la tranche : uniques sur la période, identiques à chaque régénération.
- L'`event_id` est un UUID v7 dont les bits aléatoires viennent du générateur seedé.
- Une alarme garde le même `alarm_id` de son ouverture à sa clôture (la clôture nomme l'alarme
  qu'elle ferme) ; les alarmes de fond sont rejouées par retour en arrière de 240 min.
- Débit mesuré : ~6 msg/s au total (≈ 4 transactionnels + ≈ 2 périodiques), voir
  [ADR-011](ADR-011-local-dev-strategy.md).
- Les flux n'importent que `telecom_common` et `telecomx_generators` (règle `.importlinter`).

## Références

- [ADR-015](ADR-015-data-contracts.md) — contrats Avro ; [ADR-018](ADR-018-reference-data.md) — référentiel
- [PRD §2.2 et scénario 61](../product/PRD.md) — débits de production et valeurs du scénario
- FR-M1-006 (enveloppe), FR-M1-010 (événements tardifs)
