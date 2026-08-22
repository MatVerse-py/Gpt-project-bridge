from app.core import Decision, HDBResult, omega_gate


def test_valid_signature_does_not_override_invalid_transition():
    decision, reason = omega_gate(hdb=HDBResult(Decision.PASS, "ok"), action="COMMIT", ontology_ok=True, signature_valid=True, transition_valid=False)
    assert decision is Decision.BLOCK
    assert "valid_signature" in reason
