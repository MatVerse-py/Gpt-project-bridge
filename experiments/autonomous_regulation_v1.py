from __future__ import annotations

import json

from app.core import stable_hash
from app.deterministic_lab import (
    DeterministicFaultPlan,
    DeterministicTelemetry,
    FaultDirective,
    FaultKind,
)
from app.organism_loop import GovernedOrganism
from app.physiology import (
    DurableEventJournal,
    HomeostaticPolicy,
    PhysiologyEngine,
    RecoveryAction,
)

CYCLES = 6


def main() -> int:
    journal = DurableEventJournal("autonomous-regulation-v1.sqlite3")
    organism = GovernedOrganism(
        organism_id="autonomous-regulation-organism",
        frozen_contract_hash="f" * 64,
        runtime_id="autonomous-regulation-runtime",
        state_secret="autonomous-regulation-state",
        authority_secrets={"independent-authorizer": "autonomous-regulation-authority"},
    )
    plan = DeterministicFaultPlan(
        seed=31,
        cycles=CYCLES,
        directives=(FaultDirective(cycle_seq=2, kind=FaultKind.CRITICAL_DISK),),
    )
    telemetry = DeterministicTelemetry(plan=plan)
    engine = PhysiologyEngine(
        organism=organism,
        journal=journal,
        telemetry=telemetry,
        policy=HomeostaticPolicy(normal_streak_to_exit_safe_mode=3),
        executor=None,
    )

    cycles = [engine.tick(proposal=None) for _ in range(CYCLES)]
    actions = [cycle.recovery_action for cycle in cycles]
    health = [cycle.health.value for cycle in cycles]

    expected_actions = [
        RecoveryAction.NONE,
        RecoveryAction.ENTER_SAFE_MODE,
        RecoveryAction.NONE,
        RecoveryAction.NONE,
        RecoveryAction.EXIT_SAFE_MODE,
        RecoveryAction.NONE,
    ]
    events = journal.read(limit=500)
    plans = [
        event.payload
        for event in events
        if event.event_type == "RECOVERY_PLAN"
    ]

    autonomous_regulation_pass = bool(
        actions == expected_actions
        and health[0] == "NORMAL"
        and health[1] == "CRITICAL"
        and all(item == "NORMAL" for item in health[2:])
        and engine.safe_mode is False
        and engine.throttled is False
        and len(plans) == CYCLES
        and plans[1]["action"] == RecoveryAction.ENTER_SAFE_MODE.value
        and plans[4]["action"] == RecoveryAction.EXIT_SAFE_MODE.value
        and journal.integrity_check()
    )

    report = {
        "schema": "matverse.autonomous-regulation-experiment.v1",
        "scope": "LOCAL_CONTROLLED_DETERMINISTIC_LAB",
        "evidence_class": "SIMULATED_INSTRUMENTED",
        "cycles": CYCLES,
        "fault_plan": plan.as_dict(),
        "health_sequence": health,
        "recovery_action_sequence": [item.value for item in actions],
        "human_runtime_intervention": False,
        "external_action_proposals": 0,
        "safe_mode_final": engine.safe_mode,
        "throttled_final": engine.throttled,
        "journal_integrity": journal.integrity_check(),
        "autonomous_operational_regulation_pass": autonomous_regulation_pass,
        "classification": (
            "LOCAL_CONTROLLED_AUTONOMOUS_REGULATION_PASS"
            if autonomous_regulation_pass
            else "HOLD"
        ),
        "claim_boundary": {
            "environment_self_repair": "NOT_DEMONSTRATED",
            "general_autonomous_homeostasis": "HOLD",
            "native_world_telemetry": "HOLD",
            "external_pass": "HOLD",
            "world_real_pass": "HOLD",
            "autopoiesis": "HOLD",
        },
        "final_state_root": organism.state_root(),
    }
    report["result_hash"] = stable_hash(report)

    with open("autonomous-regulation-v1.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True, indent=2)
        fh.write("\n")
    print(json.dumps(report, sort_keys=True, indent=2))
    journal.close()
    return 0 if autonomous_regulation_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
