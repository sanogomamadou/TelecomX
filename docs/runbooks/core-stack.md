# Démarrer et dépanner le socle `core`

> Décision et justification : [ADR-020](../adr/ADR-020-core-runtime.md). Budget mémoire : [ADR-011](../adr/ADR-011-local-dev-strategy.md).

## Démarrer

```powershell
.\make.ps1 env        # une fois : crée .env et génère les secrets locaux (jamais affichés)
.\make.ps1 up-core    # Kafka, Schema Registry, SeaweedFS (S3), PostgreSQL, Redis (≈ 2,1 GB)
```

Linux/macOS : `make env && make up-core`.

`kafka-init` et `s3-init` sont des tâches **ponctuelles** : elles créent les 22 topics puis les
6 buckets, puis s'arrêtent. `Exited (0)` est leur état normal de réussite.

| Service | Adresse depuis la machine | Vérification rapide |
|---|---|---|
| Kafka | `localhost:9092` | `docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:19092 --list` |
| Schema Registry | http://127.0.0.1:8081 | `curl http://127.0.0.1:8081/subjects` |
| Stockage objet (API S3) | http://localhost:9000 · état du maître http://localhost:9333 | client S3 avec les identifiants de `.env` |
| PostgreSQL | `localhost:5432` | `docker compose exec postgres pg_isready` |
| Redis | `localhost:6379` | `docker compose exec redis redis-cli ping` |

## Arrêter

`.\make.ps1 down` arrête tout en conservant les données (volumes). `.\make.ps1 clean-data` les efface.

## Symptômes courants

| Symptôme | Cause probable | Action |
|---|---|---|
| `docker compose pull` échoue avec `EOF` ou `TLS handshake timeout`, puis le moteur répond `Internal Server Error` | Réseau instable vers le CDN de Docker pendant un gros téléchargement (le Schema Registry pèse 4,2 GB) | `wsl --shutdown`, relancer Docker Desktop, retélécharger **image par image** (`docker pull <image>`) : les couches déjà reçues sont conservées. Un autre réseau (partage de connexion) aide |
| `pull access denied ... repository does not exist` | L'image a été retirée du registre | Ne pas insister : vérifier sur hub.docker.com si le dépôt existe encore (c'est ce qui est arrivé à MinIO, [ADR-021](../adr/ADR-021-seaweedfs-replaces-minio.md)) |
| Sous Git Bash, `docker compose exec ... /opt/...` répond `C:/Program Files/Git/opt/...` | Git Bash convertit les chemins commençant par `/` | `export MSYS_NO_PATHCONV=1` avant la commande, ou utiliser PowerShell |
| `required variable S3_SECRET_KEY is missing a value` | `.env` absent ou secret vide | `.\make.ps1 env` |
| `docker compose` ne répond pas / `Internal Server Error` | Le moteur Docker (WSL2) est bloqué | Vérifier `.wslconfig` (`memory=7GB`, ADR-011), puis `wsl --shutdown` et relancer Docker Desktop |
| Un conteneur se redémarre en boucle, `Exited (137)` | `OOMKilled` : limite mémoire dépassée | `docker inspect <conteneur> --format '{{.State.OOMKilled}}'` ; vérifier que `-Xmx` ≤ 70 % de la limite |
| `kafka-init` termine en erreur | Broker pas encore prêt ou `topics.conf` invalide | `docker compose logs kafka-init` ; le script est rejouable sans risque |
| Un client sur la machine ne joint pas Kafka | Il utilise `kafka:19092` (adresse interne) | Utiliser `localhost:9092` depuis l'hôte |
| Un client dans un conteneur ne joint pas Kafka | Il utilise `localhost:9092` | Utiliser `kafka:19092` |

## Prévention

Les règles mémoire, sécurité et cohérence topics ↔ contrats sont testées sans Docker :
`pytest tests/unit/infra`. Modifier `docker-compose.yml` ou `topics.conf` sans les relancer est
le seul moyen de les casser sans le savoir.
