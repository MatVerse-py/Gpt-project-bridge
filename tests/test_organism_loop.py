import json

import pytest

from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism, constitutional_contract_hash, gate_fingerprint

FROZEN = "a" * 64


def _rejected_shell(organism: GovernedOrganism, event_id: str = "reject-1"):
    result = organism.evaluate(
        event_id=event_id,
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=False,
    )
    assert result.decision is Decision.BLOCK
    return result


def test_gate_fingerprint_is_deterministic_and_bound():
    first = gate_fingerprint()
    second = gate_fingerprint()
    assert len(first) == 64
    assert first == second
    assert constitutional_contract_hash(frozen_contract_hash=FROZEN) != FROZEN


def test_generator_cannot_self_promote_constraint():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="runtime-a")
    _rejected_shell(organism)
    candidate = organism.observe_rejection(event_id="reject-1", generator_id="gen-a", causal_keys=["action", "tool"])
    with pytest.raises(PermissionError):
        organism.authorize_constraint(candidate, authorizer_id="gen-a")


def test_unverified_or_pass_event_cannot_seed_constraint():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="model-a")
    organism.evaluate(event_id="pass-1", proposal={"action": "READ", "tool": "shell"})
    with pytest.raises(ValueError, match="not a verified BLOCK rejection"):
        organism.observe_rejection(event_id="pass-1", generator_id="model-a", causal_keys=["action"])
    with pytest.raises(ValueError, match="exactly one prior evaluation"):
        organism.observe_rejection(event_id="missing", generator_id="model-a", causal_keys=["action"])


def test_causal_inheritance_survives_context_flush_and_runtime_swap():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="model-a")
    baseline = organism.evaluate(event_id="before", proposal={"action": "READ", "tool": "shell"})
    assert baseline.decision is Decision.PASS
    _rejected_shell(organism)
    candidate = organism.observe_rejection(event_id="reject-1", generator_id="model-a", causal_keys=["action", "tool"])
    constraint = organism.authorize_constraint(candidate, authorizer_id="omega-authority")

    serialized = json.loads(json.dumps(organism.export_state(), sort_keys=True))
    swapped = GovernedOrganism(
        organism_id="org-1",
        frozen_contract_hash=FROZEN,
        runtime_id="model-b",
        state=serialized,
    )
    blocked = swapped.evaluate(event_id="after", proposal={"action": "EXECUTE", "tool": "shell"})
    assert blocked.decision is Decision.BLOCK
    assert blocked.matched_constraint_id == constraint.constraint_id

    control = swapped.evaluate(event_id="control", proposal={"action": "EXECUTE", "tool": "python"})
    assert control.decision is Decision.PASS


def test_returned_match_is_not_a_mutation_handle():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="model-a")
    _rejected_shell(organism)
    candidate = organism.observe_rejection(event_id="reject-1", generator_id="model-a", causal_keys=["action", "tool"])
    constraint = organism.authorize_constraint(candidate, authorizer_id="omega-authority")
    external = constraint.match
    external["tool"] = "python"
    assert constraint.match["tool"] == "shell"
    assert organism.evaluate(event_id="shell", proposal={"action": "EXECUTE", "tool": "shell"}).decision is Decision.BLOCK
    assert organism.evaluate(event_id="python", proposal={"action": "EXECUTE", "tool": "python"}).decision is Decision.PASS


def test_lineage_tamper_fails_closed():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="runtime-a")
    organism.evaluate(event_id="e1", proposal={"action": "READ"})
    state = organism.export_state()
    state["lineage"][0]["decision"] = "BLOCK"
    with pytest.raises(ValueError, match="state root mismatch"):
        GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="runtime-b", state=state)


def test_constraint_internal_tamper_fails_closed_even_if_root_is_recomputed():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="model-a")
    _rejected_shell(organism)
    candidate = organism.observe_rejection(event_id="reject-1", generator_id="model-a", causal_keys=["action", "tool"])
    organism.authorize_constraint(candidate, authorizer_id="omega-authority")
    state = organism.export_state()
    state["constraints"][0]["reason"] = "tampered reason"
    body = dict(state)
    body.pop("state_root")
    state["state_root"] = stable_hash(body)
    with pytest.raises(ValueError, match="constraint authority receipt mismatch"):
        GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="model-b", state=state)


def test_hdb_and_omega_still_gate_noninherited_paths():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="runtime-a")
    blocked = organism.evaluate(
        event_id="hdb",
        proposal={"action": "EXPORT"},
        human={"serialize_human": True},
    )
    assert blocked.decision is Decision.BLOCK
    assert "serialized" in blocked.reason

    invalid_signature = organism.evaluate(
        event_id="sig",
        proposal={"action": "EXECUTE"},
        signature_valid=False,
    )
    assert invalid_signature.decision is Decision.BLOCK
    assert invalid_signature.reason == "invalid signature"
