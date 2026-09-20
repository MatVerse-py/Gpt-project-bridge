from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.core import Decision, stable_hash
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult
from app.physiology_effect_feedback import ClosedLoopPhysiologyEngine

SCHEMA = "matverse.long-run-autonomous-liveness.v1"
FEEDBACK_SECRET = "matverse-long-run-feedback-v1"
STATE_SECRET = "matverse-long-run-state-v1"
ORGANISM_STATE_KEY = "long-run:organism-state"


@dataclass
class PlannedExecutor:
    fail_every: int
    invocations: int = 0
    injected_failures: int = 0
    successful_recoveries: int = 0

    def __call__(self, proposal: Mapping[str, Any]) -> ExecutionResult:
        self.invocations += 1
        is_recovery = bool(proposal.get("recovery_for"))
        if is_recovery:
            self.successful_recoveries += 1
            return ExecutionResult(
                status="OK",
                effect={"kind": "RECOVERY", "invocation": self.invocations},
            )
        if self.fail_every > 0 and self.invocations % self.fail_every == 0:
            self.injected_failures += 1
            return ExecutionResult(
                status="FAIL",
                effect={"kind": "INJECTED_FAILURE", "invocation": self.invocations},
            )
        return ExecutionResult(
            status="OK",
            effect={"kind": "NORMAL", "invocation": self.invocations},
        )


def _organism(state: Mapping[str, Any] | None = None) -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="long-run-autonomous-liveness-v1",
        frozen_contract_hash="b" * 64,
        runtime_id="long-run-runtime-v1",
        state_secret=STATE_SECRET,
        authority_secrets={"independent-authorizer": "long-run-authority-v1"},
        state=state,
    )


def _engine(
    *,
    journal: DurableEventJournal,
    executor: PlannedExecutor,
    cycles: int,
    organism_state: Mapping[str, Any] | None = None,
) -> ClosedLoopPhysiologyEngine:
    telemetry = DeterministicTelemetry(
        plan=DeterministicFaultPlan(
            seed=20260920,
            cycles=max(cycles * 3, 1),
            directives=(),
        )
    )
    return ClosedLoopPhysiologyEngine(
        organism=_organism(organism_state),
        journal=journal,
        telemetry=telemetry,
        executor=executor,
        feedback_state_secret=FEEDBACK_SECRET,
    )


def _persist_organism_state(
    journal: DurableEventJournal,
    engine: ClosedLoopPhysiologyEngine,
) -> str:
    state = engine.organism.export_state()
    journal.set_state(ORGANISM_STATE_KEY, state)
    return str(state["state_root"])


def _read_all_events(journal: DurableEventJournal) -> list[Any]:
    events: list[Any] = []
    after_seq = 0
    while True:
        batch = journal.read(after_seq=after_seq, limit=10_000)
        if not batch:
            return events
        events.extend(batch)
        after_seq = batch[-1].seq


def run_long_run(
    *,
    cycles: int,
    fail_every: int,
    restart_every: int,
    db_path: Path,
) -> dict[str, Any]:
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    if fail_every < 0:
        raise ValueError("fail_every must be >= 0")
    if restart_every < 0:
        raise ValueError("restart_every must be >= 0")

    executor = PlannedExecutor(fail_every=fail_every)
    journal = DurableEventJournal(db_path)
    initial_organism_state = journal.get_state(ORGANISM_STATE_KEY, None)
    if initial_organism_state is not None and not isinstance(initial_organism_state, dict):
        raise RuntimeError("persisted organism state has invalid type")
    initial_cycle_seq = int(
        journal.get_state(
            "physiology:long-run-autonomous-liveness-v1",
            {"cycle_seq": 0},
        )["cycle_seq"]
    )
    initial_event_count = len(_read_all_events(journal))
    engine = _engine(
        journal=journal,
        executor=executor,
        cycles=cycles,
        organism_state=initial_organism_state,
    )
    if initial_organism_state is None:
        _persist_organism_state(journal, engine)

    restarts = 0
    completed = 0
    pass_decisions = 0
    hold_decisions = 0
    block_decisions = 0
    undecided_decisions = 0
    recovery_cycles = 0
    closed_loop_state_roots: list[str] = []
    organism_state_roots: list[str] = [engine.organism.state_root()]
    restart_continuity_checks: list[bool] = []

    try:
        while completed < cycles:
            if restart_every and completed and completed % restart_every == 0:
                expected_root = engine.organism.state_root()
                journal.close()
                journal = DurableEventJournal(db_path)
                persisted_state = journal.get_state(ORGANISM_STATE_KEY, None)
                if not isinstance(persisted_state, dict):
                    raise RuntimeError("persisted organism state missing at restart")
                engine = _engine(
                    journal=journal,
                    executor=executor,
                    cycles=cycles,
                    organism_state=persisted_state,
                )
                restored_root = engine.organism.state_root()
                restart_continuity_checks.append(restored_root == expected_root)
                restarts += 1

            proposal: dict[str, Any] = {
                "action": "READ",
                "resource": "long-run-sensor",
                "logical_cycle": completed + 1,
            }
            if engine.feedback.recovery_required:
                proposal["recovery_for"] = engine.feedback.recovery_for_cycle_id
                recovery_cycles += 1

            result = engine.tick(proposal=proposal)
            completed += 1
            closed_loop_state_roots.append(result.closed_loop_state_root)
            organism_state_roots.append(_persist_organism_state(journal, engine))

            if result.effective_decision is Decision.PASS:
                pass_decisions += 1
            elif result.effective_decision is Decision.HOLD:
                hold_decisions += 1
            elif result.effective_decision is Decision.BLOCK:
                block_decisions += 1
            else:
                undecided_decisions += 1

        drain_cycles = 0
        if engine.feedback.recovery_required:
            drain_cycles = 1
            result = engine.tick(
                proposal={
                    "action": "READ",
                    "resource": "long-run-sensor",
                    "logical_cycle": cycles + 1,
                    "recovery_for": engine.feedback.recovery_for_cycle_id,
                }
            )
            recovery_cycles += 1
            closed_loop_state_roots.append(result.closed_loop_state_root)
            organism_state_roots.append(_persist_organism_state(journal, engine))
            if result.effective_decision is Decision.PASS:
                pass_decisions += 1
            elif result.effective_decision is Decision.HOLD:
                hold_decisions += 1
            elif result.effective_decision is Decision.BLOCK:
                block_decisions += 1
            else:
                undecided_decisions += 1

        events = _read_all_events(journal)
        new_event_count = len(events) - initial_event_count
        final_feedback = engine.feedback
        final_cycle_seq = int(
            journal.get_state(
                f"physiology:{engine.organism.organism_id}",
                {"cycle_seq": -1},
            )["cycle_seq"]
        )
        persisted_final_organism = journal.get_state(ORGANISM_STATE_KEY, None)
        final_organism_state_matches = (
            isinstance(persisted_final_organism, dict)
            and persisted_final_organism.get("state_root")
            == engine.organism.state_root()
        )
        executed_cycles = cycles + drain_cycles

        hard_checks = {
            "requested_cycles_completed": completed == cycles,
            "journal_integrity": journal.integrity_check(),
            "final_recovery_clear": final_feedback.recovery_required is False,
            "fault_path_exercised":
                fail_every == 0
                or cycles < fail_every
                or executor.injected_failures > 0,
            "all_injected_failures_recovered":
                executor.successful_recoveries == executor.injected_failures,
            "restarts_exercised": restart_every == 0 or restarts > 0,
            "restart_organism_continuity":
                restart_every == 0
                or (
                    len(restart_continuity_checks) == restarts
                    and all(restart_continuity_checks)
                ),
            "final_organism_state_persisted": final_organism_state_matches,
            "cycle_sequence_increment":
                final_cycle_seq - initial_cycle_seq == executed_cycles,
            "closed_loop_state_roots_present":
                len(closed_loop_state_roots) == executed_cycles
                and all(
                    isinstance(root, str) and len(root) == 64
                    for root in closed_loop_state_roots
                ),
            "organism_state_roots_present":
                len(organism_state_roots) == executed_cycles + 1
                and all(
                    isinstance(root, str) and len(root) == 64
                    for root in organism_state_roots
                ),
            "organism_state_evolves":
                len(set(organism_state_roots)) == len(organism_state_roots),
            "every_cycle_pass":
                pass_decisions == executed_cycles
                and hold_decisions == 0
                and block_decisions == 0
                and undecided_decisions == 0,
            "events_persisted": new_event_count > executed_cycles,
        }
        passed = all(hard_checks.values())
        report: dict[str, Any] = {
            "schema": SCHEMA,
            "evidence_class": "LOCAL_DETERMINISTIC_LONG_RUN",
            "requested_cycles": cycles,
            "drain_cycles": drain_cycles,
            "fail_every": fail_every,
            "restart_every": restart_every,
            "completed_cycles": completed,
            "initial_cycle_seq": initial_cycle_seq,
            "final_cycle_seq": final_cycle_seq,
            "restarts": restarts,
            "restart_continuity_checks": restart_continuity_checks,
            "executor_invocations": executor.invocations,
            "injected_failures": executor.injected_failures,
            "successful_recoveries": executor.successful_recoveries,
            "recovery_cycles": recovery_cycles,
            "pass_decisions": pass_decisions,
            "hold_decisions": hold_decisions,
            "block_decisions": block_decisions,
            "undecided_decisions": undecided_decisions,
            "initial_event_count": initial_event_count,
            "new_event_count": new_event_count,
            "event_count": len(events),
            "initial_organism_state_root": organism_state_roots[0],
            "final_organism_state_root": engine.organism.state_root(),
            "final_closed_loop_state_root": closed_loop_state_roots[-1],
            "hard_checks": hard_checks,
            "classification": (
                "LOCAL_LONG_RUN_AUTONOMOUS_LIVENESS_PASS" if passed else "HOLD"
            ),
            "claim_boundary": {
                "process_restart_reproduction": "NOT_TESTED",
                "external_reproduction": "HOLD",
                "world_real_homeostasis": "HOLD",
                "world_real_pass": "HOLD",
                "scientific_pass": "NOT_CLAIMED",
                "telemetry": "SIMULATED_DETERMINISTIC",
            },
        }
        report["result_hash"] = stable_hash(report)
        return report
    finally:
        journal.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument("--fail-every", type=int, default=37)
    parser.add_argument("--restart-every", type=int, default=100)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("long-run-autonomous-liveness-v1.json"),
    )
    args = parser.parse_args()

    if args.db is None:
        with tempfile.TemporaryDirectory(prefix="matverse-long-run-") as tmp:
            report = run_long_run(
                cycles=args.cycles,
                fail_every=args.fail_every,
                restart_every=args.restart_every,
                db_path=Path(tmp) / "physiology.sqlite3",
            )
    else:
        report = run_long_run(
            cycles=args.cycles,
            fail_every=args.fail_every,
            restart_every=args.restart_every,
            db_path=args.db,
        )

    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return (
        0
        if report["classification"] == "LOCAL_LONG_RUN_AUTONOMOUS_LIVENESS_PASS"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
