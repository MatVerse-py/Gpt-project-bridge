from app.qex_adapters import ClassicalNotAdapter, IdealStatevectorNotAdapter
from app.qex_experiment import qex_substrate_01_contract
from app.qex_qiskit_adapter import QiskitStatevectorNotAdapter
from app.qex_substrate import ComparisonStatus, compare_substrate_results


HARD = (
    "experiment_id",
    "contract_hash",
    "problem_hash",
    "metric_schema_hash",
    "observable_schema_hash",
    "evidence_policy_hash",
    "result",
)


def test_cpu_internal_statevector_and_qiskit_preserve_contract_for_both_bits():
    c = qex_substrate_01_contract()
    adapters = (ClassicalNotAdapter(), IdealStatevectorNotAdapter(), QiskitStatevectorNotAdapter())

    for bit in (0, 1):
        results = [adapter.execute(c, {"bit": bit}) for adapter in adapters]
        reference = results[0].canonical_observable()
        for candidate in results[1:]:
            status = compare_substrate_results(
                reference,
                candidate.canonical_observable(),
                hard_invariants=HARD,
                numeric_tolerances={"probability_0": 0.0, "probability_1": 0.0},
            )
            assert status is ComparisonStatus.EXACT
        assert {r.result for r in results} == {1 - bit}
        assert len({r.receipt["receipt_hash"] for r in results}) == 3


def test_qiskit_adapter_is_explicitly_simulated_quantum_regime():
    result = QiskitStatevectorNotAdapter().execute(qex_substrate_01_contract(), {"bit": 0})
    assert result.backend_id.startswith("qiskit-2.5-statevector")
    assert result.regime.value == "QUANTUM_GATE"
