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
