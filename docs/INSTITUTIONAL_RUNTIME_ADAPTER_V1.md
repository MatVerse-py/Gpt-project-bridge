# MatVerse Institutional Runtime Adapter v1

## Purpose

This adapter is the executable boundary between the canonical MatVerse runtime and institutional surfaces such as Manus dashboards.

It is **not a second constitutional runtime**. It reuses the canonical repository's authentication, capability model, Ledger, Contract Registry, constitutional guard fingerprint and contract binding.

```text
CANONICAL MATVERSE CORE
Bridge / Federation / HDB / Omega / Organism / Ledger
        |
        | deterministic source-bound projection
        v
INSTITUTIONAL ADAPTER
GET projection / POST intent / GET own intents
        |
        v
MANUS / DRIZZLE / OTHER UI
read model + non-canonical intent creation only
```

## Service

Run the adapter as:

```bash
uvicorn app.institutional_service:app --host 0.0.0.0 --port 8001
```

The adapter may be deployed as a separate process for operational isolation, but it MUST share or securely access canonical runtime state and MUST NOT become an independent authority.

## Required production configuration

```text
MATVERSE_DB
MATVERSE_PRINCIPALS_JSON
MATVERSE_BUILD_COMMIT
MATVERSE_BUILD_REF
MATVERSE_FROZEN_CONTRACT_HASH
MATVERSE_BUILD_TIMESTAMP   # optional when canonical ledger has usable timestamps
```

`MATVERSE_BUILD_COMMIT` MUST identify the deployed Git object. `MATVERSE_FROZEN_CONTRACT_HASH` MUST be the frozen contract actually governing the deployment. Neither value may be invented or defaulted optimistically.

If these bindings are absent or invalid, projection availability fails closed.

## Authentication

The adapter reuses `app.auth.authenticate`.

Requests carry:

```text
X-MatVerse-Principal
X-MatVerse-Timestamp
X-MatVerse-Nonce
X-MatVerse-Content-SHA256
X-MatVerse-Signature
```

The canonical authentication layer validates HMAC, request body hash, timestamp window, principal registry, capabilities and nonce replay.

Required capabilities:

```text
institutional:projection:read
institutional:intent:submit
institutional:intent:read
```

Optional elevated capabilities:

```text
institutional:intent:submit:any
institutional:intent:read:any
```

The normal Manus principal SHOULD NOT receive the elevated `:any` capabilities.

## Projection endpoint

```text
GET /institutional/projection
```

The response follows `contracts/institutional-surface-v1.schema.json` and contains a source binding to:

```text
repository
commit_sha
ref
frozen_contract_hash
gate_fingerprint
constitutional_contract_hash
projection_hash
```

The projection is generated from canonical state. The adapter does not synthesize scientific claims, maturity promotions, identities or relations that are not canonically represented.

Current projectable material includes canonical Contract Registry artifacts and Ledger receipts. Unmaterialized institutional domains remain empty rather than being fabricated.

An artifact projected as `PASS` in `contract-registry/...` scope means only that the exact content hash exists in the canonical Contract Registry and has a matching canonical registration receipt. It does not mean that the artifact's scientific, security or operational claims are independently validated.

When the Ledger has no events, the projection contains an explicit deterministic `LEDGER_GENESIS_COMMITMENT` receipt rather than an invisible or fabricated event receipt.

Before exposure, every generated projection is passed through the deterministic institutional semantic validator. If Ledger integrity, deployment binding or projection semantics fail, the endpoint fails closed instead of returning an optimistic `LIVE` state.

## Deterministic projection hashing

Projection hashes use the interoperable subset of RFC 8785 JCS implemented by `app.institutional_projection`.

The v1 subset accepts:

- null;
- boolean;
- Unicode strings without lone UTF-16 surrogates;
- integers within `[-9007199254740991, 9007199254740991]`;
- arrays of accepted values;
- objects with string keys and accepted values.

Floating-point values are deliberately rejected in v1 institutional canonical payloads to remove cross-runtime number-format ambiguity.

Object properties are ordered by UTF-16 code units as required by RFC 8785. `projection.projection_hash` is omitted from the hashed document to avoid self-reference.

## Intent endpoint

```text
POST /institutional/intents
```

Input follows `contracts/institutional-intent-v1.schema.json`.

An intent MUST be bound to the exact canonical projection visible when the user requested the action. For a new intent, the adapter compares every source-binding field and `projection_hash` with a freshly generated canonical projection.

If canonical state advanced since the UI snapshot, the request is not silently applied:

```text
stale projection -> HOLD / HTTP 409
```

The UI must refresh its projection and let the user create a new intent against current state.

## Intent acceptance is not execution

A successful submission means only:

```text
acceptance_decision = PASS
execution_decision = HOLD
status = PENDING_EVALUATION
parameter_persistence = HASH_ONLY
```

It does **not** mean the requested action passed Omega, executed, changed maturity, published anything, anchored anything, or modified canonical policy.

The adapter atomically persists an intent commitment and appends an `INSTITUTIONAL_INTENT_ACCEPTED` event to the canonical Ledger. Canonical persistence contains the JCS/SHA-256 parameter commitment, not the raw parameter object.

This is deliberate: receiving an authenticated intent is not sufficient authority to persist arbitrary operational or human-sensitive payloads before the relevant HDB/Omega/authorization stage.

The institutional UI may retain the raw draft locally under its own privacy controls. A future canonical executor must require the payload to be resubmitted, verify that it reproduces `parameters_hash`, and then apply the operation-specific HDB, capability, Omega and evidence gates before execution or canonical persistence.

## Idempotency

The same authenticated principal may retry the exact same `intent_id` + `intent_hash` after network failure.

The first acceptance advances the Ledger and therefore changes the current projection. Exact retries are nevertheless returned idempotently from the stored acceptance commitment without creating a second Ledger event.

Mutating the content under an existing `intent_id` is rejected.

## Private-state boundary

Intent parameters reuse the Model Bridge private-state prohibition. Fields representing hidden or private model state are rejected, including canonical forms of:

```text
chain_of_thought
reasoning_trace
hidden_reasoning
hidden_state
private_memory
system_prompt
developer_prompt
credentials
api_key
secret_key
access_token
refresh_token
password
```

No institutional workflow requires transport of hidden reasoning or credentials as evidence. Raw credentials and secret material must never be placed inside an intent parameter object.

## Read endpoints

```text
GET /institutional/intents
GET /institutional/intents/{intent_id}
```

By default a principal can read only its own intent commitments. Cross-actor reads require explicit `institutional:intent:read:any` capability. Read responses expose `parameters_hash`, not raw submitted parameters.

## Manus binding

The Manus application should use this adapter as follows:

```text
Dashboard load
-> GET /institutional/projection
-> persist as read model/cache with source metadata

User clicks Verify / Publish / Evaluate / Anchor / Register
-> construct matverse.institutional-intent.v1
-> POST /institutional/intents
-> show ACCEPTED / PENDING_EVALUATION
-> never show canonical PASS from submission response

Later refresh
-> GET /institutional/projection
-> replace local projection idempotently
```

Drizzle remains useful for UI search, pagination, local drafts and cached projections. It does not become canonical truth.

## Explicit non-claims

This adapter does not establish:

```text
EXTERNAL_PASS
WORLD_REAL_PASS
SCIENTIFIC_PASS
INDEPENDENT_REPLICATION_PASS
```

Those states remain governed by their own evidence and independence requirements.
