import json

import pytest

from app.core import Decision
from app.organism_loop import (
    AUTH_CAPABILITY,
    AuthorizationGrant,
    GovernedOrganism,
    constitutional_contract_hash,
    gate_fingerprint,
    sign_authorization_grant,
)

FROZEN = "a" * 64
STATE_SECRET = "state-secret-for-tests"
AUTHORITY_SECRETS = {"omega-authority": "omega-secret", "other-authority": "other-secret"}


def _organism(runtime_id: str = "model-a", state=None):
    return GovernedOrganism(
        organism_id="org-1",
        frozen_contract_hash=FROZEN,
        runtime_id=runtime_id,
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
        state=state,
    )


def _rejected_shell(organism: GovernedOrganism, event_id: str = "reject-1"):
    result = organism.evaluate(event_id=event_id, proposal={"action": "EXECUTE", "tool": "shell"}, signature_valid=False)
    assert result.decision is Decision.BLOCK
    return result


def _grant(candidate_id: str, principal_id: str = "omega-authority"):
    return sign_authorization_grant(
        secret=AUTHORITY_SECRETS[principal_id],
        principal_id=principal_id,
        candidate_id=candidate_id,
    )


def test_gate_fingerprint_is_deterministic_and_bound():
    first = gate_fingerprint()
    second = gate_fingerprint()
    assert len(first) == 64
    assert first == second
    assert constitutional_contract_hash(frozen_contract_hash=FROZEN) != FROZEN
    with pytest.raises(ValueError, match="at least one"):
        gate_fingerprint([])
    with pytest.raises(ValueError, match="64 hexadecimal"):
        constitutional_contract_hash(frozen_contract_hash="+" + "a" * 63)


def test_generator_cannot_self_promote_and_forged_grant_fails():
    organism = _organism()
    _rejected_shell(organism)
    candidate = organism.observe_rejection(event_id="reject-1", generator_id="omega-authority", causal_keys=["action", "tool"])
    self_grant = _grant(candidate.candidate_id)
    with pytest.raises(PermissionError, match="generator cannot authorize"):
        organism.authorize_constraint(candidate, grant=self_grant)

    candidate = organism.observe_rejection(event_id="reject-1", generator_id="model-a", causal_keys=["action", "tool"])
    forged = AuthorizationGrant("omega-authority", AUTH_CAPABILITY, candidate.candidate_id, "0" * 64)
    with pytest.raises(PermissionError, match="invalid authorization grant signature"):
        organism.authorize_constraint(candidate, grant=forged)


def test_unverified_or_pass_event_cannot_seed_constraint():
    organism = _organism()
    organism.evaluate(event_id="pass-1", proposal={"action": "READ", "tool": "shell"})
    with pytest.raises(ValueError, match="not a verified BLOCK rejection"):
        organism.observe_rejection(event_id="pass-1", generator_id="model-a", causal_keys=["action"])
    with pytest.raises(ValueError, match="exactly one prior evaluation"):
        organism.observe_rejection(event_id="missing", generator_id="model-a", causal_keys=["action"])


def test_causal_inheritance_survives_context_flush_and_runtime_swap():
    organism = _organism()
    assert organism.evaluate(event_id="before", proposal={"action": "READ", "tool": "shell"}).decision is Decision.PASS
    _rejected_shell(organism)
    candidate = organism.observe_rejection(event_id="reject-1", generator_id="model-a", causal_keys=["action", "tool"])
    constraint = organism.authorize_constraint(candidate, grant=_grant(candidate.candidate_id))
    serialized = json.loads(json.dumps(organism.export_state(), sort_keys=True))
    swapped = _organism(runtime_id="model-b", state=serialized)
    blocked = swapped.evaluate(event_id="after", proposal={"action": "EXECUTE", "tool": "shell"})
    assert blocked.decision is Decision.BLOCK
    assert blocked.matched_constraint_id == constraint.constraint_id
    assert swapped.evaluate(event_id="control", proposal={"action": "EXECUTE", "tool": "python"}).decision is Decision.PASS


def test_returned_match_is_not_a_mutation_handle():
    organism = _organism()
    _rejected_shell(organism)
    candidate = organism.observe_rejection(event_id="reject-1", generator_id="model-a", causal_keys=["action", "tool"])
    constraint = organism.authorize_constraint(candidate, grant=_grant(candidate.candidate_id))
    external = constraint.match
    external["tool"] = "python"
    assert constraint.match["tool"] == "shell"
    assert organism.evaluate(event_id="shell", proposal={"action": "EXECUTE", "tool": "shell"}).decision is Decision.BLOCK
    assert organism.evaluate(event_id="python", proposal={"action": "EXECUTE", "tool": "python"}).decision is Decision.PASS


def test_state_tamper_fails_authentication_even_if_public_root_is_recomputed():
    organism = _organism()
    organism.evaluate(event_id="e1", proposal={"action": "READ"})
    state = organism.export_state()
    state["lineage"][0]["decision"] = "BLOCK"
    body = {k: v for k, v in state.items() if k not in {"state_root", "state_mac"}}
    from app.core import stable_hash
    state["state_root"] = stable_hash(body)
    with pytest.raises(ValueError, match="state authentication failed"):
        _organism(runtime_id="runtime-b", state=state)


def test_constraint_forgery_fails_even_with_valid_state_mac_if_authority_signature_is_invalid():
    organism = _organism()
    _rejected_shell(organism)
    candidate = organism.observe_rejection(event_id="reject-1", generator_id="model-a", causal_keys=["action", "tool"])
    organism.authorize_constraint(candidate, grant=_grant(candidate.candidate_id))
    state = organism.export_state()
    payload = {k: v for k, v in state.items() if k not in {"state_root", "state_mac"}}
    payload["constraints"][0]["authorization_signature"] = "0" * 64
    import hashlib, hmac
    from app.core import stable_hash
    from app.evidence import canonical_json
    state = {**payload, "state_root": stable_hash(payload)}
    state["state_mac"] = hmac.new(STATE_SECRET.encode(), canonical_json(payload).encode(), hashlib.sha256).hexdigest()
    with pytest.raises(PermissionError, match="invalid authorization grant signature"):
        _organism(runtime_id="model-b", state=state)


def test_null_match_does_not_match_missing_field():
    organism = _organism()
    result = organism.evaluate(event_id="reject-null", proposal={"action": "EXECUTE", "tool": None}, signature_valid=False)
    assert result.decision is Decision.BLOCK
    candidate = organism.observe_rejection(event_id="reject-null", generator_id="model-a", causal_keys=["tool"])
    organism.authorize_constraint(candidate, grant=_grant(candidate.candidate_id))
    assert organism.evaluate(event_id="missing-tool", proposal={"action": "READ"}).decision is Decision.PASS


def test_hdb_and_omega_inputs_change_receipt_and_still_gate():
    organism = _organism()
    blocked = organism.evaluate(event_id="hdb", proposal={"action": "EXPORT"}, human={"serialize_human": True})
    assert blocked.decision is Decision.BLOCK
    assert "serialized" in blocked.reason
    invalid_signature = organism.evaluate(event_id="sig", proposal={"action": "EXECUTE"}, signature_valid=False)
    assert invalid_signature.decision is Decision.BLOCK
    assert invalid_signature.reason == "invalid signature"
    pass_a = organism.evaluate(event_id="pass-a", proposal={"action": "READ"}, human=None)
    pass_b = organism.evaluate(event_id="pass-b", proposal={"action": "READ"}, human={"consent": True, "purpose": "test"})
    assert pass_a.decision is Decision.PASS and pass_b.decision is Decision.PASS
    assert pass_a.evidence["receipt_hash"] != pass_b.evidence["receipt_hash"]
