#!/bin/sh
# Create the object-storage buckets of the platform (ADR-004). Idempotent.
# Runs inside the minio-init container; credentials come from the environment.
set -eu

mc alias set local http://minio:9000 "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null

for bucket in telecom-lake telecom-warehouse telecom-mlflow telecom-checkpoints telecom-rag telecom-backup; do
  mc mb --ignore-existing "local/$bucket"
done

# Dev lifecycle (ADR-004): bronze is kept 7 days locally, the disk is small.
# `mc ilm rule add` is not idempotent (each run would stack another rule), so check first.
if ! mc ilm rule ls local/telecom-lake 2>/dev/null | grep -q "bronze/"; then
  mc ilm rule add --expire-days 7 --prefix "bronze/" local/telecom-lake
fi

echo "OK: buckets ready"
mc ls local
