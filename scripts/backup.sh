#!/bin/sh
set -eu
mkdir -p backups
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
container=$(docker compose ps -q api)
[ -n "$container" ] || { echo 'API container is not running'; exit 1; }
docker compose exec -T api python - <<'PY' > "backups/projectvault-${timestamp}.sql"
import sqlite3, sys
source=sqlite3.connect('/app/data/projectvault.db')
target=sqlite3.connect(':memory:')
source.backup(target)
for line in target.iterdump():
    print(line)
PY
# The SQL dump is intentionally portable and inspectable.
gzip "backups/projectvault-${timestamp}.sql"
sha256sum "backups/projectvault-${timestamp}.sql.gz" > "backups/projectvault-${timestamp}.sql.gz.sha256"
echo "backups/projectvault-${timestamp}.sql.gz"
