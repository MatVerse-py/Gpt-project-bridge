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

The server negotiates supported MCP protocol versions and advertises read-only, non-destructive tool annotations. In production, tool calls require an OIDC access token with `projects.read`. `UNASSIGNED` means the source did not contain sufficient explicit attribution and the owner has not supplied a confirmed mapping.
