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

`list_ingestions` returns bounded provenance records, including source and member-manifest SHA-256 values when an archive was imported. It never returns archive bytes. `list_unassigned` returns bounded metadata for sources that need an owner decision; callers must not infer project membership from names, topics or filenames.

The server negotiates supported MCP protocol versions and advertises read-only, non-destructive tool annotations. In production, tool calls require an OIDC access token with `projects.read`. `UNASSIGNED` means the source did not contain sufficient explicit attribution and the owner has not supplied a confirmed mapping.
