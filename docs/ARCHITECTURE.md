# GPT Project Bridge — Architecture

## Purpose

GPT Project Bridge creates an owner-controlled, auditable retrieval layer over official ChatGPT exports, explicitly supplied project files and completed external task-backup containers such as Manus `.manustask`. It does not scrape an authenticated browser session, call a provider restoration API, or infer project membership from names, topics, dates, or similarity.

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
  ├─ external task-backup ingestion
  │    └─ member-by-member staging on the mounted data volume
  └─ SQLite FTS5 volume
```

## Trust boundaries

1. **Browser boundary:** the browser never receives the internal API token. The web proxy injects it upstream.
2. **External MCP boundary:** production uses `hybrid` authentication. The internal static token remains valid for the web proxy while external MCP callers must present a valid OIDC token and `projects.read` scope.
3. **Ingestion boundary:** ZIP members are checked for traversal, absolute paths, symlinks, encryption, duplicate normalized names, file count, member size and expanded size. Supported members are staged and parsed one at a time; the whole archive is never extracted.
4. **Attribution boundary:** explicit metadata wins. An owner map may fill missing attribution, but a contradiction aborts the ingest.
5. **External-backup boundary:** a `.manustask` archive is treated as a provider-agnostic ZIP container. Its container SHA-256 and member-manifest SHA-256 are recorded; content remains `UNASSIGNED` unless the owner explicitly assigns the entire archive to one project.

## Data model

- `projects`: stable project registry, including `unassigned`.
- `documents`: one preserved searchable unit per conversation or supplied file.
- `documents_fts`: SQLite FTS5 index.
- `ingestion_runs`: provenance and counts per import.
- `audit_log`: request and retrieval events without storing bearer tokens.

## Capacity model

The API uploads archive bodies and multipart spill files to `PROJECTVAULT_STAGING_DIR`, which defaults to the database volume's `staging` directory. This is deliberately separate from the container's small `/tmp` tmpfs. Multipart parsing and controlled intake briefly retain two copies, so the admission check reserves twice the declared body length plus the configured free-space floor. Only indexable member types are subsequently staged.

## Deployment constraint

SQLite and a local mounted volume make this a **single-writer, single-node** deployment. Do not scale the API horizontally against the same database file. A future PostgreSQL migration should replace storage and rate-limit state as one coordinated change rather than mixing backends.
