from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.federation_ed25519 import (
    ED25519_PUBLIC_KEY_SCHEME,
    ed25519_public_key_hex,
    sign_relation_ed25519_source,
    sign_relation_ed25519_target,
)
from app.federation_key_registry import (
    AuthorityKeyRecord,
    FederationAuthorityKeyRegistry,
    GovernedEd25519RelationIntegrityGate,
    authority_key_id,
)
from app.federation_relation import (
    FederatedCapabilityGraph,
    FederatedCrossing,
    FederationRelation,
)
from app.federation_routing import (
    AdmissibilityGate,
    CapabilityNode,
    Criterion,
    Direction,
    PreferenceModel,
)

CONTRACT = "b" * 64
SOURCE_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("51" * 32))
TARGET_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("61" * 32))
SOURCE_PUBLIC = ed25519_public_key_hex(SOURCE_PRIVATE)
TARGET_PUBLIC = ed25519_public_key_hex(TARGET_PRIVATE)
SOURCE_KEY_ID = authority_key_id(SOURCE_PUBLIC)
TARGET_KEY_ID = authority_key_id(TARGET_PUBLIC)


def signed_relation() -> FederationRelation:
    unsigned = FederationRelation(
        relation_id="rel-governed-routing-v1",
        source_domain="domain-a",
        target_domain="domain-b",
        source_authority="authority-a",
        target_authority="authority-b",
        contract_hash=CONTRACT,
        capabilities=("state.transfer",),
        valid_from=100,
        valid_until=220,
        witness_scheme=ED25519_PUBLIC_KEY_SCHEME,
    )
    return sign_relation_ed25519_target(
        sign_relation_ed25519_source(unsigned, private_key=SOURCE_PRIVATE),
        private_key=TARGET_PRIVATE,
    )


def populated_registry(relation: FederationRelation) -> FederationAuthorityKeyRegistry:
    registry = FederationAuthorityKeyRegistry()
    registry.register_key(
        AuthorityKeyRecord(
            authority_id="authority-a",
            key_id=SOURCE_KEY_ID,
            public_key_hex=SOURCE_PUBLIC,
            valid_from=100,
            valid_until=300,
        ),
        actor_id="admin",
    )
    registry.register_key(
        AuthorityKeyRecord(
            authority_id="authority-b",
            key_id=TARGET_KEY_ID,
            public_key_hex=TARGET_PUBLIC,
            valid_from=100,
            valid_until=300,
        ),
        actor_id="admin",
    )
    registry.register_relation_binding(
        relation,
        source_key_id=SOURCE_KEY_ID,
        target_key_id=TARGET_KEY_ID,
        actor_id="admin",
    )
    return registry


def graph_for(
    relation: FederationRelation,
    registry: FederationAuthorityKeyRegistry,
    *,
    now: int,
) -> FederatedCapabilityGraph:
    return FederatedCapabilityGraph(
        nodes=[
            CapabilityNode("domain-a", "domain", {"quality": 0.5}),
            CapabilityNode("domain-b", "domain", {"quality": 0.9}),
        ],
        crossings=[
            FederatedCrossing(
                "domain-a",
                "domain-b",
                0.1,
                relation.relation_id,
                "state.transfer",
                CONTRACT,
            )
        ],
        capability_gate=AdmissibilityGate([]),
        preference=PreferenceModel(
            {"quality": Criterion("quality", Direction.HIGHER_IS_BETTER, 0.0, 1.0)},
            {"quality": 1.0},
        ),
        relations=[relation],
        relation_gate=GovernedEd25519RelationIntegrityGate(
            registry,
            now=lambda: now,
        ),
    )


def test_governed_ed25519_binding_is_required_and_sufficient_for_routing():
    relation = signed_relation()
    registry = populated_registry(relation)

    result = graph_for(relation, registry, now=150).route(
        "domain-a",
        ["domain-b"],
        capability="state.transfer",
    )

    assert result.route.path == ("domain-a", "domain-b")
    assert result.traversed_relations == (relation.relation_id,)
    assert result.blocked_relations == {}


def test_revocation_removes_previously_routable_edge_at_effective_time():
    relation = signed_relation()
    registry = populated_registry(relation)

    before = graph_for(relation, registry, now=160)
    assert before.route(
        "domain-a",
        ["domain-b"],
        capability="state.transfer",
    ).route.path == ("domain-a", "domain-b")

    registry.revoke_key(
        "authority-a",
        SOURCE_KEY_ID,
        effective_at=170,
        reason="compromise-detected",
        actor_id="admin",
    )

    after = graph_for(relation, registry, now=180)
    blocked = after.blocked_for("state.transfer")
    assert any("source_key_revoked" in reasons for reasons in blocked.values())
    assert all(
        relation.relation_id not in key or "source_key_revoked" in reasons
        for key, reasons in blocked.items()
    )
