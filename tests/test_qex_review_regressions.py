from __future__ import annotations

from app.qex_substrate import (
    CapabilityProfile,
    ComparisonStatus,
    ComputeRegime,
    ExperimentContract,
    assess_candidate,
    compare_substrate_results,
    select_substrate,
)


def contract() -> ExperimentContract:
    return ExperimentContract(
        experiment_id="QEX-REVIEW-REGRESSION",
        problem_hash="a" * 64,
        objective="review regression tests",
        required_capabilities=("bit_not",),
        metric_schema_hash="b" * 64,
        observable_schema_hash="c" * 64,
        evidence_policy_hash="d" * 64,
    )


def test_unknown_telemetry_is_not_scored_as_zero():
    c = contract()
    unknown = CapabilityProfile(
        backend_id="cpu-unknown",
        regime=ComputeRegime.CLASSICAL,
        capabilities=("bit_not",),
    )
    known = CapabilityProfile(
        backend_id="hybrid-known",
        regime=ComputeRegime.HYBRID,
        capabilities=("bit_not",),
        estimated_cost=2.0,
        estimated_latency_ms=10.0,
        error_rate=0.01,
    )
    assert assess_candidate(c, unknown).preference_score is None
    assert assess_candidate(c, known).preference_score is not None
    selected = select_substrate(c, (unknown, known))
    assert selected.selected_backend_id == "hybrid-known"


def test_gate_failure_receipt_binds_profiles_and_assessments():
    c = contract()
    first = CapabilityProfile("cpu-a", ComputeRegime.CLASSICAL, capabilities=("bit_not",))
    second = CapabilityProfile("cpu-b", ComputeRegime.CLASSICAL, capabilities=("bit_not",))
    r1 = select_substrate(c, (first,), signature_valid=False).receipt
    r2 = select_substrate(c, (second,), signature_valid=False).receipt
    assert r1["input_hash"] != r2["input_hash"]
    assert r1["output_hash"] != r2["output_hash"]


def test_classical_baseline_hold_receipt_binds_candidate_assessments():
    c = contract()
    profile = CapabilityProfile("hybrid-a", ComputeRegime.HYBRID, capabilities=("bit_not",))
    result = select_substrate(c, (profile,))
    other = CapabilityProfile("hybrid-b", ComputeRegime.HYBRID, capabilities=("bit_not",))
    other_result = select_substrate(c, (other,))
    assert result.selected_backend_id is None
    assert result.receipt["input_hash"] != other_result.receipt["input_hash"]
    assert result.receipt["output_hash"] != other_result.receipt["output_hash"]


def test_comparator_rejects_nan_infinity_and_overflow():
    hard = ("experiment_id",)
    right = {"experiment_id": "same", "p": 0.5}
    for bad in (float("nan"), float("inf"), 10**10000):
        left = {"experiment_id": "same", "p": bad}
        assert compare_substrate_results(
            left, right, hard_invariants=hard, numeric_tolerances={"p": 1e-9}
        ) is ComparisonStatus.INCOMPARABLE


def test_comparator_rejects_nonfinite_tolerance():
    left = {"experiment_id": "same", "p": 0.5}
    right = {"experiment_id": "same", "p": 0.5}
    assert compare_substrate_results(
        left, right, hard_invariants=("experiment_id",), numeric_tolerances={"p": float("nan")}
    ) is ComparisonStatus.INCOMPARABLE
