#!/bin/sh
# Back up the Compose deployment: PostgreSQL (custom-format dump) plus the
# gateway's usage outbox. Run from the repository root:
#
#   scripts/backup.sh [OUT_DIR]          # default ./backups
#
# Set COMPOSE_PROJECT_NAME / pass -p via COMPOSE if you use a custom project.
# The files are NOT encrypted and stay on this host: copy them off-host
# encrypted (e.g. `age -r <recipient>`) - see docs/OPERATIONS.md.
set -eu
out="${1:-./backups}"
compose="${COMPOSE:-docker compose}"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"
umask 077

$compose exec -T postgres pg_dump -U twinlm -d twinlm -Fc > "$out/twinlm-$stamp.dump"
# consistent copy of the live SQLite outbox via its online backup API
$compose exec -T gateway python -c "import sqlite3; sqlite3.connect('/app/data/usage-spool.db').backup(sqlite3.connect('/tmp/spool-backup.db'))"
$compose cp gateway:/tmp/spool-backup.db "$out/usage-spool-$stamp.db" >/dev/null
$compose exec -T gateway rm -f /tmp/spool-backup.db

( cd "$out" && sha256sum "twinlm-$stamp.dump" "usage-spool-$stamp.db" > "twinlm-$stamp.sha256" )
echo "backup written: $out/twinlm-$stamp.dump (+ outbox, checksums)"
