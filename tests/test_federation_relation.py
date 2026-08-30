from dataclasses import replace

import pytest

from app.federation_relation import (
    HMAC_SHARED_SECRET_SCHEME,
    FederatedCapabilityGraph,
    FederatedCrossing,
    FederationRelation,
    RelationIntegrityGate,
    RelationRequest,
    RelationStatus,
    RelationWitness,
    sign_relation,
)
from app.federation_routing import (
    AdmissibilityGate,
    CapabilityNode,
    Criterion,
    Direction,
    PreferenceModel,
)

CONTRACT = "a" * 64
OTHER_CONTRACT = "b" * 64
SOURCE_SECRET = "source-secret-for-tests"
TARGET_SECRET = "target-secret-for-tests"


def relation(
    *,
    capability: str = "state.transfer",
    status: RelationStatus = RelationStatus.ACTIVE,
    witness_scheme: str = HMAC_SHARED_SECRET_SCHEME,
) -> FederationRelation:
    return FederationRelation(
        relation_id="rel-a-b-v1",
        source_domain="domain-a",
        target_domain="domain-b",
        source_authority="authority-a",
        target_authority="authority-b",
        contract_hash=CONTRACT,
        capabilities=(capability,),
        valid_from=100,
        valid_until=200,
        status=status,
        witness_scheme=witness_scheme,
    )


def signed_relation(**kwargs) -> FederationRelation:
    return sign_relation(
        relation(**kwargs),
        source_secret=SOURCE_SECRET,
        target_secret=TARGET_SECRET,
    )


def relation_gate(now: int = 150) -> RelationIntegrityGate:
    return RelationIntegrityGate(
        {
            "authority-a": SOURCE_SECRET,
            "authority-b": TARGET_SECRET,
        },
        now=lambda: now,
    )


def routing_graph(
    rel: FederationRelation,
    *,
    crossing_contract: str = CONTRACT,
    capability: str = "state.transfer",
    now: int = 150,
):
    nodes = [
        CapabilityNode("domain-a", "domain", {"quality": 0.5}),
        CapabilityNode("domain-b", "domain", {"quality": 0.9}),
    ]
    preference = PreferenceModel(
        {"quality": Criterion("quality", Direction.HIGHER_IS_BETTER, 0.0, 1.0)},
        {"quality": 1.0},
    )
    return FederatedCapabilityGraph(
        nodes=nodes,
        crossings=[
            FederatedCrossing(
                "domain-a",
                "domain-b",
                0.1,
                rel.relation_id,
                capability,
                crossing_contract,
            )
        ],
        capability_gate=AdmissibilityGate([]),
        preference=preference,
        relations=[rel],
        relation_gate=relation_gate(now),
    )


def test_bilateral_witness_allows_only_scoped_cross_domain_route():
    graph = routing_graph(signed_relation())
    result = graph.route("domain-a", ["domain-b"])
    assert result.route.path == ("domain-a", "domain-b")
    assert result.traversed_relations == ("rel-a-b-v1",)
    assert len(result.relation_receipt_sha256) == 64
    assert result.blocked_relations == {}


def test_missing_witness_fails_closed_before_routing():
    graph = routing_graph(relation())
    assert any(
        "missing_bilateral_witness" in reasons
        for reasons in graph.blocked_relations.values()
    )
    with pytest.raises(ValueError, match="no admissible target is reachable"):
        graph.route("domain-a", ["domain-b"])


def test_tampering_after_witness_invalidates_relation():
    original = signed_relation()
    tampered = replace(original, evidence_policy="different-policy")
    decision = relation_gate().evaluate(
        tampered,
        RelationRequest("domain-a", "domain-b", CONTRACT, "state.transfer"),
    )
    assert decision.admissible is False
    assert "witness_payload_hash_mismatch" in decision.reasons
    assert "invalid_source_witness" in decision.reasons
    assert "invalid_target_witness" in decision.reasons


def test_invalid_target_witness_fails_closed():
    original = signed_relation()
    assert original.witness is not None
    forged = replace(
        original,
        witness=RelationWitness(
            scheme=HMAC_SHARED_SECRET_SCHEME,
            payload_sha256=original.witness.payload_sha256,
            source_hmac_sha256=original.witness.source_hmac_sha256,
            target_hmac_sha256="0" * 64,
        ),
    )
    decision = relation_gate().evaluate(
        forged,
        RelationRequest("domain-a", "domain-b", CONTRACT, "state.transfer"),
    )
    assert decision.admissible is False
    assert decision.reasons == ("invalid_target_witness",)


def test_expired_relation_is_blocked_even_with_valid_witnesses():
    decision = relation_gate(now=250).evaluate(
        signed_relation(),
        RelationRequest("domain-a", "domain-b", CONTRACT, "state.transfer"),
    )
    assert decision.admissible is False
    assert decision.evaluated_at == 250
    assert "relation_expired" in decision.reasons


def test_expired_relation_cannot_be_reopened_by_routing_input():
    graph = routing_graph(signed_relation(), now=250)
    assert any("relation_expired" in reasons for reasons in graph.blocked_relations.values())
    with pytest.raises(ValueError, match="no admissible target is reachable"):
        graph.route("domain-a", ["domain-b"])


def test_contract_drift_blocks_crossing():
    graph = routing_graph(signed_relation(), crossing_contract=OTHER_CONTRACT)
    assert any(
        "contract_hash_mismatch" in reasons
        for reasons in graph.blocked_relations.values()
    )
    with pytest.raises(ValueError, match="no admissible target is reachable"):
        graph.route("domain-a", ["domain-b"])


def test_capability_out_of_scope_blocks_crossing():
    graph = routing_graph(signed_relation(), capability="model.invoke")
    assert any(
        "capability_out_of_scope" in reasons
        for reasons in graph.blocked_relations.values()
    )


def test_revoked_relation_cannot_be_reused():
    decision = relation_gate().evaluate(
        signed_relation(status=RelationStatus.REVOKED),
        RelationRequest("domain-a", "domain-b", CONTRACT, "state.transfer"),
    )
    assert decision.admissible is False
    assert "status:REVOKED" in decision.reasons


def test_unknown_target_authority_fails_closed():
    gate = RelationIntegrityGate(
        {"authority-a": SOURCE_SECRET},
        now=lambda: 150,
    )
    decision = gate.evaluate(
        signed_relation(),
        RelationRequest("domain-a", "domain-b", CONTRACT, "state.transfer"),
    )
    assert decision.admissible is False
    assert "unknown_target_authority" in decision.reasons


def test_unsupported_witness_scheme_is_not_silently_downgraded():
    unsupported = relation(witness_scheme="ED25519-V1")
    with pytest.raises(ValueError, match="unsupported witness scheme"):
        sign_relation(
            unsupported,
            source_secret=SOURCE_SECRET,
            target_secret=TARGET_SECRET,
        )
    decision = relation_gate().evaluate(
        unsupported,
        RelationRequest("domain-a", "domain-b", CONTRACT, "state.transfer"),
    )
    assert decision.admissible is False
    assert "unsupported_witness_scheme" in decision.reasons
    assert "missing_bilateral_witness" in decision.reasons


def test_relation_receipt_is_deterministic():
    graph = routing_graph(signed_relation())
    first = graph.route("domain-a", ["domain-b"])
    second = graph.route("domain-a", ["domain-b"])
    assert first.relation_receipt_sha256 == second.relation_receipt_sha256
