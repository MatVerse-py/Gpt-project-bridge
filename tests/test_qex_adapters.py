from app.core import stable_hash
from app.qex_adapters import ClassicalNotAdapter, IdealStatevectorNotAdapter
from app.qex_substrate import ComparisonStatus, ExperimentContract, compare_substrate_results


def contract() -> ExperimentContract:
    return ExperimentContract(
        experiment_id="QEX-SUBSTRATE-01",
        problem_hash=stable_hash({"problem": "NOT", "domain": "bit"}),
        objective="preserve experiment identity while changing computational substrate",
        required_capabilities=("bit_not",),
        metric_schema_hash=stable_hash({"metrics": ["probability_0", "probability_1"]}),
        observable_schema_hash=stable_hash({"observables": ["result"]}),
        evidence_policy_hash=stable_hash({"policy": "evidence-v1"}),
        require_classical_baseline=True,
    )


def test_cpu_and_ideal_statevector_preserve_hard_invariants_for_zero():
    c = contract()
    cpu = ClassicalNotAdapter().execute(c, {"bit": 0})
    qsim = IdealStatevectorNotAdapter().execute(c, {"bit": 0})

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
        cpu.canonical_observable(),
        qsim.canonical_observable(),
        hard_invariants=hard,
        numeric_tolerances={"probability_0": 0.0, "probability_1": 0.0},
    )
    assert status is ComparisonStatus.EXACT
    assert cpu.result == qsim.result == 1
    assert cpu.receipt["receipt_hash"] != qsim.receipt["receipt_hash"]


def test_cpu_and_ideal_statevector_preserve_hard_invariants_for_one():
    c = contract()
    cpu = ClassicalNotAdapter().execute(c, {"bit": 1})
    qsim = IdealStatevectorNotAdapter().execute(c, {"bit": 1})
    assert cpu.canonical_observable() == qsim.canonical_observable()
    assert cpu.result == qsim.result == 0


def test_execution_rejects_noncanonical_payload():
    c = contract()
    for adapter in (ClassicalNotAdapter(), IdealStatevectorNotAdapter()):
        try:
            adapter.execute(c, {"bit": True})
        except ValueError as exc:
            assert "integer 0 or 1" in str(exc)
        else:
            raise AssertionError("adapter accepted bool as a bit")
