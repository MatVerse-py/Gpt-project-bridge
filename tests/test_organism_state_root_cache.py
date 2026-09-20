from unittest.mock import patch

from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism, sign_authorization_grant

FROZEN = "a" * 64
STATE_SECRET = "canonical-root-cache-state"
AUTHORITY_SECRETS = {"omega-authority": "canonical-root-cache-authority"}


def _make() -> GovernedOrganism:
    organism = GovernedOrganism(
        organism_id="canonical-root-cache-organism",
        frozen_contract_hash=FROZEN,
        runtime_id="canonical-root-cache-runtime",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )
    seed = organism.evaluate(
        event_id="canonical-cache-seed",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=False,
    )
    assert seed.decision is Decision.BLOCK
    candidate = organism.observe_rejection(
        event_id="canonical-cache-seed",
        generator_id="canonical-cache-generator",
        causal_keys=["action", "tool"],
    )
    grant = sign_authorization_grant(
        secret=AUTHORITY_SECRETS["omega-authority"],
        principal_id="omega-authority",
        candidate_id=candidate.candidate_id,
    )
    organism.authorize_constraint(candidate, grant=grant)
    return organism


def test_state_root_is_exact_payload_hash_and_reused_until_mutation():
    organism = _make()

    with patch("app.organism_loop.stable_hash", wraps=stable_hash) as hashed:
        root1 = organism.state_root()
        calls_after_first = hashed.call_count
        assert calls_after_first >= 1
        assert organism.state_root() == root1
        assert hashed.call_count == calls_after_first

        result = organism.evaluate(
            event_id="canonical-cache-next",
            proposal={"action": "OBSERVE", "payload": "cache-mutation"},
        )
        calls_after_evaluate = hashed.call_count
        assert result.state_root != root1
        assert organism.state_root() == result.state_root
        assert hashed.call_count == calls_after_evaluate

    assert organism.state_root() == stable_hash(organism.state_payload())


def test_export_and_restore_preserve_exact_authenticated_root_with_cache():
    organism = _make()
    for i in range(10):
        organism.evaluate(event_id=f"canonical-restore-{i}", proposal={"action": "OBSERVE", "i": i})

    exported = organism.export_state()
    assert exported["state_root"] == stable_hash(organism.state_payload())

    restored = GovernedOrganism(
        organism_id="canonical-root-cache-organism",
        frozen_contract_hash=FROZEN,
        runtime_id="canonical-root-cache-runtime-restored",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
        state=exported,
    )
    assert restored.state_root() == exported["state_root"]
    assert restored.export_state()["state_root"] == exported["state_root"]


def test_internal_hash_view_remains_exact_across_lineage_growth():
    organism = _make()
    for i in range(75):
        organism.evaluate(
            event_id=f"canonical-hash-view-{i}",
            proposal={
                "action": "EXECUTE" if i % 3 == 0 else "OBSERVE",
                "tool": "shell" if i % 3 == 0 else "python",
                "i": i,
            },
        )
        assert organism.state_root() == stable_hash(organism.state_payload())


def test_public_state_payload_remains_defensive_copy_after_hash_view_promotion():
    organism = _make()
    original_root = organism.state_root()
    payload = organism.state_payload()

    payload["lineage"][0]["decision"] = Decision.PASS.value
    payload["lineage"].append({"type": "FORGED"})

    assert organism.state_root() == original_root
    assert stable_hash(payload) != original_root
    fresh = organism.state_payload()
    assert fresh["lineage"][0]["decision"] != Decision.PASS.value
    assert all(item.get("type") != "FORGED" for item in fresh["lineage"])
