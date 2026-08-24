import json

import pytest

from app.core import Decision
from app.organism_loop import GovernedOrganism, constitutional_contract_hash, gate_fingerprint

FROZEN = "a" * 64


def test_gate_fingerprint_is_deterministic_and_bound():
    first = gate_fingerprint()
    second = gate_fingerprint()
    assert len(first) == 64
    assert first == second
    assert constitutional_contract_hash(frozen_contract_hash=FROZEN) != FROZEN


def test_generator_cannot_self_promote_constraint():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="runtime-a")
    candidate = organism.observe_rejection(
        event_id="e1",
        generator_id="gen-a",
        proposal={"action": "EXECUTE", "tool": "shell"},
        reason="shell execution rejected",
        causal_keys=["action", "tool"],
    )
    with pytest.raises(PermissionError):
        organism.authorize_constraint(candidate, authorizer_id="gen-a")


def test_causal_inheritance_survives_context_flush_and_runtime_swap():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="model-a")
    baseline = organism.evaluate(event_id="before", proposal={"action": "READ", "tool": "shell"})
    assert baseline.decision is Decision.PASS

    candidate = organism.observe_rejection(
        event_id="reject-1",
        generator_id="model-a",
        proposal={"action": "EXECUTE", "tool": "shell"},
        reason="shell execution rejected after adverse effect",
        causal_keys=["action", "tool"],
    )
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


def test_state_tamper_fails_closed():
    organism = GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="runtime-a")
    state = organism.export_state()
    state["state_root"] = "0" * 64
    with pytest.raises(ValueError, match="state root mismatch"):
        GovernedOrganism(organism_id="org-1", frozen_contract_hash=FROZEN, runtime_id="runtime-b", state=state)


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
