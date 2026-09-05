from __future__ import annotations

import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .core import Decision, stable_hash
from .deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from .evidence import evidence_receipt
from .organism_loop import GovernedOrganism
from .physiology import DurableEventJournal, ExecutionResult, PhysiologyEngine

SCHEMA_VERSION = "matverse.executor-substitution-experiment.v1"

ExecutorCallable = Callable[[Mapping[str, Any]], ExecutionResult]


@dataclass(frozen=True)
class OrganismCloneConfig:
    organism_id: str
    frozen_contract_hash: str
    runtime_id: str
    state_secret: str = field(repr=False)
    authority_secrets: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        if not self.organism_id or not self.runtime_id or not self.state_secret:
            raise ValueError("organism_id, runtime_id, and state_secret are required")
        if len(self.frozen_contract_hash) != 64:
            raise ValueError("frozen_contract_hash must be a 64-character SHA-256 hex digest")
        if not self.authority_secrets:
            raise ValueError("authority_secrets cannot be empty")

    def instantiate(self, *, state: Mapping[str, Any] | None = None) -> GovernedOrganism:
        return GovernedOrganism(
            organism_id=self.organism_id,
            frozen_contract_hash=self.frozen_contract_hash,
            runtime_id=self.runtime_id,
            state_secret=self.state_secret,
            authority_secrets=self.authority_secrets,
            state=state,
        )


@dataclass(frozen=True)
class ExecutorArm:
    arm_id: str
    provider: str
    model: str
    executor: ExecutorCallable = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        for name, value in (("arm_id", self.arm_id), ("provider", self.provider), ("model", self.model)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class ArmOutcome:
    arm_id: str
    provider: str
    model: str
    decision: str | None
    executed: bool
    success: bool
    health: str
    recovery_action: str
    cycle_id: str
    state_root_before: str
    state_root_after: str
    effect_status: str | None
    effect_hash: str | None
    usage: Mapping[str, Any]
    elapsed_ms: float
    journal_integrity: bool
    journal_event_types: tuple[str, ...]


@dataclass(frozen=True)
class SubstitutionReport:
    schema: str
    experiment_id: str
    snapshot_hash: str
    task_hash: str
    arms: tuple[ArmOutcome, ...]
    invariants: Mapping[str, bool]
    substitution_pass: bool
    receipt: Mapping[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "experiment_id": self.experiment_id,
            "snapshot_hash": self.snapshot_hash,
            "task_hash": self.task_hash,
            "arms": [asdict(item) for item in self.arms],
            "invariants": dict(self.invariants),
            "substitution_pass": self.substitution_pass,
            "receipt": dict(self.receipt),
        }


def capture_snapshot(config: OrganismCloneConfig, *, organism: GovernedOrganism | None = None) -> dict[str, Any]:
    source = organism or config.instantiate()
    if source.organism_id != config.organism_id:
        raise ValueError("snapshot source organism_id does not match clone config")
    return source.export_state()


def _constraints_fingerprint(organism: GovernedOrganism) -> str:
    return stable_hash(organism.state_payload().get("constraints", []))


def _lineage_fingerprint(organism: GovernedOrganism) -> str:
    return stable_hash(organism.state_payload().get("lineage", []))


def _effect_from_journal(journal: DurableEventJournal) -> Mapping[str, Any] | None:
    effects = [item for item in journal.read(limit=10_000, topic="physiology") if item.event_type == "EFFECT"]
    if not effects:
        return None
    return dict(effects[-1].payload)


def _usage_from_effect(effect: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not effect:
        return {}
    nested = effect.get("effect")
    if not isinstance(nested, Mapping):
        return {}
    usage = nested.get("usage")
    return dict(usage) if isinstance(usage, Mapping) else {}


def _arm_success(*, decision: Decision | None, executed: bool, effect: Mapping[str, Any] | None) -> bool:
    if decision is not Decision.PASS or not executed or not effect:
        return False
    if effect.get("status") != "OK":
        return False
    nested = effect.get("effect")
    if isinstance(nested, Mapping) and nested.get("validated") is False:
        return False
    return True


def run_executor_substitution(
    *,
    config: OrganismCloneConfig,
    snapshot: Mapping[str, Any],
    proposal: Mapping[str, Any],
    arms: Sequence[ExecutorArm],
    workdir: str | Path | None = None,
) -> SubstitutionReport:
    """Run one governed physiological task from the exact same authenticated snapshot.

    Executor identity is the independent variable. Every arm gets a fresh organism restored from
    the same snapshot, deterministic telemetry and an isolated durable journal. The harness never
    needs raw provider outputs: executor effects can be reduced to validation flags, hashes and
    usage metadata before they enter the physiological journal.
    """

    chosen = tuple(arms)
    if len(chosen) < 2:
        raise ValueError("executor substitution requires at least two arms")
    if len({item.arm_id for item in chosen}) != len(chosen):
        raise ValueError("arm_id values must be unique")
    if len({(item.provider, item.model) for item in chosen}) < 2:
        raise ValueError("executor substitution requires at least two distinct executor identities")

    snapshot_copy = dict(snapshot)
    probe = config.instantiate(state=snapshot_copy)
    snapshot_hash = stable_hash(snapshot_copy)
    task_copy = dict(proposal)
    task_hash = stable_hash(task_copy)
    baseline_identity = probe.organism_id
    baseline_constitution = probe.constitutional_contract_hash
    baseline_gate = probe.gate_fingerprint
    baseline_constraints = _constraints_fingerprint(probe)
    baseline_lineage = _lineage_fingerprint(probe)

    root = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="matverse-substitution-"))
    root.mkdir(parents=True, exist_ok=True)

    outcomes: list[ArmOutcome] = []
    final_organisms: list[GovernedOrganism] = []
    for arm in chosen:
        organism = config.instantiate(state=snapshot_copy)
        state_root_before = organism.state_root()
        journal = DurableEventJournal(root / f"{arm.arm_id}.sqlite3")
        telemetry = DeterministicTelemetry(plan=DeterministicFaultPlan(seed=1, cycles=1, directives=()))
        engine = PhysiologyEngine(
            organism=organism,
            journal=journal,
            telemetry=telemetry,
            executor=arm.executor,
        )
        t0 = time.perf_counter_ns()
        cycle = engine.tick(proposal=task_copy)
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        effect = _effect_from_journal(journal)
        event_types = tuple(item.event_type for item in journal.read(limit=10_000, topic="physiology"))
        outcome = ArmOutcome(
            arm_id=arm.arm_id,
            provider=arm.provider,
            model=arm.model,
            decision=None if cycle.decision is None else cycle.decision.value,
            executed=cycle.executed,
            success=_arm_success(decision=cycle.decision, executed=cycle.executed, effect=effect),
            health=cycle.health.value,
            recovery_action=cycle.recovery_action.value,
            cycle_id=cycle.cycle_id,
            state_root_before=state_root_before,
            state_root_after=organism.state_root(),
            effect_status=None if effect is None else str(effect.get("status")),
            effect_hash=None if effect is None else stable_hash(effect),
            usage=_usage_from_effect(effect),
            elapsed_ms=elapsed_ms,
            journal_integrity=journal.integrity_check(),
            journal_event_types=event_types,
        )
        outcomes.append(outcome)
        journal.close()
        final_organisms.append(organism)

    invariants = {
        "same_snapshot": all(item.state_root_before == outcomes[0].state_root_before for item in outcomes),
        "identity_preserved": all(item.organism_id == baseline_identity for item in final_organisms),
        "constitution_preserved": all(item.constitutional_contract_hash == baseline_constitution for item in final_organisms),
        "gate_fingerprint_preserved": all(item.gate_fingerprint == baseline_gate for item in final_organisms),
        "constraints_preserved": all(_constraints_fingerprint(item) == baseline_constraints for item in final_organisms),
        "lineage_equivalent_across_arms": len({_lineage_fingerprint(item) for item in final_organisms}) == 1,
        "state_root_equivalent_across_arms": len({item.state_root_after for item in outcomes}) == 1,
        "journal_integrity": all(item.journal_integrity for item in outcomes),
    }
    structural_pass = all(invariants.values())
    substitution_pass = structural_pass and all(item.success for item in outcomes)

    experiment_core = {
        "schema": SCHEMA_VERSION,
        "snapshot_hash": snapshot_hash,
        "task_hash": task_hash,
        "baseline_lineage_hash": baseline_lineage,
        "executor_identities": [
            {"arm_id": item.arm_id, "provider": item.provider, "model": item.model} for item in chosen
        ],
    }
    experiment_id = stable_hash(experiment_core)
    auditable_outputs = {
        "experiment_id": experiment_id,
        "arms": [
            {
                "arm_id": item.arm_id,
                "provider": item.provider,
                "model": item.model,
                "decision": item.decision,
                "executed": item.executed,
                "success": item.success,
                "state_root_after": item.state_root_after,
                "effect_hash": item.effect_hash,
                "usage": dict(item.usage),
                "journal_integrity": item.journal_integrity,
            }
            for item in outcomes
        ],
        "invariants": invariants,
        "substitution_pass": substitution_pass,
    }
    receipt = evidence_receipt("EXECUTOR_SUBSTITUTION_EXPERIMENT", experiment_core, auditable_outputs)
    return SubstitutionReport(
        schema=SCHEMA_VERSION,
        experiment_id=experiment_id,
        snapshot_hash=snapshot_hash,
        task_hash=task_hash,
        arms=tuple(outcomes),
        invariants=invariants,
        substitution_pass=substitution_pass,
        receipt=receipt,
    )
