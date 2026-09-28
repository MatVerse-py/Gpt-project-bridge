# Owner Access Fabric v1

Protocol: `matverse.owner-access-fabric.v1`

## Purpose

The Owner Access Fabric extends GPT-Project-Bridge from one indexed corpus into a read-only federation of user-authorized information partitions.

The design separates four conditions that must all hold before content can be read:

1. **Owner authorization** — the caller is authenticated to the Bridge.
2. **Partition registration** — the partition was explicitly configured by the owner/operator.
3. **Partition capability** — the partition declares the requested read capability (`search` or `fetch`).
4. **Provider authorization** — the upstream provider accepts the credential/scope configured for that partition.

User intent is therefore a necessary authorization signal, but it does not bypass provider, connector, platform, or scope boundaries.

## Boundary

This feature does **not** create a privileged channel into arbitrary ChatGPT internals, hidden account state, or unregistered services.

A source becomes readable only if it is either:

- already in the local Project Vault; or
- exposed by an explicitly configured read-only MCP endpoint.

This lets the same access plane cover chats, files, mail, calendars, drives, repositories, notes, or future data sources once those sources expose a compatible `search` / `fetch` read contract.

## Security invariants

- Read-only capabilities only.
- Remote endpoints must use HTTPS, except loopback HTTP.
- No arbitrary endpoint is accepted in an MCP tool call.
- Provider credentials are referenced by environment-variable name in configuration and are never returned by the Bridge.
- Missing credentials, missing scopes, disabled partitions, and provider errors fail closed.
- Federated result IDs are namespaced: `<partition_id>::<encoded_source_id>`.
- Access receipts store hashes of query/purpose rather than raw query/purpose text.
- Search and fetch are re-authorized independently.
- The local legacy `search` / `fetch` tools remain unchanged.

## Permission modes

### `bridge_authorized`

Registration of the partition plus authenticated Bridge access is treated as the owner's standing delegation for read operations. Provider credentials are still independently enforced.

Use this for a connector the owner explicitly attached to the Bridge and wants available without repeated confirmation.

### `principal_scopes`

The caller must additionally carry every scope listed in `required_scopes`.

Use this for partitions that need stronger compartmentalization.

## Registry

Set:

```bash
PROJECTVAULT_PARTITION_REGISTRY=/app/config/partitions.json
```

Example:

```json
{
  "protocol": "matverse.owner-access-fabric.v1",
  "partitions": [
    {
      "partition_id": "drive",
      "title": "Owner Drive MCP",
      "adapter": "mcp_http",
      "endpoint": "https://drive-mcp.example.com/mcp",
      "capabilities": ["search", "fetch"],
      "authorization_mode": "bridge_authorized",
      "credential_env": "OWNER_DRIVE_MCP_TOKEN",
      "enabled": true
    }
  ]
}
```

`projectvault` is automatically registered as the local partition and must not be duplicated in the registry.

## MCP tools

When a partition registry is configured the Bridge additionally advertises:

- `list_partitions`
- `search_federated`
- `fetch_federated`
- `explain_access`

Legacy deployments with no partition registry keep the original five-tool MCP surface.

## Federated flow

```text
USER INTENT
    ↓
authenticated principal
    ↓
registered partitions
    ↓
authorization decision per partition
    ↓
search_federated
    ↓
namespaced references
    ↓
fetch_federated
    ↓
provider re-authorization
    ↓
content + access receipt
```

A federated search can query all authorized partitions or an explicit subset. One unavailable partition does not cause the other authorized partitions to be discarded; it is reported in `skipped_partitions`.

## Access receipt

The returned receipt records:

```text
protocol
receipt_id
timestamp
principal
action
purpose_hash
query_hash
requested_partitions
accessed_partitions
skipped_partitions
result_count
```

Raw credentials, content, raw query text, and raw purpose text are not placed in the receipt.

The same receipt is copied into the Bridge audit log.

## Remote MCP contract

A remote partition must expose compatible read tools.

Search:

```json
{
  "name": "search",
  "arguments": {
    "query": "..."
  }
}
```

and return a JSON object containing:

```json
{
  "results": [
    {
      "id": "provider-source-id",
      "title": "Human readable title",
      "url": "optional"
    }
  ]
}
```

Fetch:

```json
{
  "name": "fetch",
  "arguments": {
    "id": "provider-source-id"
  }
}
```

The provider may return data as MCP `structuredContent` or JSON text content.

## What this closes

Before this layer:

```text
Bridge
→ Project Vault only
```

After this layer:

```text
Bridge
       ┌→ Project Vault
       ├→ Drive MCP
       ├→ Mail MCP
       ├→ Calendar MCP
       ├→ Repository MCP
       ├→ Notes MCP
       └→ any future registered read partition
```

The architectural rule is:

```text
OWNER AUTHORIZATION
≠
UNBOUNDED ACCESS

OWNER AUTHORIZATION
+
REGISTERED PARTITION
+
READ CAPABILITY
+
PROVIDER PERMISSION
=
ACCESSIBLE INPUT
```

This preserves the user's ability to delegate broad retrieval to the Bridge without turning that delegation into a bypass of the security boundaries that make the data trustworthy.
