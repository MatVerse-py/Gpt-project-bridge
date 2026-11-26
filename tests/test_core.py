from app.core import Decision, HDBResult, omega_gate


def test_valid_signature_does_not_override_invalid_transition():
    decision, reason = omega_gate(hdb=HDBResult(Decision.PASS, "ok"), action="COMMIT", ontology_ok=True, signature_valid=True, transition_valid=False)
    assert decision is Decision.BLOCK
    assert "valid_signature" in reason



def test_unresolved_ontology_fails_closed_as_hold():
    decision, reason = omega_gate(
        hdb=HDBResult(Decision.PASS, "ok"),
        action="EXECUTE",
        ontology_ok=None,
        signature_valid=True,
        transition_valid=True,
    )
    assert decision is Decision.HOLD
    assert "ontology unresolved" in reason


def test_unresolved_transition_fails_closed_as_hold():
    decision, reason = omega_gate(
        hdb=HDBResult(Decision.PASS, "ok"),
        action="EXECUTE",
        ontology_ok=True,
        signature_valid=True,
        transition_valid=None,
    )
    assert decision is Decision.HOLD
    assert "transition contract unresolved" in reason


def test_known_contract_violation_blocks_even_if_other_inputs_are_unknown():
    decision, reason = omega_gate(
        hdb=HDBResult(Decision.PASS, "ok"),
        action="EXECUTE",
        ontology_ok=True,
        signature_valid=True,
        transition_valid=False,
    )
    assert decision is Decision.BLOCK
    assert "transition contract violation" in reason
