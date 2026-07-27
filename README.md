# GPT Project Bridge

**Your projects. Your data. One bridge.**

GPT Project Bridge is a sovereign full-stack application for importing official ChatGPT exports, preserving explicit project attribution, searching the entire owner-controlled corpus and exposing selected sources to ChatGPT through read-only MCP tools.

## What is implemented

- dark dashboard with projects, conversations, messages and files counters;
- official ChatGPT export upload;
- optional owner map for missing attribution;
- project-file ZIP upload with explicit owner-supplied project identity;
- `UNASSIGNED` bucket when evidence is absent;
- SQLite FTS5 global search;
- full source viewer with provenance;
- ingestion history;
- REST API;
- MCP `search`, `fetch` and `list_projects`;
- static internal authentication for the web proxy;
- OAuth/OIDC authentication for external MCP in production;
- hybrid auth mode so web and MCP coexist;
- Docker Compose, optional Caddy TLS gateway, health checks, backup, restore, CI, checksums and declared-dependency SBOM.

## Start locally

```bash
git clone https://github.com/MatVerse-py/Gpt-project-bridge.git
cd Gpt-project-bridge
python3 scripts/generate_env.py
docker compose up -d --build
```

Open `http://127.0.0.1:3000`.

The initial `0 / 0 / 0 / 0` dashboard is expected. It means the vault is healthy but empty. Use **Ingest ChatGPT Export** to populate it.

## Test without Docker

```bash
PYTHONPATH=apps/api python3 -m pytest apps/api/tests
node --check apps/web/app.js
python3 -m compileall -q apps/api/projectvault scripts
```

## Production MCP

Set `GPB_DOMAIN` and the OIDC variables in `.env`, then run:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Register this endpoint in the MCP client:

```text
https://YOUR_DOMAIN/mcp
```

## Attribution rule

```text
Explicit export metadata  → automatic assignment
Confirmed owner map       → authorized assignment
No evidence               → UNASSIGNED
Contradiction              → ingest aborts
```

## Structural limit

This release is single-node because SQLite and local storage have one writer. It does not create an undocumented OpenAI API and does not scrape the logged-in ChatGPT interface. New account content enters through a new official export or files explicitly supplied by the owner.

See [Architecture](docs/ARCHITECTURE.md), [Threat Model](docs/THREAT_MODEL.md), [Operations](docs/OPERATIONS.md) and [MCP Contract](docs/MCP_CONTRACT.md).
