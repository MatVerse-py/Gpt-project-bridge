from app.qex_adapters import IdealStatevectorNotAdapter
from app.qex_experiment import qex_substrate_01_contract
from app.qex_noisy_adapter import ControlledBitFlipNoiseNotAdapter, total_variation_distance
from app.qex_substrate import ComparisonStatus, compare_substrate_results


def test_noise_preserves_frozen_contract_but_changes_distribution():
    c = qex_substrate_01_contract()
    ideal = IdealStatevectorNotAdapter().execute(c, {"bit": 0})
    noisy = ControlledBitFlipNoiseNotAdapter(0.05).execute(c, {"bit": 0})

    hard = (
        "experiment_id",
        "contract_hash",
        "problem_hash",
        "metric_schema_hash",
        "observable_schema_hash",
        "evidence_policy_hash",
        "result",
    )
    status = compare_substrate_results(
        ideal.canonical_observable(),
        noisy.canonical_observable(),
        hard_invariants=hard,
        numeric_tolerances={"probability_0": 0.05, "probability_1": 0.05},
    )
    assert status is ComparisonStatus.WITHIN_TOLERANCE
    assert ideal.contract_hash == noisy.contract_hash == c.contract_hash
    assert abs(total_variation_distance(ideal.canonical_observable(), noisy.canonical_observable()) - 0.05) < 1e-12


def test_noise_exceeding_tolerance_is_divergent():
    c = qex_substrate_01_contract()
    ideal = IdealStatevectorNotAdapter().execute(c, {"bit": 1})
    noisy = ControlledBitFlipNoiseNotAdapter(0.10).execute(c, {"bit": 1})

    status = compare_substrate_results(
        ideal.canonical_observable(),
        noisy.canonical_observable(),
        hard_invariants=("experiment_id", "contract_hash", "problem_hash", "result"),
        numeric_tolerances={"probability_0": 0.05, "probability_1": 0.05},
    )
    assert status is ComparisonStatus.DIVERGENT
    assert abs(total_variation_distance(ideal.canonical_observable(), noisy.canonical_observable()) - 0.10) < 1e-12


def test_noise_configuration_fails_closed_outside_supported_range():
    for invalid in (-0.01, 0.51):
        try:
            ControlledBitFlipNoiseNotAdapter(invalid)
        except ValueError as exc:
            assert "[0,0.5]" in str(exc)
        else:
            raise AssertionError("invalid noise configuration accepted")
