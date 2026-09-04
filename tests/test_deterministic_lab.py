from __future__ import annotations

import pytest

from app.deterministic_lab import (
    DeterministicFaultPlan,
    FaultDirective,
    FaultInjectingExecutor,
    FaultKind,
    plan_fingerprint,
)
from app.physiology import ExecutionResult


def test_same_seed_produces_exact_same_fault_plan_and_fingerprint():
    first = DeterministicFaultPlan.generate(seed=20260904, cycles=500, fault_probability=0.2)
    second = DeterministicFaultPlan.generate(seed=20260904, cycles=500, fault_probability=0.2)
    assert first == second
    assert first.as_dict() == second.as_dict()
    assert plan_fingerprint(first) == plan_fingerprint(second)


def test_different_seed_changes_plan_for_nontrivial_run():
    first = DeterministicFaultPlan.generate(seed=1, cycles=500, fault_probability=0.2)
    second = DeterministicFaultPlan.generate(seed=2, cycles=500, fault_probability=0.2)
    assert first.directives != second.directives


def test_fault_injecting_executor_is_replayable():
    plan = DeterministicFaultPlan(
        seed=3,
        cycles=3,
        directives=(FaultDirective(2, FaultKind.EXECUTOR_ERROR),),
    )

    def delegate(proposal):
        return ExecutionResult(status="OK", effect={"value": proposal["value"]})

    wrapped = FaultInjectingExecutor(delegate, plan)
    assert wrapped({"value": 1}).status == "OK"
    with pytest.raises(RuntimeError, match="invocation 2"):
        wrapped({"value": 2})
    assert wrapped({"value": 3}).effect == {"value": 3}
