from app.federation_routing import (
    AdmissibilityGate,
    CapabilityGraph,
    CapabilityNode,
    Criterion,
    Crossing,
    Direction,
    PreferenceModel,
    metric_ceiling,
    metric_floor,
    requires_attr,
    tost_equivalence,
)


def build_graph():
    criteria = {
        "psi": Criterion("psi", Direction.HIGHER_IS_BETTER, 0.0, 1.0),
        "evidence": Criterion("evidence", Direction.HIGHER_IS_BETTER, 0.0, 1.0),
        "latency_ms": Criterion("latency_ms", Direction.LOWER_IS_BETTER, 1.0, 600000.0, "log"),
        "cost": Criterion("cost", Direction.LOWER_IS_BETTER, 0.01, 1000.0, "log"),
        "cvar": Criterion("cvar", Direction.LOWER_IS_BETTER, 0.0, 1.0),
    }
    preference = PreferenceModel(criteria, {"psi": 0.35, "evidence": 0.25, "latency_ms": 0.20, "cost": 0.10, "cvar": 0.10})
    nodes = [
        CapabilityNode("cpu", "infrastructure", {"psi": 0.94, "evidence": 0.92, "latency_ms": 40000, "cost": 0.40, "cvar": 0.02}, {"residency": "local"}),
        CapabilityNode("gpu", "infrastructure", {"psi": 0.94, "evidence": 0.90, "latency_ms": 3000, "cost": 2.10, "cvar": 0.02}, {"residency": "local"}),
        CapabilityNode("qsim", "infrastructure", {"psi": 0.91, "evidence": 0.62, "latency_ms": 12000, "cost": 1.30, "cvar": 0.04}, {"residency": "local"}),
        CapabilityNode("qpu", "infrastructure", {"psi": 0.80, "evidence": 0.48, "latency_ms": 480000, "cost": 220.0, "cvar": 0.19}, {"residency": "cloud"}),
    ]
    gate = AdmissibilityGate([metric_floor("psi", 0.85), metric_ceiling("cvar", 0.05), requires_attr("residency", "local")])
    crossings = [Crossing("cpu", "gpu", 0.02), Crossing("gpu", "cpu", 0.02), Crossing("cpu", "qsim", 0.05), Crossing("gpu", "qsim", 0.05), Crossing("qsim", "qpu", 0.40)]
    return CapabilityGraph(nodes, crossings, gate, preference)


def test_hard_gate_removes_qpu_instead_of_penalizing_it():
    graph = build_graph()
    assert "qpu" in graph.blocked
    assert "qpu" not in {node.node_id for node in graph.admissible}


def test_route_is_deterministic_and_receipted():
    graph = build_graph()
    first, second = graph.route("cpu"), graph.route("cpu")
    assert first.path == second.path
    assert first.receipt_sha256 == second.receipt_sha256
    assert len(first.receipt_sha256) == 64


def test_nonconservative_crossing_graph_is_reported_not_called_gradient():
    report = build_graph().conservativity_report()
    assert report["conservative"] is False
    assert report["violations"]


def test_weight_sensitivity_is_explicit():
    graph = build_graph()
    result = graph.preference.sensitivity(graph.admissible, delta=0.10)
    assert 0.0 <= result["fragility"] <= 1.0
    assert result["trials"] > 0


def test_tost_equivalence_and_divergence_are_distinct():
    equivalent = tost_equivalence(-1.1372, 0.0090, 4096, -1.1361, 0.0104, 4096, margin=0.005)
    divergent = tost_equivalence(-1.1372, 0.0090, 4096, -1.1180, 0.0140, 4096, margin=0.005)
    assert equivalent["verdict"] == "EQUIVALENT"
    assert divergent["verdict"] == "DIVERGENT"


def test_missing_metric_fails_closed():
    graph = build_graph()
    decision = graph.gate.evaluate(CapabilityNode("broken", "infrastructure", {"psi": 0.9}, {"residency": "local"}))
    assert decision.admissible is False
    assert any("missing:cvar" in reason for reason in decision.reasons)
