# GPT Project Bridge — Architecture

## Purpose

GPT Project Bridge creates an owner-controlled, auditable retrieval layer over official ChatGPT exports and explicitly supplied project files. It does not scrape an authenticated browser session and does not infer project membership from names, topics, dates, or similarity.

## Runtime topology

```text
Browser
  │ same-origin HTTP
  ▼
Web/Caddy :3000
  ├─ static SPA
  └─ /bridge-api/* → API :8787
         Authorization: internal static token (server-side injection)

External ChatGPT or MCP client
  │ OAuth/OIDC bearer token
  ▼
Gateway /mcp → API :8787

API
  ├─ REST control and retrieval endpoints
  ├─ MCP read-only tools
  ├─ official-export ingestion
  ├─ project-file ingestion
  └─ SQLite FTS5 volume
```

## Trust boundaries

1. **Browser boundary:** the browser never receives the internal API token. The web proxy injects it upstream.
2. **External MCP boundary:** production uses `hybrid` authentication. The internal static token remains valid for the web proxy while external MCP callers must present a valid OIDC token and `projects.read` scope.
3. **Ingestion boundary:** ZIP members are checked for traversal, absolute paths, symlinks, file count, member size and expanded size.
4. **Attribution boundary:** explicit metadata wins. An owner map may fill missing attribution, but a contradiction aborts the ingest.

## Data model

- `projects`: stable project registry, including `unassigned`.
- `documents`: one preserved searchable unit per conversation or supplied file.
- `documents_fts`: SQLite FTS5 index.
- `ingestion_runs`: provenance and counts per import.
- `audit_log`: request and retrieval events without storing bearer tokens.

## Deployment constraint

SQLite and a local mounted volume make this a **single-writer, single-node** deployment. Do not scale the API horizontally against the same database file. A future PostgreSQL migration should replace storage and rate-limit state as one coordinated change rather than mixing backends.
