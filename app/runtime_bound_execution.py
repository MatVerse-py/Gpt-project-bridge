from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .core import Decision, stable_hash
from .evidence import canonical_json, evidence_receipt
from .physiology import CycleResult, ExecutionResult, PhysiologyEngine
from .runtime_binding import validate_execution_binding

SCHEMA_VERSION = "matverse.runtime-bound-execution.v1"
REPLAY_EXACT = "EXACT_MATCH"
REPLAY_DIVERGENT = "DIVERGENT"
REPLAY_HOLD = "HOLD"

BoundDelegate = Callable[[Mapping[str, Any], Mapping[str, Any]], ExecutionResult]


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _runtime_identity(binding: Mapping[str, Any]) -> dict[str, Any]:
    runtime = binding.get("runtime")
    model = binding.get("model")
    container = binding.get("container")
    return {
        "runtime": _clone(runtime) if isinstance(runtime, Mapping) else None,
        "model": _clone(model) if isinstance(model, Mapping) else None,
        "container": _clone(container) if isinstance(container, Mapping) else None,
    }


@dataclass(frozen=True)
class RuntimeBoundExecutor:
    """Executor envelope pinned to one validated runtime binding.

    The envelope does not grant authority. Physiology still invokes it only after
    GovernedOrganism/HDB/Ω returns PASS. The delegate receives the exact bound
    identity as a second argument so runtime adapters cannot accidentally lose
    the execution context chosen during discovery/binding.
    """

    binding_hash: str
    discovery_report_hash: str
    identity_json: str
    delegate: BoundDelegate = field(repr=False, compare=False)

    @property
    def identity(self) -> dict[str, Any]:
        return json.loads(self.identity_json)

    def __call__(self, proposal: Mapping[str, Any]) -> ExecutionResult:
        result = self.delegate(_clone(dict(proposal)), self.identity)
        if not isinstance(result, ExecutionResult):
            raise TypeError("runtime-bound delegate must return ExecutionResult")
        return result


def bind_executor(binding: Mapping[str, Any], delegate: BoundDelegate) -> RuntimeBoundExecutor:
    snapshot = _clone(dict(binding))
    valid, reason = validate_execution_binding(snapshot)
    if not valid:
        raise ValueError(f"invalid execution binding: {reason}")
    if not callable(delegate):
        raise TypeError("delegate must be callable")
    return RuntimeBoundExecutor(
        binding_hash=str(snapshot["binding_hash"]),
        discovery_report_hash=str(snapshot["discovery_report_hash"]),
        identity_json=canonical_json(_runtime_identity(snapshot)),
        delegate=delegate,
    )


def _decision_reason(engine: PhysiologyEngine, cycle_id: str) -> str:
    events = engine.journal.read(limit=10_000, topic="physiology")
    for event in reversed(events):
        if event.correlation_id == cycle_id and event.event_type == "DECISION":
            reason = event.payload.get("reason")
            return str(reason) if reason is not None else "decision_recorded"
    return "no_decision_event"


def _preflight_hold(
    *,
    binding: Mapping[str, Any],
    proposal_hash: str,
    reason: str,
    organism_id: str,
    constitutional_contract_hash: str,
) -> dict[str, Any]:
    inputs = {
        "schema": SCHEMA_VERSION,
        "binding_hash": binding.get("binding_hash"),
        "discovery_report_hash": binding.get("discovery_report_hash"),
        "runtime_identity": _runtime_identity(binding),
        "proposal_hash": proposal_hash,
        "organism_id": organism_id,
        "constitutional_contract_hash": constitutional_contract_hash,
    }
    outputs = {"decision": Decision.HOLD.value, "executed": False, "reason": reason}
    receipt = evidence_receipt("RUNTIME_BOUND_EXECUTION_PREFLIGHT", inputs, outputs)
    core = {
        "schema": SCHEMA_VERSION,
        "status": "HOLD",
        "decision": Decision.HOLD.value,
        "executed": False,
        "reason": reason,
        "binding": _clone(dict(binding)),
        "proposal_hash": proposal_hash,
        "cycle_id": None,
        "cycle_receipt_hash": None,
        "runtime_identity": _runtime_identity(binding),
        "receipt_inputs": inputs,
        "receipt_outputs": outputs,
        "receipt": receipt,
    }
    return {**core, "record_hash": stable_hash(core)}


def execute_bound_workload(
    *,
    engine: PhysiologyEngine,
    binding: Mapping[str, Any],
    proposal: Mapping[str, Any],
    human: Mapping[str, Any] | None = None,
    ontology_ok: bool = True,
    signature_valid: bool = True,
    transition_valid: bool = True,
) -> dict[str, Any]:
    """Execute one workload with identity binding as a precondition, never as authority."""

    binding_snapshot = _clone(dict(binding))
    proposal_snapshot = _clone(dict(proposal))
    proposal_hash = stable_hash(proposal_snapshot)
    valid, reason = validate_execution_binding(binding_snapshot)
    if not valid:
        return _preflight_hold(
            binding=binding_snapshot,
            proposal_hash=proposal_hash,
            reason=f"invalid_binding:{reason}",
            organism_id=engine.organism.organism_id,
            constitutional_contract_hash=engine.organism.constitutional_contract_hash,
        )

    executor = engine.executor
    if not isinstance(executor, RuntimeBoundExecutor):
        return _preflight_hold(
            binding=binding_snapshot,
            proposal_hash=proposal_hash,
            reason="executor_not_runtime_bound",
            organism_id=engine.organism.organism_id,
            constitutional_contract_hash=engine.organism.constitutional_contract_hash,
        )
    expected_identity = _runtime_identity(binding_snapshot)
    if executor.binding_hash != binding_snapshot["binding_hash"] or executor.identity != expected_identity:
        return _preflight_hold(
            binding=binding_snapshot,
            proposal_hash=proposal_hash,
            reason="executor_binding_identity_mismatch",
            organism_id=engine.organism.organism_id,
            constitutional_contract_hash=engine.organism.constitutional_contract_hash,
        )

    cycle: CycleResult = engine.tick(
        proposal=proposal_snapshot,
        human=human,
        ontology_ok=ontology_ok,
        signature_valid=signature_valid,
        transition_valid=transition_valid,
    )
    decision = None if cycle.decision is None else cycle.decision.value
    decision_reason = _decision_reason(engine, cycle.cycle_id)
    inputs = {
        "schema": SCHEMA_VERSION,
        "binding_hash": binding_snapshot["binding_hash"],
        "discovery_report_hash": binding_snapshot["discovery_report_hash"],
        "runtime_identity": expected_identity,
        "proposal_hash": proposal_hash,
        "organism_id": engine.organism.organism_id,
        "constitutional_contract_hash": engine.organism.constitutional_contract_hash,
    }
    outputs = {
        "cycle_id": cycle.cycle_id,
        "decision": decision,
        "executed": cycle.executed,
        "reason": decision_reason,
        "state_root": cycle.state_root,
        "cycle_receipt_hash": cycle.receipt_hash,
    }
    receipt = evidence_receipt("RUNTIME_BOUND_EXECUTION", inputs, outputs)
    payload = {
        "binding_hash": binding_snapshot["binding_hash"],
        "discovery_report_hash": binding_snapshot["discovery_report_hash"],
        "runtime_identity": expected_identity,
        "proposal_hash": proposal_hash,
        "decision": decision,
        "executed": cycle.executed,
        "reason": decision_reason,
        "cycle_receipt_hash": cycle.receipt_hash,
        "receipt": receipt,
    }
    engine.journal.append(
        event_id=f"{cycle.cycle_id}:runtime-bound-execution",
        topic="runtime-binding",
        event_type="RUNTIME_BOUND_EXECUTION",
        payload=payload,
        causation_id=f"{cycle.cycle_id}:memory",
        correlation_id=cycle.cycle_id,
    )
    core = {
        "schema": SCHEMA_VERSION,
        "status": "RECORDED",
        "decision": decision,
        "executed": cycle.executed,
        "reason": decision_reason,
        "binding": binding_snapshot,
        "proposal_hash": proposal_hash,
        "cycle_id": cycle.cycle_id,
        "cycle_receipt_hash": cycle.receipt_hash,
        "runtime_identity": expected_identity,
        "receipt_inputs": inputs,
        "receipt_outputs": outputs,
        "receipt": receipt,
    }
    return {**core, "record_hash": stable_hash(core)}


def replay_bound_execution_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Replay the observable receipt graph; this is not a provider re-execution."""

    snapshot = _clone(dict(record))
    if snapshot.get("schema") != SCHEMA_VERSION:
        return {"status": REPLAY_HOLD, "reason": "unsupported_record_schema"}

    supplied_record_hash = snapshot.pop("record_hash", None)
    if not isinstance(supplied_record_hash, str) or stable_hash(snapshot) != supplied_record_hash:
        return {"status": REPLAY_DIVERGENT, "reason": "record_hash_mismatch"}

    binding = snapshot.get("binding")
    if not isinstance(binding, dict):
        return {"status": REPLAY_HOLD, "reason": "binding_missing"}
    valid, reason = validate_execution_binding(binding)
    if not valid:
        return {"status": REPLAY_DIVERGENT, "reason": f"binding_invalid:{reason}"}

    inputs = snapshot.get("receipt_inputs")
    outputs = snapshot.get("receipt_outputs")
    receipt = snapshot.get("receipt")
    if not isinstance(inputs, dict) or not isinstance(outputs, dict) or not isinstance(receipt, dict):
        return {"status": REPLAY_HOLD, "reason": "receipt_material_missing"}
    expected_receipt = evidence_receipt(
        "RUNTIME_BOUND_EXECUTION" if snapshot.get("status") == "RECORDED" else "RUNTIME_BOUND_EXECUTION_PREFLIGHT",
        inputs,
        outputs,
    )
    if expected_receipt != receipt:
        return {"status": REPLAY_DIVERGENT, "reason": "receipt_mismatch"}
    if inputs.get("binding_hash") != binding.get("binding_hash"):
        return {"status": REPLAY_DIVERGENT, "reason": "binding_receipt_link_mismatch"}

    return {
        "status": REPLAY_EXACT,
        "reason": "observable_receipt_graph_matches",
        "record_hash": supplied_record_hash,
        "binding_hash": binding.get("binding_hash"),
        "cycle_id": snapshot.get("cycle_id"),
    }
