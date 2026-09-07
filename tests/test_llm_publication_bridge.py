import pytest

from app.llm_publication_bridge import (
    AuthorityKind,
    BridgeError,
    CapabilityRegistry,
    LLMPublicationBridge,
    ReviewDecision,
    Role,
    Stage,
    WorkItem,
    canonical_matverse_capabilities,
)


def work() -> WorkItem:
    return WorkItem(
        work_id="paper-001",
        artifact_sha256="a" * 64,
        title="MatVerse governed publication test",
        source_ref="local://papers/test.md",
        publication_kind="living-paper",
    )


def bridge() -> LLMPublicationBridge:
    return LLMPublicationBridge(CapabilityRegistry(canonical_matverse_capabilities()))


def test_registry_contains_named_llm_capabilities() -> None:
    inventory = CapabilityRegistry(canonical_matverse_capabilities()).public_inventory()
    assert {item["capability_id"] for item in inventory} == {"gpt", "claude-code", "manus", "minimax", "local"}


def test_llm_can_propose_but_not_smuggle_authority() -> None:
    b = bridge()
    proposal = b.propose(
        work(),
        capability_id="gpt",
        role=Role.DRAFT,
        payload={"summary": "correct section 3", "changes": ["replace unsupported claim"]},
    )
    assert proposal.capability_id == "gpt"
    assert proposal.payload_hash
    with pytest.raises(BridgeError, match="authority/secret"):
        b.propose(
            work(),
            capability_id="gpt",
            role=Role.DRAFT,
            payload={"publish": True},
        )


def test_generator_and_reviewer_must_be_independent() -> None:
    b = bridge()
    proposal = b.propose(work(), capability_id="gpt", role=Role.DRAFT, payload={"text": "draft"})
    with pytest.raises(BridgeError, match="independent principals"):
        b.review(
            proposal,
            reviewer_capability_id="gpt",
            decision=ReviewDecision.APPROVE,
        )


def test_llm_review_does_not_equal_authorization() -> None:
    b = bridge()
    proposal = b.propose(work(), capability_id="claude-code", role=Role.CODE, payload={"patch": "safe patch"})
    review = b.review(
        proposal,
        reviewer_capability_id="minimax",
        decision=ReviewDecision.APPROVE,
        findings=("no unsupported claim",),
    )
    with pytest.raises(BridgeError, match="external write requires authorization"):
        b.plan_action(work(), proposal, action="git.merge", target="main")
    authorization = b.authorize(
        proposal,
        review,
        action="git.merge",
        principal="human:owner",
        authority_kind=AuthorityKind.HUMAN,
        scope="repo:MatVerse-py/Gpt-project-bridge",
    )
    action = b.plan_action(work(), proposal, action="git.merge", target="main", authorization=authorization)
    assert action.side_effect is True
    assert action.authorization_id == authorization.authorization_id


def test_blocked_review_cannot_be_authorized() -> None:
    b = bridge()
    proposal = b.propose(work(), capability_id="gpt", role=Role.METADATA, payload={"doi_relation": "candidate"})
    review = b.review(
        proposal,
        reviewer_capability_id="minimax",
        decision=ReviewDecision.BLOCK,
        findings=("metadata conflicts with canonical record",),
    )
    with pytest.raises(BridgeError, match="APPROVE"):
        b.authorize(
            proposal,
            review,
            action="zenodo.publish",
            principal="human:owner",
            authority_kind=AuthorityKind.HUMAN,
            scope="publication:paper-001",
        )


def test_authorizer_cannot_be_llm_principal() -> None:
    b = bridge()
    proposal = b.propose(work(), capability_id="gpt", role=Role.DRAFT, payload={"text": "draft"})
    review = b.review(proposal, reviewer_capability_id="minimax", decision=ReviewDecision.APPROVE)
    with pytest.raises(BridgeError, match="independent"):
        b.authorize(
            proposal,
            review,
            action="zenodo.publish",
            principal="llm:openai:gpt",
            authority_kind=AuthorityKind.CONSTITUTIONAL,
            scope="publication:paper-001",
        )


def test_state_machine_requires_external_authority_to_publish() -> None:
    b = bridge()
    item = work()
    state = b.initial_state(item)
    for target in (
        Stage.ANALYZED,
        Stage.GIT_STAGED,
        Stage.GOVERNANCE_PASS,
        Stage.HF_STAGED,
        Stage.ZENODO_DRAFT,
        Stage.COMMUNITY_REVIEW,
    ):
        state = b.transition(state, target)
    with pytest.raises(BridgeError, match="requires external authorization"):
        b.transition(state, Stage.PUBLISH_AUTHORIZED)

    proposal = b.propose(item, capability_id="gpt", role=Role.DRAFT, payload={"text": "final candidate"})
    review = b.review(proposal, reviewer_capability_id="minimax", decision=ReviewDecision.APPROVE)
    authorization = b.authorize(
        proposal,
        review,
        action="zenodo.publish",
        principal="human:owner",
        authority_kind=AuthorityKind.HUMAN,
        scope="publication:paper-001",
    )
    state = b.transition(state, Stage.PUBLISH_AUTHORIZED, authorization=authorization)
    state = b.transition(state, Stage.PUBLISHED, authorization=authorization)
    assert state.stage is Stage.PUBLISHED
    assert state.history[-2:] == ("PUBLISH_AUTHORIZED", "PUBLISHED")


def test_receipts_are_generated_for_proposal_review_authorization_and_action() -> None:
    b = bridge()
    item = work()
    proposal = b.propose(item, capability_id="claude-code", role=Role.CODE, payload={"patch": "x"})
    review = b.review(proposal, reviewer_capability_id="minimax", decision=ReviewDecision.APPROVE)
    authorization = b.authorize(
        proposal,
        review,
        action="hf.push",
        principal="human:owner",
        authority_kind=AuthorityKind.HUMAN,
        scope="hf:MatVerseHub/corpus",
    )
    action = b.plan_action(item, proposal, action="hf.push", target="MatVerseHub/corpus", authorization=authorization)
    for receipt in (proposal.receipt, review.receipt, authorization.receipt, action.receipt):
        assert receipt["schema"] == "matverse.evidence-receipt.v1"
        assert len(receipt["receipt_hash"]) == 64
