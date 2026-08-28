from app.core import Decision, stable_hash
from app.qex_substrate import (
    CapabilityProfile,
    ComparisonStatus,
    ComputeRegime,
    ExperimentContract,
    QuantumModality,
    compare_substrate_results,
    select_substrate,
)


def contract(**overrides):
    values = dict(
        experiment_id="QEX-SUBSTRATE-01",
        problem_hash=stable_hash({"problem": "demo"}),
        objective="compare computational substrates",
        required_capabilities=("optimization",),
        metric_schema_hash=stable_hash({"metrics": ["quality", "runtime"]}),
        observable_schema_hash=stable_hash({"observables": ["result"]}),
        evidence_policy_hash=stable_hash({"policy": "evidence-v1"}),
        budget_max=10.0,
        latency_max_ms=5000.0,
        require_classical_baseline=True,
    )
    values.update(overrides)
    return ExperimentContract(**values)


def test_best_justified_infrastructure_can_be_classical():
    profiles = [
        CapabilityProfile("cpu", ComputeRegime.CLASSICAL, capabilities=("optimization",), estimated_cost=1, estimated_latency_ms=100),
        CapabilityProfile("qpu", ComputeRegime.QUANTUM_GATE, QuantumModality.SUPERCONDUCTING, ("optimization",), "HIGH", True, 8, 4000, 0.02),
    ]
    result = select_substrate(contract(), profiles)
    assert result.selected_backend_id == "cpu"
    assert result.receipt["schema"] == "matverse.evidence-receipt.v1"


def test_quantum_can_win_after_admissibility_when_profile_justifies_it():
    profiles = [
        CapabilityProfile("cpu", ComputeRegime.CLASSICAL, capabilities=("optimization",), estimated_cost=5, estimated_latency_ms=4000),
        CapabilityProfile("ion-qpu", ComputeRegime.QUANTUM_GATE, QuantumModality.TRAPPED_ION, ("optimization",), "HIGH", True, 1, 100, 0.0001),
    ]
    result = select_substrate(contract(), profiles)
    assert result.selected_backend_id == "ion-qpu"


def test_missing_classical_baseline_holds_selection():
    profiles = [CapabilityProfile("qpu", ComputeRegime.QUANTUM_GATE, QuantumModality.SUPERCONDUCTING, ("optimization",), "HIGH", True, 1, 100)]
    result = select_substrate(contract(), profiles)
    assert result.selected_backend_id is None


def test_topological_claim_is_fail_closed_while_contested():
    profiles = [
        CapabilityProfile("cpu", ComputeRegime.CLASSICAL, capabilities=("optimization",), estimated_cost=5, estimated_latency_ms=100),
        CapabilityProfile("topo", ComputeRegime.QUANTUM_GATE, QuantumModality.TOPOLOGICAL, ("optimization",), "EXPERIMENTAL", True, 1, 100),
    ]
    result = select_substrate(contract(), profiles)
    topo = next(a for a in result.assessments if a.backend_id == "topo")
    assert topo.decision is Decision.BLOCK


def test_omega_gate_precedes_preference_routing():
    profiles = [CapabilityProfile("cpu", ComputeRegime.CLASSICAL, capabilities=("optimization",), estimated_cost=1, estimated_latency_ms=100)]
    result = select_substrate(contract(), profiles, signature_valid=False)
    assert result.selected_backend_id is None
    assert result.assessments[0].decision is Decision.BLOCK


def test_substrate_invariance_exact_and_statistical():
    left = {"problem_hash": "p", "metric_schema_hash": "m", "quality": 0.91, "runtime": 10}
    exact = dict(left)
    near = {**left, "quality": 0.905, "runtime": 12}
    hard = ("problem_hash", "metric_schema_hash")
    assert compare_substrate_results(left, exact, hard_invariants=hard, numeric_tolerances={"quality": 0.01}) is ComparisonStatus.EXACT
    assert compare_substrate_results(left, near, hard_invariants=hard, numeric_tolerances={"quality": 0.01}) is ComparisonStatus.STATISTICALLY_EQUIVALENT


def test_substrate_invariance_divergent_and_incomparable():
    hard = ("problem_hash", "metric_schema_hash")
    assert compare_substrate_results({"problem_hash": "a", "metric_schema_hash": "m"}, {"problem_hash": "b", "metric_schema_hash": "m"}, hard_invariants=hard) is ComparisonStatus.DIVERGENT
    assert compare_substrate_results({"problem_hash": "a"}, {"problem_hash": "a"}, hard_invariants=hard) is ComparisonStatus.INCOMPARABLE
