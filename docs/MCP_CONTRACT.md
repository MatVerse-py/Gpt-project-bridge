# MCP Contract

Endpoint: `POST /mcp`

Supported methods:

- `initialize`
- `ping`
- `tools/list`
- `tools/call`

Read-only tools:

- `search({"query":"..."})`
- `fetch({"id":"..."})`
- `list_projects({})`
- `list_ingestions({"limit"?: 1..500})`
- `list_unassigned({"limit"?: 1..1000})`
- `resolve_recoverability({"reference":"..."})`

`list_ingestions` returns bounded provenance records, including source and member-manifest SHA-256 values when an archive was imported. It never returns archive bytes. `list_unassigned` returns bounded metadata for sources that need an owner decision; callers must not infer project membership from names, topics or filenames.

The server negotiates supported MCP protocol versions and advertises read-only, non-destructive tool annotations. In production, tool calls require an OIDC access token with `projects.read`. `UNASSIGNED` means the source did not contain sufficient explicit attribution and the owner has not supplied a confirmed mapping.


## Recoverability contract

`resolve_recoverability` answers one narrow question before content retrieval:

> Is this object ingested, canonically identified, and authorized for retrieval?

The tool accepts either a Project Vault object ID such as `chat:<conversation_id>` or a ChatGPT conversation URL containing `/c/<conversation_id>`. It returns no document body.

Protocol: `matverse.recoverability.v1`

Statuses:

- `RECOVERABLE` — ingestion, identity, and authorization proofs all pass; next action is `fetch`.
- `NOT_INGESTED` — no matching object exists in the local Project Vault.
- `INGESTION_UNVERIFIED` — an object exists but its provenance evidence is incomplete.
- `IDENTITY_UNRESOLVED` — the object is preserved but remains `UNASSIGNED` or lacks an accepted attribution basis.
- `ACCESS_DENIED` — the authenticated principal does not satisfy the Bridge/partition authorization policy.
- `ACCESS_UNAVAILABLE` — the partition/provider boundary is currently unavailable.

The decision is fail-closed:

```text
INGESTION_PASS
AND IDENTITY_PASS
AND AUTHORIZATION_PASS
= RECOVERABLE
```

Existence alone never implies recoverability.
