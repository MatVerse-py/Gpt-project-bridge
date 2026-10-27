"""Read-only bridge from governed physiology to the Captals metabolic API.

No parallel Omega/Gate, no automatic allocation, no invention of capital gains.
A local receipt is not an independently verified scientific claim. This adapter
projects local instrumentation but deliberately preserves UNVERIFIED / NOT_AVAILABLE.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .core import Decision
from .evidence import evidence_receipt
from .physiology import DurableEventJournal, JournalEvent
from .physiology_effect_feedback import ClosedLoopCycleResult, SCHEMA_VERSION as FEEDBACK_SCHEMA

SCHEMA = "matverse.captals-physiology-projection.v0"
REAL_CPU_INSTRUMENT = "time.process_time_ns"
SIM_CPU_INSTRUMENT = "deterministic_lab.logical_cpu"


def _cycle_events(journal: DurableEventJournal, cycle_id: str) -> dict[str, JournalEvent]:
    found: dict[str, JournalEvent] = {}
    after_seq = 0
    while True:
        batch = journal.read(after_seq=after_seq, limit=1000, topic="physiology")
        if not batch:
            break
        for item in batch:
            if item.correlation_id != cycle_id:
                continue
            if item.event_type in found:
                raise ValueError(f"duplicate event type in cycle: {item.event_type}")
            found[item.event_type] = item
        after_seq = batch[-1].seq
    return found


def _validate_event_receipts(result: ClosedLoopCycleResult, events: Mapping[str, JournalEvent]) -> None:
    memory = events.get("MEMORY_COMMIT")
    if memory is None:
        raise ValueError("missing durable memory commit")
    if memory.event_id != f"{result.cycle.cycle_id}:memory":
        raise ValueError("memory commit does not belong to cycle")
    recorded = memory.payload.get("cycle_receipt")
    if not isinstance(recorded, dict):
        raise ValueError("missing cycle receipt")
    inputs = {key: value for key, value in memory.payload.items() if key != "cycle_receipt"}
    recomputed = evidence_receipt("PHYSIOLOGY_CYCLE", inputs, {"closed": True})
    if recorded != recomputed or recorded["receipt_hash"] != result.cycle.receipt_hash:
        raise ValueError("cycle receipt mismatch")
    if inputs.get("cycle_id") != result.cycle.cycle_id:
        raise ValueError("cycle identity mismatch")
    if inputs.get("state_root") != result.cycle.state_root:
        raise ValueError("cycle state root mismatch")
    feedback = events.get("EFFECT_FEEDBACK")
    recovery_gate = events.get("EFFECT_RECOVERY_GATE")
    if feedback is not None and recovery_gate is not None:
        raise ValueError("conflicting feedback and recovery gate")
    if result.feedback_receipt_hash is not None:
        if feedback is not None:
            if feedback.event_id != f"{result.cycle.cycle_id}:effect-feedback":
                raise ValueError("effect feedback bound to wrong cycle")
            data = feedback.payload
            if not isinstance(data.get("inputs"), dict) or not isinstance(data.get("outputs"), dict):
                raise ValueError("invalid feedback structure")
            expected = evidence_receipt("PHYSIOLOGY_EFFECT_FEEDBACK", data["inputs"], data["outputs"])
            if data.get("receipt") != expected or expected["receipt_hash"] != result.feedback_receipt_hash:
                raise ValueError("effect feedback receipt mismatch")
            if data["inputs"].get("cycle_receipt_hash") != result.cycle.receipt_hash:
                raise ValueError("effect feedback not bound to cycle")
        elif recovery_gate is not None:
            if (recovery_gate.event_id != f"{result.cycle.cycle_id}:effect-recovery-gate"
                or result.effective_decision is not Decision.HOLD
                or not result.recovery_required
                or not result.recovery_for_cycle_id):
                raise ValueError("invalid recovery HOLD gate")
            gate_payload = recovery_gate.payload
            if gate_payload.get("decision") != "HOLD" or not isinstance(gate_payload.get("reason"), str):
                raise ValueError("recovery HOLD gate invalid")
            expected = evidence_receipt(
                "PHYSIOLOGY_EFFECT_RECOVERY_GATE",
                {"schema": FEEDBACK_SCHEMA, "cycle_id": result.cycle.cycle_id,
                 "recovery_for_cycle_id": result.recovery_for_cycle_id},
                {"decision": "HOLD", "reason": gate_payload["reason"]},
            )
            if gate_payload.get("receipt") != expected or expected["receipt_hash"] != result.feedback_receipt_hash:
                raise ValueError("recovery HOLD receipt mismatch")
        else:
            raise ValueError("missing effect feedback or recovery gate")
    elif feedback is not None or recovery_gate is not None:
        raise ValueError("feedback exists but result does not bind it")


def _cpu_cost(events: Mapping[str, JournalEvent]) -> tuple[dict[str, Any], str | None]:
    before = events.get("OBSERVATION")
    after = events.get("EFFECT_FEEDBACK")
    if before is None or after is None:
        return {}, None
    start = before.payload.get("telemetry", {})
    finish = after.payload.get("inputs", {}).get("post_effect_telemetry")
    if not isinstance(start, dict) or not isinstance(finish, dict):
        return {}, None
    start_instrument = start.get("instruments", {}).get("process_time_ns")
    finish_instrument = finish.get("instruments", {}).get("process_time_ns")
    if start_instrument != finish_instrument or start_instrument not in (
        REAL_CPU_INSTRUMENT, SIM_CPU_INSTRUMENT
    ):
        return {}, None
    start_ns, finish_ns = start.get("process_time_ns"), finish.get("process_time_ns")
    if type(start_ns) is not int or type(finish_ns) is not int or start_ns < 0 or finish_ns < start_ns:
        return {}, None
    seconds = (finish_ns - start_ns) / 1_000_000_000
    if not math.isfinite(seconds):
        return {}, None
    return {
        "computational": {
            "value": seconds,
            "unit": "cpu_second",
            "instrument": start_instrument,
        }
    }, start_instrument


def project_cycle(result: ClosedLoopCycleResult, journal: DurableEventJournal) -> dict[str, Any]:
    """Build an input compatible with POST /api/captals/evaluate.

    Does not call Captals or grant financial authority. External evidence/replay
    authentication must be handled by a separately authorized integration.
    """
    if not isinstance(result, ClosedLoopCycleResult):
        raise TypeError("expected real ClosedLoopCycleResult")
    if not journal.integrity_check():
        raise ValueError("journal failed SQLite integrity check")
    cycle_id = result.cycle.cycle_id
    events = _cycle_events(journal, cycle_id)
    _validate_event_receipts(result, events)

    decision = result.effective_decision
    if decision is not None and not isinstance(decision, Decision):
        raise ValueError("invalid governed decision")
    if decision is Decision.PASS and result.cycle.decision is not Decision.PASS:
        raise ValueError("effective PASS without constitutional PASS")
    if result.recovery_required and decision is Decision.PASS:
        # The gate admitted an attempt, but recovery still prevents promotion.
        # Captals receives the original decision; evidence remains unverified.
        pass
    costs, instrument = _cpu_cost(events)
    source = (
        "bridge.physiology.simulated"
        if instrument == SIM_CPU_INSTRUMENT
        else "bridge.physiology.local"
    )
    return {
        "event_id": cycle_id,
        "track_id": "cycle:" + cycle_id,
        "source": source,
        "gate_decision": Decision.HOLD.value if decision is None else decision.value,
        "evidence_status": "UNVERIFIED",
        "replay_status": "NOT_AVAILABLE",
        "capital_delta": {},
        "costs": costs,
        "budget_limits": {},
        "provenance_ref": f"physiology:{cycle_id}:memory",
        "receipt_ref": result.cycle.receipt_hash,
        "legacy_omega_score": None,
    }


def record_projection(
    result: ClosedLoopCycleResult, journal: DurableEventJournal
) -> tuple[dict[str, Any], JournalEvent]:
    """Idempotently store a read-only projection in the existing local journal."""
    projected = project_cycle(result, journal)
    event = journal.append(
        event_id=f"{result.cycle.cycle_id}:captals-projection",
        topic="captals",
        event_type="METABOLIC_PROJECTION",
        payload={"schema": SCHEMA, "metabolic_event": projected, "promotion": "HOLD"},
        causation_id=f"{result.cycle.cycle_id}:memory",
        correlation_id=result.cycle.cycle_id,
    )
    return projected, event
