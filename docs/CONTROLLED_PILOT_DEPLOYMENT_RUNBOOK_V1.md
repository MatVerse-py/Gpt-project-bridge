# MatVerse Trust Runtime — Controlled Pilot Deployment Runbook v1

Status: `DEPLOYMENT_RUNBOOK_READY / LIVE_HOST_HOLD`

This runbook deploys the institutional commercial surface without changing the canonical runtime entrypoint. It does not by itself establish unrestricted production readiness, `EXTERNAL_PASS`, or `WORLD_REAL_PASS`.

## Image

Use the dedicated pilot image:

```bash
docker build -f Dockerfile.institutional -t matverse-trust-runtime:pilot-v1 .
```

It serves:

```text
uvicorn app.institutional_service:app --host 0.0.0.0 --port 8000
```

The original repository `Dockerfile` remains reserved for `app.main:app`.

## Required deployment bindings

Provision these values outside the image:

```text
MATVERSE_RUNTIME_ID
MATVERSE_BUILD_COMMIT
MATVERSE_BUILD_REF
MATVERSE_FROZEN_CONTRACT_HASH
MATVERSE_BUILD_TIMESTAMP
MATVERSE_PRINCIPALS_JSON
MATVERSE_DB
```

Rules:

- `MATVERSE_BUILD_COMMIT` must be the exact deployed 40- or 64-character Git object id.
- `MATVERSE_BUILD_REF` must identify the deployed ref and be non-empty.
- `MATVERSE_FROZEN_CONTRACT_HASH` must be the frozen contract SHA-256 used by the pilot.
- `MATVERSE_RUNTIME_ID` must identify this deployment instance and must not be reused across unrelated deployments.
- `MATVERSE_PRINCIPALS_JSON` contains HMAC credentials and capabilities and must be provisioned by the hosting platform's secret store; never commit it to Git.
- `MATVERSE_DB` must point to persistent storage. Ephemeral container storage is not acceptable for a paid pilot.
- `MATVERSE_BUILD_TIMESTAMP` must be an RFC 3339 timestamp for the deployment build.

## Minimum pilot principal

Provision a dedicated customer principal with only the capabilities required for the scoped workflow. A typical institutional client starts with:

```text
institutional:projection:read
institutional:intent:submit
institutional:intent:read
```

Do not grant `*` or `institutional:intent:submit:any` unless the pilot explicitly requires delegated submission and that authority has been approved.

## Example container invocation

The following is a shape example only. Secret values must come from the deployment platform, not literal shell history or repository files.

```bash
docker run --rm \
  -p 8000:8000 \
  -v /srv/matverse/pilot:/data \
  -e MATVERSE_RUNTIME_ID="$MATVERSE_RUNTIME_ID" \
  -e MATVERSE_BUILD_COMMIT="$MATVERSE_BUILD_COMMIT" \
  -e MATVERSE_BUILD_REF="$MATVERSE_BUILD_REF" \
  -e MATVERSE_FROZEN_CONTRACT_HASH="$MATVERSE_FROZEN_CONTRACT_HASH" \
  -e MATVERSE_BUILD_TIMESTAMP="$MATVERSE_BUILD_TIMESTAMP" \
  -e MATVERSE_PRINCIPALS_JSON="$MATVERSE_PRINCIPALS_JSON" \
  -e MATVERSE_DB=/data/matverse-pilot.db \
  matverse-trust-runtime:pilot-v1
```

## HTTPS boundary

Expose port 8000 only behind a TLS-terminating reverse proxy or managed HTTPS service. The public pilot URL must use HTTPS. Do not expose the application directly over plaintext Internet HTTP.

The pilot host must preserve request bodies and the canonical five HMAC headers unchanged:

```text
X-MatVerse-Principal
X-MatVerse-Timestamp
X-MatVerse-Nonce
X-MatVerse-Content-SHA256
X-MatVerse-Signature
```

## Preflight

Before customer access:

1. Confirm `/health` returns service status.
2. Use a correctly signed request to `GET /institutional/runtime`.
3. Confirm `runtime_id`, authenticated principal, protocol version and source binding match the deployed instance.
4. Use a fresh nonce to obtain `GET /institutional/projection`.
5. Recompute and verify `projection_hash` client-side.
6. Confirm the source binding contains the exact deployed Git commit/ref and frozen contract.
7. Confirm `intent_execution = HOLD` before any operation-specific executor has been explicitly introduced.
8. Confirm the SQLite database is on persistent storage and the Ledger verifies.

Any mismatch is a deployment `HOLD`, not a reason to weaken validation.

## Customer-like acceptance run

Use the protocol sequence:

```text
signed GET /institutional/runtime
  -> signed GET /institutional/projection
  -> verify source binding + projection hash
  -> build source-bound institutional intent
  -> signed POST /institutional/intents
  -> acceptance_decision PASS or deterministic rejection/HOLD
  -> execution_decision HOLD unless separately authorized
  -> inspect receipt / Ledger / replay
```

The repository CI reference is implemented by:

```text
scripts/run_controlled_pilot_reference.py
.github/workflows/controlled-pilot-evidence.yml
```

A real pilot must use real deployment credentials and an external client. The synthetic CI credential is never a production credential.

## Evidence export

For every paid pilot acceptance scenario preserve at minimum:

- deployed Git object and ref;
- frozen contract hash;
- runtime id;
- principal id, without exporting the secret;
- source-bound projection hash;
- intent hash and parameter commitment;
- acceptance/execution decisions;
- Ledger receipt/head;
- replay result;
- claims matrix version;
- pilot scenario identifier and timestamp.

Raw secrets and hidden model reasoning must not enter the evidence export.

## GO / HOLD

`LIVE_CONTROLLED_PILOT = GO` only when all of these are true:

```text
HTTPS endpoint reachable
AND exact deployed Git binding verified
AND persistent storage verified
AND production runtime id provisioned
AND scoped production principal provisioned
AND HMAC secret stored outside source control
AND external customer-like request completed
AND evidence export completed
AND claims matrix attached
```

Otherwise:

```text
LIVE_CONTROLLED_PILOT = HOLD
```

Even after this gate passes, `EXTERNAL_PASS`, `WORLD_REAL_PASS`, scientific OCG claims and unrestricted production readiness remain separate gates.
