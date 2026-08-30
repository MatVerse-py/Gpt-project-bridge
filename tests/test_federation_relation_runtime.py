import pytest

from app.federation_relation import (
    FederatedCapabilityGraph,
    FederatedCrossing,
    FederationRelation,
    RelationIntegrityGate,
    RelationStatus,
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
SOURCE_SECRET = "source-secret-for-tests"
TARGET_SECRET = "target-secret-for-tests"


def make_relation(status: RelationStatus = RelationStatus.ACTIVE):
    return sign_relation(
        FederationRelation(
            relation_id="rel-a-b-runtime",
            source_domain="domain-a",
            target_domain="domain-b",
            source_authority="authority-a",
            target_authority="authority-b",
            contract_hash=CONTRACT,
            capabilities=("state.transfer",),
            valid_from=100,
            valid_until=200,
            status=status,
        ),
        source_secret=SOURCE_SECRET,
        target_secret=TARGET_SECRET,
    )


def build_graph(relations, gate):
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
                "rel-a-b-runtime",
                "state.transfer",
                CONTRACT,
            )
        ],
        capability_gate=AdmissibilityGate([]),
        preference=preference,
        relations=relations,
        relation_gate=gate,
    )


def test_relation_expiry_after_graph_creation_is_enforced_at_route_time():
    clock = {"now": 150}
    gate = RelationIntegrityGate(
        {"authority-a": SOURCE_SECRET, "authority-b": TARGET_SECRET},
        now=lambda: clock["now"],
    )
    graph = build_graph([make_relation()], gate)

    assert graph.route("domain-a", ["domain-b"]).route.path == ("domain-a", "domain-b")

    clock["now"] = 250
    assert any("relation_expired" in reasons for reasons in graph.blocked.values())
    with pytest.raises(ValueError, match="no admissible target is reachable"):
        graph.route("domain-a", ["domain-b"])


def test_relation_registry_revocation_is_visible_without_rebuilding_graph():
    clock = {"now": 150}
    registry = [make_relation()]
    gate = RelationIntegrityGate(
        {"authority-a": SOURCE_SECRET, "authority-b": TARGET_SECRET},
        now=lambda: clock["now"],
    )
    graph = build_graph(lambda: tuple(registry), gate)

    assert graph.route("domain-a", ["domain-b"]).route.path == ("domain-a", "domain-b")

    registry[0] = make_relation(RelationStatus.REVOKED)
    assert any("status:REVOKED" in reasons for reasons in graph.blocked.values())
    with pytest.raises(ValueError, match="no admissible target is reachable"):
        graph.route("domain-a", ["domain-b"])
