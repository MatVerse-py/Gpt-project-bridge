# POST /bridge/ingress — P0 execution semantics

## Problem

The historical response set `executed` equal to `decision is Decision.PASS`, although this endpoint does **not** dispatch the requested `action`. This was a false positive for operational execution.

## Corrected contract

- `decision`: constitutional admissibility. `PASS` means the request passed the ingress gate, not that the requested action ran.
- `executed`: always `false` for `/bridge/ingress` because this endpoint has no action executor.
- `receipt`: evidence of **ingress ledger append**, not evidence of a requested action's execution. An HTTP 200 is returned only after the ledger append returns without error.
- `quarantined`: `true` when the decision is not PASS. PASS + `executed=false` + `quarantined=false` is valid and expected.

No new dispatcher, authorization authority, protocol or state machine is added by this patch.

## Breaking semantic change

Clients that used `executed=true` to mean "the requested action completed" must stop doing so. After this patch `/bridge/ingress` never reports that an operational effect took place. Review consumers before deployment. Clients requiring proof of execution must use an actual authorized execution protocol with an independently established outcome, not infer one from an ingress receipt.

The wire schema remains unchanged (same property names and JSON types), but the meaning and observed value of `executed` on PASS change.

## Verification

From the repository root:

```bash
python -m pytest -q tests/test_bridge_ingress.py
python -m pytest -q
git diff --check
```

The ingress tests cover PASS/HOLD/BLOCK, hash-chain/replay, simulated persistence failure, a real SQLite insert failure with rollback, invalid HMAC, replayed nonce and insufficient capability. A module-local fixture forces the institutional remote state URL off **only during these tests**; production behavior remains unchanged.

## Deferred issues

`ontology_ok=True` and `transition_valid=True` at the ingress call site require a separate contract/security investigation. `signature_valid=True` depends on upstream authenticated dependency execution but is not itself a per-request gate result. Do not claim they are validated here. External consumers and production rollout still require review. This patch does not prove exactly-once delivery or downstream execution.
