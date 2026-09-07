from __future__ import annotations

import pytest

from app.evidence import sha256_text
from app.omega_canpublish import (
    CanPublishError,
    MarxivStage,
    OmegaCanPublishGate,
    PublishDecision,
)


def make_gate(now: int = 1_000_000_000_000):
    clock = {"now": now}
    gate = OmegaCanPublishGate(lambda: clock["now"])
    return gate, clock


def propose(gate: OmegaCanPublishGate, proposal_id: str = "p1"):
    return gate.propose(
        proposal_id=proposal_id,
        intended_action="publish_manifest",
        payload_hash=sha256_text("payload"),
        proposer_key_id="body-a",
        merge_root=sha256_text("private-root"),
        hold_ttl_seconds=60,
    )


def test_proposal_is_hold_and_marxiv_prepared():
    gate, _ = make_gate()
    proposal = propose(gate)

    assert gate.organ == "Ω-GATE::CanPublish"
    assert proposal.gate_decision is PublishDecision.HOLD
    assert proposal.marxiv_stage is MarxivStage.PREPARED
    assert proposal.adjudicator_key_id is None


def test_same_principal_cannot_adjudicate():
    gate, _ = make_gate()
    propose(gate)

    with pytest.raises(CanPublishError) as exc:
        gate.adjudicate(
            "p1",
            decision=PublishDecision.ADMIT,
            adjudicator_key_id="body-a",
            policy_id="policy://canpublish/v1",
            evidence_ref="receipt://review/1",
            signature="sig:1",
        )

    assert exc.value.status == 409


def test_admit_is_approved_not_submitted():
    gate, _ = make_gate()
    propose(gate)

    approved = gate.adjudicate(
        "p1",
        decision=PublishDecision.ADMIT,
        adjudicator_key_id="body-d",
        policy_id="policy://canpublish/v1",
        evidence_ref="receipt://review/1",
        signature="sig:1",
    )

    assert approved.gate_decision is PublishDecision.ADMIT
    assert approved.marxiv_stage is MarxivStage.APPROVED
    assert approved.provider_receipt is None


def test_body_x_is_distinct_and_only_execute_reaches_submitted():
    gate, _ = make_gate()
    propose(gate)
    gate.adjudicate(
        "p1",
        decision=PublishDecision.ADMIT,
        adjudicator_key_id="body-d",
        policy_id="policy://canpublish/v1",
        evidence_ref="receipt://review/1",
        signature="sig:1",
    )

    with pytest.raises(CanPublishError) as exc:
        gate.execute(
            "p1",
            executor_key_id="body-d",
            provider_receipt="zenodo://receipt/1",
        )
    assert exc.value.status == 409

    submitted = gate.execute(
        "p1",
        executor_key_id="body-x",
        provider_receipt="zenodo://receipt/1",
    )
    assert submitted.marxiv_stage is MarxivStage.SUBMITTED
    assert submitted.provider_receipt == "zenodo://receipt/1"


def test_hold_ttl_expires_to_block_and_cannot_be_admitted():
    gate, clock = make_gate()
    propose(gate)

    clock["now"] += 61 * 1_000_000_000
    assert gate.get("p1").gate_decision is PublishDecision.BLOCK

    with pytest.raises(CanPublishError) as exc:
        gate.adjudicate(
            "p1",
            decision=PublishDecision.ADMIT,
            adjudicator_key_id="body-d",
            policy_id="policy://canpublish/v1",
            evidence_ref="receipt://review/1",
            signature="sig:1",
        )
    assert exc.value.status == 409
    assert gate.get("p1").gate_decision is PublishDecision.BLOCK


def test_execute_requires_admit_and_provider_receipt():
    gate, _ = make_gate()
    propose(gate)

    with pytest.raises(CanPublishError) as exc:
        gate.execute(
            "p1",
            executor_key_id="body-x",
            provider_receipt="zenodo://receipt/1",
        )
    assert exc.value.status == 412
