#!/bin/sh
# Restore drill: restore a backup into a THROWAWAY PostgreSQL container and
# verify it with the gateway image (`python -m app.ops verify`: schema
# version + row counts). Never touches the live database.
#
#   scripts/restore_drill.sh backups/twinlm-<stamp>.dump [GATEWAY_IMAGE]
#
# GATEWAY_IMAGE defaults to the Compose-built image (twinlm-gateway).
set -eu
dump="$1"
image="${2:-twinlm-gateway}"
name="twinlm-restore-drill-$$"
postgres="postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"

docker network create "$name" >/dev/null
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; docker network rm "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$name" --network "$name" -e POSTGRES_DB=twinlm -e POSTGRES_USER=twinlm \
  -e POSTGRES_PASSWORD=restore-drill "$postgres" >/dev/null
until docker exec "$name" pg_isready -h 127.0.0.1 -U twinlm -d twinlm >/dev/null 2>&1; do sleep 1; done
docker exec -i "$name" pg_restore -U twinlm -d twinlm --no-owner --exit-on-error < "$dump"
docker run --rm --network "$name" \
  -e DATABASE_URL="postgresql+psycopg://twinlm:restore-drill@$name:5432/twinlm" \
  "$image" python -m app.ops verify
echo "restore drill passed for $dump"
