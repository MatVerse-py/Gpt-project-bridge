from __future__ import annotations

import json

from app.core import Decision, stable_hash
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.homeostatic_supervisor import (
    BoundedHomeostaticSupervisor,
    BoundedRecoveryPolicy,
)
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult, HealthState
from app.physiology_effect_feedback import ClosedLoopPhysiologyEngine

WORKLOADS = 300
FAIL_EVERY = 37


def main() -> int:
    journal = DurableEventJournal("longitudinal-liveness-v1.sqlite3")
    organism = GovernedOrganism(
        organism_id="longitudinal-liveness-organism",
        frozen_contract_hash="e" * 64,
        runtime_id="longitudinal-liveness-runtime",
        state_secret="longitudinal-liveness-state",
        authority_secrets={"independent-authorizer": "longitudinal-liveness-authority"},
    )
    telemetry = DeterministicTelemetry(
        plan=DeterministicFaultPlan(seed=23, cycles=(WORKLOADS * 5), directives=())
    )

    executor_calls = 0
    injected_failures = 0

    def executor(proposal):
        nonlocal executor_calls, injected_failures
        executor_calls += 1
        if executor_calls % FAIL_EVERY == 0:
            injected_failures += 1
            return ExecutionResult(
                status="FAIL",
                effect={"injected_failure": injected_failures},
            )
        return ExecutionResult(
            status="OK",
            effect={"executor_call": executor_calls, "resource": proposal.get("resource")},
        )

    engine = ClosedLoopPhysiologyEngine(
        organism=organism,
        journal=journal,
        telemetry=telemetry,
        executor=executor,
        feedback_state_secret="longitudinal-liveness-feedback",
    )
    supervisor = BoundedHomeostaticSupervisor(
        engine=engine,
        recovery_policy=BoundedRecoveryPolicy.build(
            action="READ",
            parameters={"resource": "sensor"},
        ),
    )

    recovery_attempts = 0
    cleared_recoveries = 0
    workload_passes = 0
    unexpected_decisions: list[str] = []

    for i in range(WORKLOADS):
        if engine.feedback.recovery_required:
            raise RuntimeError("recovery gate leaked into next workload")

        result = supervisor.step(
            proposal={"action": "READ", "resource": "sensor", "workload_seq": i + 1}
        )
        if result.cycle.effective_decision is not Decision.PASS:
            unexpected_decisions.append(
                "NONE"
                if result.cycle.effective_decision is None
                else result.cycle.effective_decision.value
            )
        else:
            workload_passes += 1

        if result.cycle.recovery_required:
            recovery_attempts += 1
            recovered = supervisor.step()
            if (
                recovered.autonomous_recovery_attempted
                and recovered.cycle.effective_decision is Decision.PASS
                and recovered.cycle.effect_status == "OK"
                and recovered.cycle.post_effect_health is HealthState.NORMAL
                and not recovered.cycle.recovery_required
            ):
                cleared_recoveries += 1
            else:
                raise RuntimeError("bounded autonomous recovery failed during longitudinal run")

    final_feedback = engine.feedback
    journal_ok = journal.integrity_check()
    events = journal.read(limit=10_000)
    proposal_events = sum(
        1 for event in events
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_PROPOSED"
    )
    outcome_events = sum(
        1 for event in events
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_OUTCOME"
    )
    memory_commits = sum(1 for event in events if event.event_type == "MEMORY_COMMIT")

    longitudinal_pass = bool(
        workload_passes == WORKLOADS
        and not unexpected_decisions
        and injected_failures > 0
        and recovery_attempts == injected_failures
        and cleared_recoveries == injected_failures
        and proposal_events == injected_failures
        and outcome_events == injected_failures
        and not final_feedback.recovery_required
        and journal_ok
        and memory_commits >= WORKLOADS + injected_failures
    )

    report = {
        "schema": "matverse.longitudinal-liveness-experiment.v1",
        "scope": "LOCAL_CONTROLLED_DETERMINISTIC_LAB",
        "evidence_class": "SIMULATED_INSTRUMENTED",
        "workloads": WORKLOADS,
        "executor_calls": executor_calls,
        "injected_failures": injected_failures,
        "recovery_attempts": recovery_attempts,
        "cleared_recoveries": cleared_recoveries,
        "workload_passes": workload_passes,
        "unexpected_decisions": unexpected_decisions,
        "recovery_proposal_events": proposal_events,
        "recovery_outcome_events": outcome_events,
        "memory_commits": memory_commits,
        "journal_integrity": journal_ok,
        "final_recovery_required": final_feedback.recovery_required,
        "final_organism_state_root": organism.state_root(),
        "longitudinal_liveness_pass": longitudinal_pass,
        "classification": (
            "LOCAL_CONTROLLED_LONGITUDINAL_LIVENESS_PASS"
            if longitudinal_pass
            else "HOLD"
        ),
        "claim_boundary": {
            "unattended_duration_real_time": "NOT_MEASURED",
            "native_world_telemetry": "HOLD",
            "general_autonomous_homeostasis": "HOLD",
            "external_pass": "HOLD",
            "world_real_pass": "HOLD",
            "scientific_ocg": "HOLD",
        },
    }
    report["result_hash"] = stable_hash(report)

    with open("longitudinal-liveness-v1.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True, indent=2)
        fh.write("\n")
    print(json.dumps(report, sort_keys=True, indent=2))
    journal.close()
    return 0 if longitudinal_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
