from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism, sign_authorization_grant
from app.organism_root_cache import CachedRootGovernedOrganism

FROZEN = "a" * 64
STATE_SECRET = "root-cache-test-state"
AUTHORITY_SECRETS = {"omega-authority": "root-cache-test-authority"}


def _make(cls):
    organism = cls(
        organism_id="root-cache-organism",
        frozen_contract_hash=FROZEN,
        runtime_id="root-cache-runtime",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )
    seed = organism.evaluate(
        event_id="seed-reject",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=False,
    )
    assert seed.decision is Decision.BLOCK
    candidate = organism.observe_rejection(
        event_id="seed-reject",
        generator_id="constraint-generator",
        causal_keys=["action", "tool"],
    )
    grant = sign_authorization_grant(
        secret=AUTHORITY_SECRETS["omega-authority"],
        principal_id="omega-authority",
        candidate_id=candidate.candidate_id,
    )
    constraint = organism.authorize_constraint(candidate, grant=grant)
    return organism, constraint.constraint_id


def test_cached_root_is_byte_identical_to_canonical_root_across_mutations():
    base, base_constraint = _make(GovernedOrganism)
    cached, cached_constraint = _make(CachedRootGovernedOrganism)
    assert base_constraint == cached_constraint
    assert base.export_state() == cached.export_state()

    for i in range(40):
        inherited = i % 3 == 0
        proposal = {"action": "EXECUTE", "tool": "shell" if inherited else "python", "i": i}
        base_result = base.evaluate(event_id=f"e{i}", proposal=proposal)
        cached_result = cached.evaluate(event_id=f"e{i}", proposal=proposal)

        assert cached_result.decision is base_result.decision
        assert cached_result.reason == base_result.reason
        assert cached_result.matched_constraint_id == base_result.matched_constraint_id
        assert cached_result.evidence == base_result.evidence
        assert cached_result.state_root == base_result.state_root
        assert cached.state_root() == stable_hash(cached.state_payload())

    assert cached.export_state() == base.export_state()


def test_cached_root_reuses_unchanged_state_and_invalidates_after_evaluate():
    organism, _ = _make(CachedRootGovernedOrganism)

    first = organism.state_root()
    recomputations = organism.state_root_recomputations
    assert organism.state_root() == first
    assert organism.state_root_recomputations == recomputations

    result = organism.evaluate(
        event_id="cache-invalidation-event",
        proposal={"action": "OBSERVE", "payload": "new-event"},
    )
    assert result.state_root != first
    recomputations_after_event = organism.state_root_recomputations
    assert organism.state_root() == result.state_root
    assert organism.state_root_recomputations == recomputations_after_event


def test_cached_root_restores_authenticated_state_without_semantic_drift():
    organism, _ = _make(CachedRootGovernedOrganism)
    for i in range(6):
        organism.evaluate(event_id=f"restore-e{i}", proposal={"action": "OBSERVE", "i": i})
    state = organism.export_state()

    restored = CachedRootGovernedOrganism(
        organism_id="root-cache-organism",
        frozen_contract_hash=FROZEN,
        runtime_id="root-cache-runtime-restored",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
        state=state,
    )
    assert restored.state_root() == organism.state_root()
    assert restored.export_state()["state_root"] == state["state_root"]
