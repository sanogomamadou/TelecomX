#!/bin/bash
# Create the platform topics declared in topics.conf. Idempotent: running it twice
# changes nothing, and a changed retention is applied to an existing topic.
#
# Runs inside the kafka-init container (apache/kafka image):
#   BOOTSTRAP_SERVERS  broker address as seen from the container
#   TOPICS_FILE        path of topics.conf
set -euo pipefail

: "${BOOTSTRAP_SERVERS:?BOOTSTRAP_SERVERS is required}"
: "${TOPICS_FILE:?TOPICS_FILE is required}"
KAFKA_BIN="${KAFKA_BIN:-/opt/kafka/bin}"  # overridable so the script can be tested without a broker

declared=0
while read -r topic partitions dev_hours _prod_days; do
  case "${topic:-}" in '' | '#'*) continue ;; esac

  retention_ms=$((dev_hours * 3600 * 1000))
  "$KAFKA_BIN/kafka-topics.sh" --bootstrap-server "$BOOTSTRAP_SERVERS" \
    --create --if-not-exists --topic "$topic" \
    --partitions "$partitions" --replication-factor 1 \
    --config "retention.ms=$retention_ms" --config "min.insync.replicas=1"

  # --create --if-not-exists leaves an existing topic untouched: re-apply retention.
  "$KAFKA_BIN/kafka-configs.sh" --bootstrap-server "$BOOTSTRAP_SERVERS" \
    --alter --entity-type topics --entity-name "$topic" \
    --add-config "retention.ms=$retention_ms" >/dev/null
  declared=$((declared + 1))
# tr drops carriage returns: a CRLF checkout of topics.conf must not create a topic named CR.
done < <(tr -d '\015' <"$TOPICS_FILE")

echo "OK: $declared topics declared and configured"
"$KAFKA_BIN/kafka-topics.sh" --bootstrap-server "$BOOTSTRAP_SERVERS" --list
