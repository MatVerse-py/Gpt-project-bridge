from __future__ import annotations

import json
from pathlib import Path

from app.core import Decision, stable_hash
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.homeostatic_supervisor import (
    BoundedHomeostaticSupervisor,
    BoundedRecoveryPolicy,
)
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult, HealthState
from app.physiology_effect_feedback import ClosedLoopPhysiologyEngine

JOURNAL_PATH = Path("bounded-autonomous-recovery-v1.sqlite3")
EVIDENCE_PATH = Path("bounded-autonomous-recovery-v1-evidence.json")


def _reset_journal() -> None:
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(JOURNAL_PATH) + suffix)
        if path.exists():
            path.unlink()


def main() -> int:
    _reset_journal()
    journal = DurableEventJournal(JOURNAL_PATH)
    organism = GovernedOrganism(
        organism_id="bounded-autonomous-exp-organism",
        frozen_contract_hash="d" * 64,
        runtime_id="bounded-autonomous-exp-runtime",
        state_secret="bounded-autonomous-exp-state",
        authority_secrets={"independent-authorizer": "bounded-autonomous-exp-authority"},
    )
    telemetry = DeterministicTelemetry(
        plan=DeterministicFaultPlan(seed=11, cycles=20, directives=())
    )

    calls: list[dict] = []

    def executor(proposal):
        calls.append(dict(proposal))
        if len(calls) == 1:
            return ExecutionResult(status="FAIL", effect={"fault": "transient"})
        return ExecutionResult(status="OK", effect={"recovered": True})

    engine = ClosedLoopPhysiologyEngine(
        organism=organism,
        journal=journal,
        telemetry=telemetry,
        executor=executor,
        feedback_state_secret="bounded-autonomous-exp-feedback",
    )
    supervisor = BoundedHomeostaticSupervisor(
        engine=engine,
        recovery_policy=BoundedRecoveryPolicy.build(
            action="READ",
            parameters={"resource": "sensor"},
        ),
    )

    perturbation = supervisor.step(
        proposal={"action": "READ", "resource": "sensor"}
    )
    origin = perturbation.cycle.recovery_for_cycle_id
    recovery = supervisor.step()

    events = journal.read(limit=500)
    proposal_events = [
        event
        for event in events
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_PROPOSED"
    ]
    outcome_events = [
        event
        for event in events
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_OUTCOME"
    ]

    bounded_pass = bool(
        perturbation.cycle.effective_decision is Decision.PASS
        and perturbation.cycle.effect_status == "FAIL"
        and perturbation.cycle.recovery_required
        and origin is not None
        and recovery.autonomous_recovery_attempted
        and recovery.recovery_origin_cycle_id == origin
        and recovery.cycle.effective_decision is Decision.PASS
        and recovery.cycle.cycle.executed
        and recovery.cycle.effect_status == "OK"
        and recovery.cycle.post_effect_health is HealthState.NORMAL
        and not recovery.cycle.recovery_required
        and len(calls) == 2
        and calls[1].get("recovery_for") == origin
        and len(proposal_events) == 1
        and len(outcome_events) == 1
        and proposal_events[0].causation_id
        == f"{perturbation.cycle.cycle.cycle_id}:effect-feedback"
        and "inputs" in outcome_events[0].payload
    )

    evidence = {
        "schema": "matverse.bounded-autonomous-recovery-evidence.v1",
        "journal_integrity": journal.integrity_check(),
        "events": [
            {
                "seq": event.seq,
                "event_id": event.event_id,
                "topic": event.topic,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_ns": event.created_ns,
                "causation_id": event.causation_id,
                "correlation_id": event.correlation_id,
                "receipt_hash": event.receipt_hash,
            }
            for event in events
        ],
    }
    evidence["evidence_hash"] = stable_hash(evidence)
    with EVIDENCE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(evidence, fh, sort_keys=True, indent=2)
        fh.write("\n")

    report = {
        "schema": "matverse.bounded-autonomous-recovery-experiment.v1",
        "scope": "LOCAL_CONTROLLED_DETERMINISTIC_LAB",
        "evidence_class": "SIMULATED_INSTRUMENTED",
        "human_runtime_intervention_between_failure_and_recovery": False,
        "policy_predeclared": True,
        "ordinary_hdb_omega_path_preserved": True,
        "perturbation": {
            "cycle_id": perturbation.cycle.cycle.cycle_id,
            "decision": perturbation.cycle.effective_decision.value,
            "effect_status": perturbation.cycle.effect_status,
            "recovery_required": perturbation.cycle.recovery_required,
        },
        "recovery": {
            "origin_cycle_id": origin,
            "cycle_id": recovery.cycle.cycle.cycle_id,
            "decision": recovery.cycle.effective_decision.value,
            "effect_status": recovery.cycle.effect_status,
            "post_effect_health": recovery.cycle.post_effect_health.value
            if recovery.cycle.post_effect_health is not None
            else None,
            "recovery_required_after": recovery.cycle.recovery_required,
            "proposal_receipt_hash": recovery.proposal_receipt_hash,
            "outcome_receipt_hash": recovery.outcome_receipt_hash,
        },
        "bounded_autonomous_recovery_pass": bounded_pass,
        "classification": (
            "LOCAL_CONTROLLED_BOUNDED_AUTONOMOUS_RECOVERY_PASS"
            if bounded_pass
            else "HOLD"
        ),
        "claim_boundary": {
            "general_autonomous_homeostasis": "HOLD",
            "world_real_homeostasis": "HOLD",
            "external_pass": "HOLD",
            "world_real_pass": "HOLD",
            "autopoiesis": "HOLD",
        },
        "closed_loop_state_root": recovery.cycle.closed_loop_state_root,
        "evidence_hash": evidence["evidence_hash"],
    }
    report["result_hash"] = stable_hash(report)

    with open("bounded-autonomous-recovery-v1.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True, indent=2)
        fh.write("\n")
    print(json.dumps(report, sort_keys=True, indent=2))
    journal.close()
    return 0 if bounded_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
