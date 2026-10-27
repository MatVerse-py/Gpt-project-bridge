# Captals ↔ Governed Physiology: P0 integration (candidate)

Status: **DRAFT / LOCAL_PROJECTION / NO_CAPITAL_PROMOTION / NO_DEPLOY**.

## Problem solved

The Bridge already has an operational causal loop, homeostatic policy,
post-effect feedback, durable SQLite journal, and binding to the existing
HDB / Ω-Gate. Captals already implements the separately governed metabolism
domain and the `POST /api/captals/evaluate` schema. We should **not**
create another Gate or copy Captals metabolic rules into Bridge.

This adapter translates a **real `ClosedLoopCycleResult` and its journal
entries** into a Captals `MetabolicEvent` payload, including a limited,
instrument-labelled CPU-time observation when available.

## Usage (from a Bridge service with a real engine)

```python
from app.captals_physiology_projection import record_projection

cycle = closed_loop_engine.tick(proposal={"action": "READ"})
metabolic_event, journal_row = record_projection(cycle, existing_journal)
# Send metabolic_event to the Captals API ONLY through the approved
# Bridge transport and service authorization. Not done by this adapter.
```

## Contracts and guardrails

- `MatVerse / Trust Kernel` keeps decision authority.
- `SymbiOS` stays the action executor.
- `Captals` is a consumer of a **projection**, not a replacement Gate.
- `capital_delta` remains empty: action success is not automatically value.
- `gate_decision` retains the governed decision; an absent decision maps HOLD.
- `evidence_status=UNVERIFIED` and `replay_status=NOT_AVAILABLE`
  are intentional: an HMAC or local SHA-256 is not an external trust root.
- Cost is only projected if the pre/post telemetry uses the same approved,
  named CPU-time instrument; deterministic_lab is labelled SIMULATED.
- A projection is stored idempotently using `DurableEventJournal.append`.
- No API calls, economic allocations, provider credentials or contract writes.
- Projection rejects missing or contradictory cycle / feedback receipts.
- The existing `app/core.py`, policy, HDB, Gate and physics engines are untouched.

## Integration boundary still HOLD

1. Captals API authentication, TLS, service identity and allowlisting.
2. Independent authority for VERIFIED status, authenticated replay and
   producer root; local receipt is insufficient.
3. Instrumented capital deltas with units, baseline, provenance and
   measurement authority; workload success is **not** capital gain.
4. Immutable or equivalently durable projection export and cross-process
   outbox/ack integration with retries. A local journal is not distributed exactly-once.
5. Governed cost budgets established **before execution**; this adapter reports
   CPU cost **after** an attempt and does not impose a preflight limit.
6. Version-locked compatibility check against Captals' installed schema, plus
   cross-repository CI. Tests in this PR only verify the Bridge boundary.

## Quick tests

```bash
python -m pytest -q tests/test_captals_physiology_projection.py
```

Passing this suite supports **LOCAL_BOUNDARY_PASS only**. It does not grant
`WORLD_REAL_PASS`, `EXTERNAL_PASS`, DeFi activation or a canonical merge.
