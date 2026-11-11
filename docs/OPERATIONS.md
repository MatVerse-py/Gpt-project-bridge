# Operations

## Local startup

```bash
cp .env.example .env
python3 scripts/generate_env.py  # use this instead of copying when .env does not exist
# or edit GPB_API_TOKEN manually
docker compose up -d --build
```

Open `http://127.0.0.1:3000`.

## Production

Configure a real domain and OIDC provider in `.env`, then:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The production override enables hybrid authentication and Caddy TLS. The OIDC provider must issue tokens with the configured issuer, audience and `projects.read` scope.

## Large archive capacity

Archive limits are environment variables expressed in bytes. The defaults are intentionally sized for the current multi-gigabyte Manus exports:

| Variable | Default | Purpose |
|---|---:|---|
| `PROJECTVAULT_MAX_UPLOAD_BYTES` | 6 GiB | Maximum uploaded container size. |
| `PROJECTVAULT_MAX_ARCHIVE_EXPANDED_BYTES` | 32 GiB | Maximum total uncompressed member size. |
| `PROJECTVAULT_MAX_ARCHIVE_MEMBER_BYTES` | 1 GiB | Maximum individual member size. |
| `PROJECTVAULT_MAX_INDEXABLE_FILE_BYTES` | 20 MiB | Largest supported member parsed into the search index. |
| `PROJECTVAULT_MAX_ARCHIVE_FILES` | 50,000 | Maximum non-directory members. |
| `PROJECTVAULT_MIN_FREE_BYTES` | 1 GiB | Storage reserve checked during upload and member staging. |

Set `PROJECTVAULT_STAGING_DIR` to a writable mounted volume with capacity for roughly twice the largest upload plus the reserve. Multipart parsing retains the incoming upload before the controlled intake copy is made, and the API rejects a declared upload when that combined space is unavailable. Archive upload endpoints require `Content-Length`; this avoids allocating multipart spill storage for an unbounded chunked request. In the supplied Docker Compose configuration it defaults to `/app/data/staging`; `TMPDIR` points to the same location so multipart uploads do not exhaust the 1 GiB `/tmp` tmpfs. The host volume, not the container tmpfs, must have the required free space.

To adjust capacity, set the values in `.env` and recreate the API container:

```bash
PROJECTVAULT_MAX_UPLOAD_BYTES=12884901888
PROJECTVAULT_MAX_ARCHIVE_EXPANDED_BYTES=68719476736
PROJECTVAULT_STAGING_DIR=/app/data/staging
docker compose up -d --build api
```

Do not raise the limits solely to accept an unknown archive. Keep the staging path private and use the member, expanded-size and free-space limits as independent controls.

## Manus task-backup intake

Use a completed `.manustask` file from storage you control. The browser form and the CLI both require that extension and validate the underlying ZIP container. For a large file, the CLI avoids a browser transfer:

```bash
cd apps/api
projectvault ingest-manus /secure/backups/tasks-data-matverse.manustask \
  --database /secure/bridge/projectvault.db \
  --staging-dir /secure/bridge/staging
```

The command is an indexing operation, not a Manus restoration. It records container and member provenance, indexes supported files one at a time, and uses `UNASSIGNED` by default. Add `--project-id` and `--project-name` together only when the owner confirms that every indexed member belongs to that one project.

## Backup

```bash
make backup
```

The backup is a portable SQLite SQL dump compressed with gzip and accompanied by a SHA-256 file.

## Restore

```bash
scripts/restore.sh backups/projectvault-YYYYMMDDTHHMMSSZ.sql.gz
```

The prior database is retained as `projectvault.db.before-restore` inside the data volume.

## Smoke test

```bash
make smoke
```

The smoke test checks API health, authenticated stats, MCP negotiation and the web root.
