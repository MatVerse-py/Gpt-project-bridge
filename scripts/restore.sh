#!/bin/sh
set -eu
archive="${1:?Usage: scripts/restore.sh backups/file.db.gz}"
test -f "$archive"
docker compose stop api
volume=$(docker volume ls --format '{{.Name}}' | grep 'gpb_data$' | head -1)
[ -n "$volume" ] || { echo 'gpb_data volume not found'; exit 1; }
tmp=$(mktemp -d)
gzip -dc "$archive" > "$tmp/dump.sql"
docker run --rm -v "$volume:/data" -v "$tmp:/restore:ro" python:3.13-slim sh -c \
  "python - <<'PY'
import sqlite3
from pathlib import Path
path=Path('/data/projectvault.db')
if path.exists(): path.rename('/data/projectvault.db.before-restore')
conn=sqlite3.connect(path)
conn.executescript(Path('/restore/dump.sql').read_text())
conn.close()
PY"
rm -rf "$tmp"
docker compose start api
echo 'restore completed; previous DB kept as projectvault.db.before-restore'
