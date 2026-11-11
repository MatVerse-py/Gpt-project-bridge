# Native Provider Partitions v1

This layer extends `matverse.owner-access-fabric.v1` with read-only provider adapters that can be registered as information partitions.

## Principle

Effective access is still:

`OWNER AUTHORIZATION + REGISTERED PARTITION + READ CAPABILITY + PROVIDER PERMISSION`

A native adapter does not bypass OAuth scopes, provider sharing rules, workspace permissions, or connector availability. Tokens are supplied through environment variables and are never returned by Bridge APIs.

## Supported native adapters

- `github_rest`
- `google_drive`
- `gmail`
- `google_calendar`
- `notion`

`mcp_http` remains available for host-provided or third-party partitions, including ChatGPT Projects/File Library when a compatible host endpoint is actually exposed.

## GitHub

Adapter: `github_rest`

Credential: bearer token / installation token through `credential_env`.

Options:

- `kind`: `code` or `issues` (default `code`)
- `repository`: optional `owner/repo` restriction

The adapter uses the official `api.github.com` host only and sends `X-GitHub-Api-Version: 2026-03-10`.

## Google Drive

Adapter: `google_drive`

Credential: Google OAuth access token through `credential_env` with Drive read permission.

Search uses Drive v3 `files.list` with `name contains` / `fullText contains`. Fetch supports metadata and text retrieval for Google Docs, Google Sheets export, and directly textual MIME types. Unsupported binary content is returned as metadata-only rather than guessed.

Optional `drive_id` can scope a shared-drive partition.

## Gmail

Adapter: `gmail`

Credential: Google OAuth access token with an applicable Gmail read scope.

Options:

- `user_id`: defaults to `me`

Search uses Gmail search syntax through `users.messages.list`. Message details are resolved with `users.messages.get`; fetch extracts textual MIME bodies and falls back to the provider snippet when no textual body is available.

## Google Calendar

Adapter: `google_calendar`

Credential: Google OAuth access token with calendar event read access.

Options:

- `calendar_id`: default `primary`
- `past_days`: default `365`, max `3650`
- `future_days`: default `365`, max `3650`

Search uses `events.list` free-text `q`, `singleEvents=true`, and explicit RFC3339 bounds.

## Notion

Adapter: `notion`

Credential: Notion integration/OAuth bearer token through `credential_env`.

Options:

- `max_blocks`: default `200`, max `1000`

Requests use `Notion-Version: 2026-03-11`. Search resolves pages, and fetch reads page metadata plus block children up to the configured bound.

## ChatGPT Projects / File Library

There is deliberately no fabricated native adapter. The Bridge cannot assume access to private ChatGPT account internals from a deployed external service.

When the host provides a compatible authenticated MCP/HTTP partition, register it using `mcp_http`. This preserves the invariant:

`USER PERMISSION != UNEXPOSED PLATFORM ACCESS`

## Security invariants

1. Native adapters use fixed official provider hosts; registry entries cannot override them with arbitrary endpoints.
2. Provider tokens may only be referenced by environment variable name.
3. Secret-like values are rejected from partition `options`.
4. Search and fetch remain read-only capabilities.
5. Federated IDs remain namespaced as `<partition_id>::<encoded_source_id>`.
6. Access receipts store hashes of purpose/query, not raw credentials or copied source content.
7. A provider failure marks only that partition `UNAVAILABLE`; it does not create synthetic results.

## Example

```json
{
  "partition_id": "drive",
  "title": "Owner Google Drive",
  "adapter": "google_drive",
  "credential_env": "OWNER_GOOGLE_ACCESS_TOKEN",
  "authorization_mode": "bridge_authorized",
  "capabilities": ["search", "fetch"],
  "enabled": true
}
```

Once registered and authorized, `search_federated` can include this partition without a per-query reprompt, subject to the provider token's actual permissions.
