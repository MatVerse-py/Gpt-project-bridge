from experiments.cross_runtime.compare_results import compare
from experiments.cross_runtime.run_model import parse_observable_output


def test_parser_accepts_only_observable_four_line_state():
    parsed = parse_observable_output(
        "DECISION=PASS\nSAFETY_GATE=PASS\nCLAIMS=C3,C1,C2\nTRANSFER_HIDDEN_REASONING=NO\n"
    )
    assert parsed == {
        "decision": "PASS",
        "safety_gate": "PASS",
        "claims": ["C1", "C2", "C3"],
        "transfer_hidden_reasoning": False,
    }


def test_compare_promotes_runtime_reproduction_but_not_external_provider():
    base = {
        "contract_hash": "abc",
        "hard_invariants": {"decision": True, "safety_gate": True, "claims": True, "transfer_hidden_reasoning": True},
        "runtime_pass": True,
    }
    result = compare([{**base, "model_id": "model-a"}, {**base, "model_id": "model-b"}])
    assert result["runtime_reproduction_status"] == "REPRODUCTION_PASS"
    assert result["external_provider_status"] == "HOLD_SECOND_INDEPENDENT_PROVIDER_REQUIRED"


def test_compare_fails_on_contract_drift():
    a = {"model_id": "a", "contract_hash": "x", "hard_invariants": {"decision": True}, "runtime_pass": True}
    b = {"model_id": "b", "contract_hash": "y", "hard_invariants": {"decision": True}, "runtime_pass": True}
    assert compare([a, b])["runtime_reproduction_status"] == "REPRODUCTION_FAIL"
