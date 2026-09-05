import pytest

from app.federation_relation import (
    FederatedCapabilityGraph,
    FederatedCrossing,
    FederationRelation,
    RelationIntegrityGate,
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
CAPABILITY = "state.transfer"


def preference() -> PreferenceModel:
    return PreferenceModel(
        {"quality": Criterion("quality", Direction.HIGHER_IS_BETTER, 0.0, 1.0)},
        {"quality": 1.0},
    )


def relation(
    relation_id: str,
    src: str,
    dst: str,
    source_authority: str,
    target_authority: str,
    capabilities: tuple[str, ...],
    source_secret: str,
    target_secret: str,
) -> FederationRelation:
    return sign_relation(
        FederationRelation(
            relation_id=relation_id,
            source_domain=src,
            target_domain=dst,
            source_authority=source_authority,
            target_authority=target_authority,
            contract_hash=CONTRACT,
            capabilities=capabilities,
            valid_from=100,
            valid_until=200,
        ),
        source_secret=source_secret,
        target_secret=target_secret,
    )


def test_multi_hop_route_requires_same_requested_capability_on_every_boundary():
    secrets = {"auth-a": "secret-a", "auth-b": "secret-b", "auth-c": "secret-c"}
    rel_ab = relation(
        "rel-ab",
        "a",
        "b",
        "auth-a",
        "auth-b",
        (CAPABILITY,),
        secrets["auth-a"],
        secrets["auth-b"],
    )
    rel_bc = relation(
        "rel-bc",
        "b",
        "c",
        "auth-b",
        "auth-c",
        ("model.invoke",),
        secrets["auth-b"],
        secrets["auth-c"],
    )
    graph = FederatedCapabilityGraph(
        nodes=[
            CapabilityNode("a", "domain", {"quality": 0.4}),
            CapabilityNode("b", "domain", {"quality": 0.6}),
            CapabilityNode("c", "domain", {"quality": 0.9}),
        ],
        crossings=[
            FederatedCrossing("a", "b", 0.1, "rel-ab", CAPABILITY, CONTRACT),
            FederatedCrossing("b", "c", 0.1, "rel-bc", "model.invoke", CONTRACT),
        ],
        capability_gate=AdmissibilityGate([]),
        preference=preference(),
        relations=[rel_ab, rel_bc],
        relation_gate=RelationIntegrityGate(secrets, now=lambda: 150),
    )

    blocked = graph.blocked_for(CAPABILITY)
    assert any("crossing_capability_mismatch" in reasons for reasons in blocked.values())
    assert any("capability_out_of_scope" in reasons for reasons in blocked.values())
    with pytest.raises(ValueError, match="no admissible target is reachable"):
        graph.route("a", ["c"], capability=CAPABILITY)


def test_multi_hop_route_passes_when_every_boundary_authorizes_requested_capability():
    secrets = {"auth-a": "secret-a", "auth-b": "secret-b", "auth-c": "secret-c"}
    rel_ab = relation(
        "rel-ab",
        "a",
        "b",
        "auth-a",
        "auth-b",
        (CAPABILITY,),
        secrets["auth-a"],
        secrets["auth-b"],
    )
    rel_bc = relation(
        "rel-bc",
        "b",
        "c",
        "auth-b",
        "auth-c",
        (CAPABILITY,),
        secrets["auth-b"],
        secrets["auth-c"],
    )
    graph = FederatedCapabilityGraph(
        nodes=[
            CapabilityNode("a", "domain", {"quality": 0.4}),
            CapabilityNode("b", "domain", {"quality": 0.6}),
            CapabilityNode("c", "domain", {"quality": 0.9}),
        ],
        crossings=[
            FederatedCrossing("a", "b", 0.1, "rel-ab", CAPABILITY, CONTRACT),
            FederatedCrossing("b", "c", 0.1, "rel-bc", CAPABILITY, CONTRACT),
        ],
        capability_gate=AdmissibilityGate([]),
        preference=preference(),
        relations=[rel_ab, rel_bc],
        relation_gate=RelationIntegrityGate(secrets, now=lambda: 150),
    )

    result = graph.route("a", ["c"], capability=CAPABILITY)
    assert result.route.path == ("a", "b", "c")
    assert result.traversed_relations == ("rel-ab", "rel-bc")


def test_blocked_relation_evidence_is_immutable_after_receipt_creation():
    secrets = {"auth-a": "secret-a", "auth-b": "secret-b", "auth-c": "secret-c"}
    rel_ab = relation(
        "rel-ab",
        "a",
        "b",
        "auth-a",
        "auth-b",
        (CAPABILITY,),
        secrets["auth-a"],
        secrets["auth-b"],
    )
    unsigned_ac = FederationRelation(
        relation_id="rel-ac",
        source_domain="a",
        target_domain="c",
        source_authority="auth-a",
        target_authority="auth-c",
        contract_hash=CONTRACT,
        capabilities=(CAPABILITY,),
        valid_from=100,
        valid_until=200,
    )
    graph = FederatedCapabilityGraph(
        nodes=[
            CapabilityNode("a", "domain", {"quality": 0.4}),
            CapabilityNode("b", "domain", {"quality": 0.8}),
            CapabilityNode("c", "domain", {"quality": 0.9}),
        ],
        crossings=[
            FederatedCrossing("a", "b", 0.1, "rel-ab", CAPABILITY, CONTRACT),
            FederatedCrossing("a", "c", 0.1, "rel-ac", CAPABILITY, CONTRACT),
        ],
        capability_gate=AdmissibilityGate([]),
        preference=preference(),
        relations=[rel_ab, unsigned_ac],
        relation_gate=RelationIntegrityGate(secrets, now=lambda: 150),
    )

    result = graph.route("a", ["b"], capability=CAPABILITY)
    receipt_before = result.relation_receipt_sha256
    assert any("missing_bilateral_witness" in reasons for reasons in result.blocked_relations.values())
    with pytest.raises(TypeError):
        result.blocked_relations["forged"] = ("forged",)  # type: ignore[index]
    first_reasons = next(iter(result.blocked_relations.values()))
    with pytest.raises(TypeError):
        first_reasons[0] = "forged"  # type: ignore[index]
    assert result.relation_receipt_sha256 == receipt_before


def test_blocked_relation_keys_are_collision_free_for_delimiter_like_identifiers():
    graph = FederatedCapabilityGraph(
        nodes=[
            CapabilityNode("a", "domain", {"quality": 0.4}),
            CapabilityNode("b", "domain", {"quality": 0.6}),
            CapabilityNode("b:c", "domain", {"quality": 0.7}),
        ],
        crossings=[
            FederatedCrossing("a", "b:c", 0.1, "d", CAPABILITY, CONTRACT),
            FederatedCrossing("a", "b", 0.1, "c:d", CAPABILITY, CONTRACT),
        ],
        capability_gate=AdmissibilityGate([]),
        preference=preference(),
        relations=[],
        relation_gate=RelationIntegrityGate({}, now=lambda: 150),
    )

    blocked = graph.blocked_for(CAPABILITY)
    assert len(blocked) == 2
    assert '["a","b:c","d"]' in blocked
    assert '["a","b","c:d"]' in blocked


def test_same_shared_secret_cannot_create_bilateral_witness():
    rel = FederationRelation(
        relation_id="rel-ab",
        source_domain="a",
        target_domain="b",
        source_authority="auth-a",
        target_authority="auth-b",
        contract_hash=CONTRACT,
        capabilities=(CAPABILITY,),
        valid_from=100,
        valid_until=200,
    )
    with pytest.raises(ValueError, match="must be distinct"):
        sign_relation(rel, source_secret="same-secret", target_secret="same-secret")


def test_gate_fails_closed_if_distinct_authorities_are_misconfigured_with_same_secret():
    signed = relation(
        "rel-ab",
        "a",
        "b",
        "auth-a",
        "auth-b",
        (CAPABILITY,),
        "secret-a",
        "secret-b",
    )
    gate = RelationIntegrityGate(
        {"auth-a": "secret-a", "auth-b": "secret-a"},
        now=lambda: 150,
    )
    from app.federation_relation import RelationRequest

    decision = gate.evaluate(
        signed,
        RelationRequest("a", "b", CONTRACT, CAPABILITY),
    )
    assert decision.admissible is False
    assert "shared_authority_secret" in decision.reasons
