#!/bin/sh
# Create the object-storage buckets of the platform (ADR-004, ADR-021). Idempotent.
# Runs inside the s3-init container (SeaweedFS image) and talks to the master/filer
# with `weed shell`: bucket creation is an administration task, not a data access, so
# the "S3 API only" rule of ADR-004 (which governs how data is read and written) is intact.
set -eu

MASTER=seaweedfs:9333
FILER=seaweedfs:8888
BUCKETS="telecom-lake telecom-warehouse telecom-mlflow telecom-checkpoints telecom-rag telecom-backup"

shell() {
  printf '%s\n' "$1" | weed shell -master="$MASTER" -filer="$FILER"
}

existing=$(shell "s3.bucket.list" 2>/dev/null || true)

for bucket in $BUCKETS; do
  if printf '%s\n' "$existing" | grep -qw "$bucket"; then
    echo "exists: $bucket"
  else
    shell "s3.bucket.create -name $bucket"
    echo "created: $bucket"
  fi
done

# The 7-day dev lifecycle of bronze (ADR-004) is not applied: SeaweedFS expresses
# expiry as a per-collection TTL, not as a bucket lifecycle rule. See ADR-021.

echo "OK: buckets ready"
shell "s3.bucket.list"
