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
    unknown = CapabilityProfile("cpu-unknown", ComputeRegime.CLASSICAL, capabilities=("bit_not",))
    known = CapabilityProfile(
        "hybrid-known",
        ComputeRegime.HYBRID,
        capabilities=("bit_not",),
        estimated_cost=2.0,
        estimated_latency_ms=10.0,
        error_rate=0.01,
    )
    assert assess_candidate(c, unknown).preference_score is None
    assert assess_candidate(c, known).preference_score is not None
    selected = select_substrate(
        c, (unknown, known), ontology_ok=True, signature_valid=True, transition_valid=True
    )
    assert selected.selected_backend_id == "hybrid-known"


def test_fail_closed_receipt_binds_assessment_content_independently_of_profile_hash():
    c = contract()
    profile = CapabilityProfile("cpu-a", ComputeRegime.CLASSICAL, capabilities=("bit_not",))
    ontology_block = select_substrate(
        c, (profile,), ontology_ok=False, signature_valid=True, transition_valid=True
    ).receipt
    signature_block = select_substrate(
        c, (profile,), ontology_ok=True, signature_valid=False, transition_valid=True
    ).receipt
    assert ontology_block["input_hash"] == signature_block["input_hash"]
    assert ontology_block["output_hash"] != signature_block["output_hash"]


def test_classical_baseline_hold_receipt_is_present():
    c = contract()
    profile = CapabilityProfile("hybrid-a", ComputeRegime.HYBRID, capabilities=("bit_not",))
    result = select_substrate(
        c, (profile,), ontology_ok=True, signature_valid=True, transition_valid=True
    )
    assert result.selected_backend_id is None
    assert isinstance(result.receipt.get("receipt_hash"), str)
    assert len(result.receipt["receipt_hash"]) == 64


def test_comparator_rejects_nan_infinity_and_overflow():
    right = {"experiment_id": "same", "p": 0.5}
    for bad in (float("nan"), float("inf"), 10**10000):
        left = {"experiment_id": "same", "p": bad}
        assert compare_substrate_results(
            left, right, hard_invariants=("experiment_id",), numeric_tolerances={"p": 1e-9}
        ) is ComparisonStatus.INCOMPARABLE


def test_comparator_rejects_nonfinite_tolerance():
    left = {"experiment_id": "same", "p": 0.5}
    right = {"experiment_id": "same", "p": 0.5}
    assert compare_substrate_results(
        left,
        right,
        hard_invariants=("experiment_id",),
        numeric_tolerances={"p": float("nan")},
    ) is ComparisonStatus.INCOMPARABLE
