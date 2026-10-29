# MVKit v2 — experimental shadow integration

**Status:** `IMPLEMENTATION_CANDIDATE / HOLD_PRODUCTION / NO_CANONICAL_AUTHORITY`.

## Contract

- `app.core.omega_gate` is the **constitutional** gate (HDB, ontology, signatures, transitions); it is not numerically equivalent to the MVKit experimental `omega_gate.py` scoring function.
- `app.mvkit_shadow` is **opt-in** and has no routes, jobs, database writes, provider calls, execution grants, `COMMIT`, or policy promotion. The `comparison_hash` is a local checksum, not an authenticated receipt.
- `compare_shadow` requires an existing `app.core.Decision` and returns a **comparison**, never a new canonical decision. `SHADOW_HOLD` on missing scorer, invalid archive, or invalid candidate output.
- `load_pinned_scorer` accepts only the exact user-supplied `mvkit_v2_hardened.zip` (SHA-256 `f14a7c72fbffcc2ac2bef13ea84960680e62f276d6329a16bee80200fd8a6da0`), and checks the embedded source digest. It runs reviewed Python code **inside the caller process** and therefore must run in an isolated/disposable test environment, not the public runtime.
- `numpy` is an **optional experimental dependency** of the archive, not added to production `requirements.txt`.
- This adapter does **not** adopt the experimental MENBIT ledger. Its in-memory, unverifiable-from-external-root hash chain is not interchangeable with production Ledger/EvidenceOS. No v1→v2 migration.

## Local evaluation

After installing optional test dependencies in a disposable environment, use:

```python
from pathlib import Path
from app.core import Decision
from app.mvkit_shadow import compare_archive

result = compare_archive(
    archive_bytes=Path("mvkit_v2_hardened.zip").read_bytes(),
    canonical_decision=Decision.HOLD,  # value from real canonical runtime
    samples=[0.99] * 40, theta_hat=0.95, cvar=0.02, pole=True,
)
assert result["canonical_decision"] == "HOLD"  # never promoted
print(result)
```

`pytest -q tests/test_mvkit_shadow.py` tests the non-authority and error paths without installing NumPy. The 44 MVKit unit/adversarial tests and B₆ evaluation remain independently in the user-provided experimental ZIP; their pass status is **not** a repo CI pass until the workflows execute.

## Promotion HOLD

Requires (1) independent scientific ratification of policy, (2) authenticated sample provenance + effective sample-size dependence treatment, (3) separate ledger durable/storage and identity migration design, (4) CI with optional experimental dependency, (5) human authorization. Do not merge this as a replacement gate.
