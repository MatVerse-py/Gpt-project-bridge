# Controlled Pilot Reference Scenario v1

Status: `CI_CUSTOMER_LIKE_REFERENCE`

This scenario is the executable commercial reference for **MatVerse Trust Runtime — Controlled Pilot v1**. It proves the bounded institutional control path in CI; it is not a live customer deployment and does not promote `EXTERNAL_PASS`, `WORLD_REAL_PASS`, `SCIENTIFIC_PASS`, or unrestricted production readiness.

## Buyer-like scenario

An authenticated customer principal requests authorization for an AI/agent workflow to export one document from a sandboxed reference resource.

```text
customer-like principal
        |
        | HMAC authenticated request
        v
/institutional/runtime
        |
        v
source-bound canonical projection
        |
        v
REQUEST_AUTHORIZATION intent
        |
        +--> acceptance = PASS
        +--> execution  = HOLD
        +--> status     = PENDING_EVALUATION
        |
        v
hash-only parameter commitment
        |
        v
Ledger receipt -> chain verification -> replay
```

The scenario deliberately stops before execution. The current institutional protocol accepts an authenticated, source-bound intent; it does not grant a generic auto-executor authority. That fail-closed boundary is part of the product evidence rather than a missing claim being hidden.

## Executable artifact

Run:

```bash
python scripts/run_controlled_pilot_reference.py \
  --build-commit <DEPLOYED_GIT_OBJECT> \
  --build-ref <DEPLOYED_REF> \
  --output evidence/pilot-v1/EVIDENCE_PACK.json
```

CI runs the same scenario in `.github/workflows/controlled-pilot-evidence.yml` and uploads the resulting Evidence Pack as a workflow artifact.

## Assertions

The scenario fails if any of these conditions are not met:

1. authenticated runtime handshake returns `READY`;
2. runtime identity is provisioned;
3. runtime and projection share the same source binding and projection hash;
4. the submitted intent is bound to the live projection;
5. `acceptance_decision = PASS`;
6. `execution_decision = HOLD`;
7. `status = PENDING_EVALUATION`;
8. the canonical Ledger contains exactly the acceptance event for the isolated run;
9. raw intent parameters are absent from the Ledger and only `parameters_hash` is persisted;
10. the Ledger hash chain verifies;
11. observable replay reconstructs the accepted event state.

## Evidence classification

A green run authorizes only:

```text
CONTROLLED_PILOT_REFERENCE = PASS
```

It leaves:

```text
LIVE_CUSTOMER_PILOT = HOLD
LIVE_HTTPS_DEPLOYMENT = HOLD until a real endpoint is provisioned
EXTERNAL_PASS = HOLD
WORLD_REAL_PASS = HOLD
UNRESTRICTED_PRODUCTION_READINESS = HOLD
```

## Deployment image

The repository's original `Dockerfile` serves `app.main:app`. The institutional product surface lives at `app.institutional_service:app`.

For that reason the controlled pilot uses the separate `Dockerfile.institutional`. This preserves the existing canonical runtime entrypoint and avoids silently changing the meaning of the original image.

## Production boundary

The CI scenario uses a synthetic HMAC credential solely to exercise the protocol. A real pilot must provision its principal and HMAC secret in a secure external secret store and must never reuse the fixture credential.

A real paid pilot becomes `GO` only after live HTTPS deployment, runtime identity, secure principal provisioning, customer-like external access, exportable evidence, deployment runbook, claims matrix, and commercial license boundary are all closed under Issue #10.
