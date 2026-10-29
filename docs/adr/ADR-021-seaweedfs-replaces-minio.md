# ADR-021 — SeaweedFS remplace MinIO derrière l'API S3

| Champ | Valeur |
|---|---|
| **Statut** | ✅ Accepted |
| **Date** | 2026-10-28 |
| **Décideur** | M. Sanogo |
| **Phase** | 1 (lot 1.5) |
| **Remplace** | [ADR-004](ADR-004-object-storage.md) pour le choix du produit (la règle « API S3 uniquement » reste en vigueur) |

## Contexte

[ADR-004](ADR-004-object-storage.md) a retenu MinIO pour le stockage objet. En démarrant le
profil `core` pour la première fois sur une machine réelle, `docker compose pull` a échoué :

```
pull access denied for minio/mc, repository does not exist or may require 'docker login'
pull access denied for minio/minio, repository does not exist or may require 'docker login'
```

Vérification indépendante de la machine locale, par l'API publique de Docker Hub :
`minio/minio` et `minio/mc` répondent **« object not found »**, alors que d'autres dépôts
(`chrislusf/seaweedfs`, `rustfs/rustfs`, `dxflrs/garage`) existent et ont été mis à jour dans les
derniers jours. Le quay.io officiel de MinIO exige désormais une authentification. Les images
officielles ne sont donc plus distribuées : le projet ne peut plus démarrer son stockage tel
que décidé.

> 💡 **Ce que cela montre.** Le choix initial reposait sur l'état de l'écosystème en septembre 2026.
> Il a été invalidé en quelques semaines, sans que rien dans le dépôt ne change. Les tests
> statiques du lot 1.5 ne pouvaient pas le voir : seul un téléchargement réel l'a révélé.

## Critères de décision

| # | Critère | Poids |
|---|---|---|
| C1 | Compatibilité API S3 avec Spark (`s3a`), Trino et Iceberg | 🔴 Élevé |
| C2 | Image officielle **disponible et maintenue** | 🔴 **Bloquant** |
| C3 | Licence permissive, pas de dépendance à une édition commerciale | 🟠 Moyen |
| C4 | Empreinte mémoire compatible avec le budget de 0,3 GB (ADR-011) | 🔴 Élevé |
| C5 | Coût de migration (le reste du projet ne doit pas changer) | 🟠 Moyen |

## Options envisagées

| Option | Verdict |
|---|---|
| **A — SeaweedFS** | ✅ Maintenu activement, Apache-2.0, passerelle S3 éprouvée avec l'écosystème Spark/Trino/Iceberg, une seule image. ❌ Pas de console web intégrée de même niveau ; l'expiration est un TTL par collection, pas une règle de cycle de vie S3 ; la création de buckets passe par `weed shell`. |
| **B — Image `bitnamilegacy/minio`** | ✅ Même logiciel, migration quasi nulle. ❌ Image figée depuis août 2025, plus aucun correctif de sécurité : impasse annoncée. |
| **C — RustFS** | ✅ API et console très proches de MinIO, Apache-2.0. ❌ Projet jeune, en alpha : le risque de comportements S3 incomplets avec Iceberg est celui qu'on ne veut pas porter en Phase 1. |
| **D — Construire MinIO depuis les sources** | ✅ Logiciel connu. ❌ Une chaîne de construction et de mise à jour à maintenir pour un composant qu'on voulait « prêt à l'emploi ». |
| **E — Garage** | ✅ Très léger. ❌ Licence AGPL et couverture S3 plus étroite (cycle de vie, versioning). |

## Décision

**SeaweedFS (option A)**, épinglé en `chrislusf/seaweedfs:4.48`, en mono-nœud, avec sa passerelle S3.

Ce qui **ne change pas** et explique pourquoi la migration est petite :

- La règle d'ADR-004 : **tout accès aux données passe par l'API S3** (`s3a://`, `s3://`). Aucun
  code applicatif ne dépend de MinIO.
- Les variables `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`,
  `S3_PATH_STYLE_ACCESS` : la passerelle est publiée sur `localhost:9000`, comme l'était MinIO.
- Les six buckets d'ADR-004.

Ce qui change : le service Compose (`seaweedfs`), le script d'initialisation des buckets
(`s3-init.sh`), et l'authentification, qui passe par un fichier d'identités généré **au démarrage
du conteneur** à partir de l'environnement (aucun identifiant dans le dépôt).

## Conséquences

| ✅ Positives | ⚠️ Négatives / limites assumées |
|---|---|
| Le projet démarre à nouveau avec des images disponibles | **Plus de console web** pour parcourir les fichiers Parquet en démonstration (ADR-004 la citait). On passe par `weed shell`, la page d'état du maître (`localhost:9333`) ou un client S3 |
| La règle « S3 uniquement » a fait ses preuves : le remplacement n'a touché ni code ni contrats | **La rétention de 7 jours du bronze n'est pas appliquée en dev** : SeaweedFS exprime l'expiration en TTL par collection, pas en règle de cycle de vie de bucket. Le disque est surveillé à la main (`make clean-data`) |
| Licence Apache-2.0, produit activement maintenu | `mc mirror` (sauvegarde, ADR-004) n'existe plus : la Phase 8 utilisera un client S3 générique |
| Une seule image pour le service et son initialisation | **Compatibilité à confirmer avec Iceberg et Trino** (lots 1.7-1.8) ; c'est le risque principal de ce choix, à tester dès le premier job Spark, pas à supposer. *Constaté au lot 1.5 : l'authentification, l'écriture, la lecture et le listage en style chemin fonctionnent ; Iceberg et Trino restent non testés* |

## Notes d'implémentation

- Les autres ADR (003, 005, 010, 017) et les schémas d'architecture citent encore MinIO : ce sont
  des documents historiques. Lire « stockage objet S3 de la plateforme ».
- Mémoire : conteneur à 300 Mo, plafond du *runtime* Go (`GOMEMLIMIT`) à 200 Mo, l'équivalent
  Go du `-Xmx` Java (testé).
- Prévention : un test refuse toute image issue des dépôts retirés, et le job CI « Docker Compose
  file » vérifie maintenant, par `docker manifest inspect`, que **chaque image existe encore dans
  son registre**. C'est ce contrôle, absent au départ, qui aurait détecté le problème sans
  démarrage manuel.

## Références

- [ADR-004](ADR-004-object-storage.md) · [ADR-011](ADR-011-local-dev-strategy.md) · [ADR-020](ADR-020-core-runtime.md)
- SeaweedFS : https://github.com/seaweedfs/seaweedfs
