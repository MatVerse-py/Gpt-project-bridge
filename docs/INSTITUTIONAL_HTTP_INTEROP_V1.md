# MatVerse Institutional HTTP Interop v1

## Status

This document closes the protocol boundary between the canonical Python runtime and the TypeScript institutional surface without granting the surface canonical authority.

```text
TypeScript institutional surface
        |
        | HTTPS + HMAC-SHA256 request authentication
        v
Python canonical institutional adapter
        |
        | source-bound projection + intent acceptance commitment
        v
Ledger / Contract Registry / canonical runtime state
```

Protocol: `matverse.institutional-http.v1`.

## Deployment identity

External activation requires a deployed HTTPS endpoint plus:

```text
MATVERSE_RUNTIME_ID
MATVERSE_PRINCIPALS_JSON
MATVERSE_BUILD_COMMIT
MATVERSE_BUILD_REF
MATVERSE_FROZEN_CONTRACT_HASH
MATVERSE_DB
```

`MATVERSE_RUNTIME_ID` is an operational deployment identifier. It is not a scientific claim, a model identity, a public key, or a maturity state.

## Authentication

The TypeScript server signs each outbound request using the existing canonical HMAC mechanism. Secrets stay server-side.

```text
X-MatVerse-Principal
X-MatVerse-Timestamp
X-MatVerse-Nonce
X-MatVerse-Content-SHA256
X-MatVerse-Signature
```

Signing payload:

```text
METHOD
PATH
TIMESTAMP
NONCE
CONTENT_SHA256
```

`CONTENT_SHA256` is SHA-256 over the exact HTTP request body bytes. GET requests use the hash of the empty body. Nonces are one-time values; replay is rejected by canonical storage.

There is no `keyId` or PEM key in HMAC v1.

## Real activation handshake

The institutional surface MUST NOT mark a runtime VERIFIED by accepting a self-declared handshake JSON object.

The real handshake is outbound and observable:

1. `GET /institutional/runtime` with HMAC authentication.
2. Verify `runtime_id` equals the configured expected runtime id.
3. Verify `authenticated_principal_id` equals the configured principal.
4. Verify protocol and authentication identifiers.
5. `GET /institutional/projection` with a fresh nonce.
6. Recompute the projection hash with the shared RFC 8785/JCS subset.
7. Verify the runtime source binding equals the projection source binding.
8. Verify the runtime-reported projection hash equals the live projection hash.
9. Persist VERIFIED locally only after all checks pass.

`/institutional/runtime` returns `intent_execution = HOLD` deliberately. Connectivity does not authorize execution.

## Canonicalization

Both runtimes use the same restricted JSON domain:

- null;
- booleans;
- Unicode strings without lone UTF-16 surrogates;
- integers in `[-9007199254740991, 9007199254740991]`;
- arrays;
- objects with string keys.

Object property names are ordered by UTF-16 code units. Locale collation is forbidden. Floats are rejected.

## Intent flow

When the institutional surface creates an action it may keep a local non-canonical intent record. If a verified external configuration is present, the server fetches a fresh canonical projection and builds `matverse.institutional-intent.v1` using:

```text
intent_id
requested_operation
actor_id = authenticated principal
operation-specific target kind
parameters
created_at
complete source binding + projection_hash
intent_hash = SHA-256(JCS(intent without intent_hash))
```

Then it submits:

```text
POST /institutional/intents
```

A successful response must remain:

```text
acceptance_decision = PASS
execution_decision = HOLD
status = PENDING_EVALUATION
```

Any other combination is a boundary violation and must not be promoted locally.

## Stale projection

If canonical state advances between projection acquisition and intent submission, the adapter returns HTTP 409 / HOLD. The TypeScript surface must not silently rebuild the same intent under a new source binding with the same id. It must surface HOLD and require a new source-bound submission or an explicit retry policy preserving the original canonical envelope.

## Result observation

`matverse.institutional-http.v1` does not define `POST /institutional/receipts` on the TypeScript surface. The previous local callback design is not canonical.

Until an operation-specific executor is implemented, the surface observes canonical progress by authenticated reads of projection and actor-scoped intent state. A future callback protocol, if introduced, must receive its own version and contract rather than being inferred.

## Authority boundary

```text
authenticated request != authorized execution
intent accepted        != Omega PASS
Omega PASS             != execution evidence
execution              != scientific validation
```

No EXTERNAL_PASS, WORLD_REAL_PASS, SCIENTIFIC_PASS or INDEPENDENT_REPLICATION_PASS follows from this interop layer.
