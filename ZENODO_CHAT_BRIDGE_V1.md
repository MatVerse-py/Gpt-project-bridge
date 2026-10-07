# MatVerse Zenodo OAuth -> ChatGPT MCP Bridge v1

## Purpose

Provide governed, user-authorized access from ChatGPT to the MatVerse Zenodo account without placing a Zenodo access token or OAuth client secret in a prompt, receipt, manifest, or Git repository.

Architecture:

```text
ChatGPT
  |
  | Streamable HTTP MCP
  v
app/zenodo_mcp.py
  |
  | governed tools
  v
app/zenodo_bridge.py
  |
  +--> encrypted OAuth token store (.data, gitignored)
  |
  +--> Zenodo REST API
          |
          +-- records/search
          +-- user depositions
          +-- create draft
          +-- publish action (double gated)

Browser
  |
  | OAuth authorization
  v
Zenodo
  |
  | authorization code
  v
app/zenodo_oauth_app.py
  |
  +--> token exchange
  +--> encrypted token store
```

## Evidence boundary

This implementation establishes the software path and tests the OAuth/API logic with mocked transports.

It does NOT claim live Zenodo access until:
1. a Zenodo OAuth application is created;
2. the client ID/secret are injected at runtime;
3. the browser OAuth flow succeeds against Zenodo;
4. at least one live read is recorded;
5. write operations are tested first in Zenodo Sandbox.

No access token or client secret is emitted by MCP tools.

## Zenodo OAuth application form

Open:

`https://zenodo.org/account/settings/applications/clients/new/`

Use:

| Field | Value |
|---|---|
| Name | `MatVerse Zenodo Bridge` |
| Description | `OAuth client for the MatVerse governed publication bridge, enabling user-authorized Zenodo record discovery, draft management, and publication through ChatGPT/MCP.` |
| Website URL | `https://github.com/MatVerse-py/Gpt-project-bridge` |
| Redirect URI | `http://localhost:8788/zenodo/oauth/callback` |
| Client type | `Confidential` |

Why Confidential: the OAuth code exchange occurs in a backend process that can keep the client secret out of the browser and out of ChatGPT.

For a future deployed bridge, add a second redirect URI using HTTPS, for example:

`https://<bridge-host>/zenodo/oauth/callback`

Never replace the localhost URI until the deployed endpoint has MCP ingress authentication and secure secret storage.

## OAuth scopes

Default:

```text
deposit:write
```

This allows creating/updating deposits but does not authorize publication.

To enable irreversible publish/edit/discard operations, reconnect with:

```text
deposit:write deposit:actions
```

The MCP publish tool still requires an exact human confirmation phrase:

```text
PUBLISH ZENODO <deposition_id>
```

Therefore publication is protected by:
1. Zenodo OAuth `deposit:actions`;
2. ChatGPT write-tool confirmation;
3. the MatVerse exact confirmation phrase.

## Local configuration

Copy `.env.example` to a local `.env` and inject values outside Git.

Generate independent runtime secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set:

```text
ZENODO_CLIENT_ID=<from Zenodo>
ZENODO_CLIENT_SECRET=<from Zenodo; never paste into chat>
ZENODO_REDIRECT_URI=http://localhost:8788/zenodo/oauth/callback
ZENODO_BASE_URL=https://zenodo.org
ZENODO_SCOPES=deposit:write
ZENODO_OAUTH_STATE_SECRET=<generated secret>
ZENODO_TOKEN_ENCRYPTION_KEY=<generated Fernet key>
```

The access token is stored as encrypted ciphertext in:

`.data/zenodo-oauth.sqlite3`

The directory is gitignored.

## Run

Install:

```bash
python -m pip install -r requirements.txt
```

Start OAuth callback:

```bash
python -m app.zenodo_oauth_app
```

Start MCP server:

```bash
python -m app.zenodo_mcp
```

Default endpoints:

```text
OAuth start:    http://localhost:8788/zenodo/oauth/start
OAuth callback: http://localhost:8788/zenodo/oauth/callback
OAuth status:   http://localhost:8788/zenodo/status
MCP:            http://127.0.0.1:8800/mcp
```

The MCP server binds to localhost by default and fails closed if configured for a non-local host without `ZENODO_MCP_REMOTE_AUTH_READY=1`.

## Connect ChatGPT

Use ChatGPT custom MCP support with Streamable HTTP. For local development, expose the local MCP endpoint through the ChatGPT secure MCP tunnel and point it to:

`http://127.0.0.1:8800/mcp`

Do not expose this server directly to the public internet in v1.

Tools:

```text
zenodo_connection_status
zenodo_oauth_start_url
zenodo_search_records
zenodo_get_record
zenodo_list_depositions
zenodo_get_deposition
zenodo_create_draft
zenodo_publish_draft
```

## Sandbox before production writes

Zenodo provides a separate Sandbox account/environment. For write validation, create separate Sandbox credentials and use:

```text
ZENODO_BASE_URL=https://sandbox.zenodo.org
```

Do not reuse production OAuth credentials in Sandbox.

Promotion sequence:

```text
MOCK_TEST_PASS
  -> SANDBOX_OAUTH_PASS
  -> SANDBOX_READ_PASS
  -> SANDBOX_DRAFT_PASS
  -> SANDBOX_PUBLISH_PASS
  -> PRODUCTION_READ_PASS
  -> PRODUCTION_DRAFT_PASS
  -> PRODUCTION_PUBLISH_HOLD_UNTIL_EXPLICIT_AUTHORIZATION
```

## Security invariants

```text
ZENODO_CLIENT_SECRET not in Git
ACCESS_TOKEN not in Git
ACCESS_TOKEN not in prompt
ACCESS_TOKEN not in MCP result
ACCESS_TOKEN not in EvidenceReceipt
ACCESS_TOKEN not in logs

READ != WRITE
DRAFT != PUBLISH
LLM_RECOMMENDATION != AUTHORIZATION
deposit:write != deposit:actions
```

## Next hardening

Before a remote public deployment:
- put OAuth client secret, encryption key and state key behind the MatVerse Secret Plane / platform secret manager;
- add authenticated MCP ingress (OAuth for the ChatGPT -> MCP leg);
- bind Zenodo write actions to the existing LLM Publication Bridge authorization object;
- emit evidence receipts containing only action metadata, resource IDs, HTTP status and hashes, never credentials;
- add token refresh/rotation handling if Zenodo's issued token profile requires it;
- run live integration tests in Sandbox and preserve receipts.
