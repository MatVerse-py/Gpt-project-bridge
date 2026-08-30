from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.federation_ed25519 import (
    ED25519_PUBLIC_KEY_SCHEME,
    Ed25519RelationIntegrityGate,
    Ed25519RelationWitness,
    HybridRelationIntegrityGate,
    ed25519_public_key_hex,
    sign_relation_ed25519_source,
    sign_relation_ed25519_target,
)
from app.federation_relation import (
    FederatedCapabilityGraph,
    FederatedCrossing,
    FederationRelation,
    RelationRequest,
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
SOURCE_SECRET = "legacy-source-secret"
TARGET_SECRET = "legacy-target-secret"
SOURCE_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("11" * 32))
TARGET_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("22" * 32))
SOURCE_PUBLIC = ed25519_public_key_hex(SOURCE_PRIVATE)
TARGET_PUBLIC = ed25519_public_key_hex(TARGET_PRIVATE)


def ed_relation() -> FederationRelation:
    return FederationRelation(
        relation_id="rel-a-b-ed25519-v1",
        source_domain="domain-a",
        target_domain="domain-b",
        source_authority="authority-a",
        target_authority="authority-b",
        contract_hash=CONTRACT,
        capabilities=("state.transfer",),
        valid_from=100,
        valid_until=200,
        witness_scheme=ED25519_PUBLIC_KEY_SCHEME,
    )


def signed_ed_relation() -> FederationRelation:
    source_signed = sign_relation_ed25519_source(
        ed_relation(),
        private_key=SOURCE_PRIVATE,
    )
    return sign_relation_ed25519_target(
        source_signed,
        private_key=TARGET_PRIVATE,
    )


def ed_gate() -> Ed25519RelationIntegrityGate:
    return Ed25519RelationIntegrityGate(
        {
            "authority-a": SOURCE_PUBLIC,
            "authority-b": TARGET_PUBLIC,
        },
        now=lambda: 150,
    )


def request() -> RelationRequest:
    return RelationRequest(
        "domain-a",
        "domain-b",
        CONTRACT,
        "state.transfer",
    )


def test_bilateral_ed25519_witness_verifies_with_public_keys_only():
    gate = ed_gate()
    decision = gate.evaluate(signed_ed_relation(), request())
    assert decision.admissible is True
    assert decision.reasons == ()
    assert set(gate.__dict__) == {"_authority_public_keys", "_now"}


def test_partial_signature_remains_fail_closed():
    source_signed = sign_relation_ed25519_source(
        ed_relation(),
        private_key=SOURCE_PRIVATE,
    )
    decision = ed_gate().evaluate(source_signed, request())
    assert decision.admissible is False
    assert decision.reasons == ("missing_target_witness",)


def test_authorities_can_sign_in_target_first_order():
    target_signed = sign_relation_ed25519_target(
        ed_relation(),
        private_key=TARGET_PRIVATE,
    )
    fully_signed = sign_relation_ed25519_source(
        target_signed,
        private_key=SOURCE_PRIVATE,
    )
    assert ed_gate().evaluate(fully_signed, request()).admissible is True


def test_payload_tampering_invalidates_both_signatures():
    tampered = replace(signed_ed_relation(), evidence_policy="tampered-policy")
    decision = ed_gate().evaluate(tampered, request())
    assert decision.admissible is False
    assert "witness_payload_hash_mismatch" in decision.reasons
    assert "invalid_source_witness" in decision.reasons
    assert "invalid_target_witness" in decision.reasons


def test_swapped_registry_keys_cannot_authorize_relation():
    gate = Ed25519RelationIntegrityGate(
        {
            "authority-a": TARGET_PUBLIC,
            "authority-b": SOURCE_PUBLIC,
        },
        now=lambda: 150,
    )
    decision = gate.evaluate(signed_ed_relation(), request())
    assert decision.admissible is False
    assert "invalid_source_witness" in decision.reasons
    assert "invalid_target_witness" in decision.reasons


def test_source_and_target_signatures_are_role_bound():
    relation = signed_ed_relation()
    assert isinstance(relation.witness, Ed25519RelationWitness)
    swapped = replace(
        relation,
        witness=replace(
            relation.witness,
            source_signature_hex=relation.witness.target_signature_hex,
            target_signature_hex=relation.witness.source_signature_hex,
        ),
    )
    decision = ed_gate().evaluate(swapped, request())
    assert decision.admissible is False
    assert "invalid_source_witness" in decision.reasons
    assert "invalid_target_witness" in decision.reasons


def test_distinct_authorities_cannot_share_one_public_key():
    same_key_relation = sign_relation_ed25519_target(
        sign_relation_ed25519_source(
            ed_relation(),
            private_key=SOURCE_PRIVATE,
        ),
        private_key=SOURCE_PRIVATE,
    )
    gate = Ed25519RelationIntegrityGate(
        {
            "authority-a": SOURCE_PUBLIC,
            "authority-b": SOURCE_PUBLIC,
        },
        now=lambda: 150,
    )
    decision = gate.evaluate(same_key_relation, request())
    assert decision.admissible is False
    assert "shared_authority_public_key" in decision.reasons


def test_unknown_target_public_key_fails_closed():
    gate = Ed25519RelationIntegrityGate(
        {"authority-a": SOURCE_PUBLIC},
        now=lambda: 150,
    )
    decision = gate.evaluate(signed_ed_relation(), request())
    assert decision.admissible is False
    assert "unknown_target_authority" in decision.reasons


def test_duplicate_authority_signature_is_rejected():
    source_signed = sign_relation_ed25519_source(
        ed_relation(),
        private_key=SOURCE_PRIVATE,
    )
    with pytest.raises(ValueError, match="already signed"):
        sign_relation_ed25519_source(
            source_signed,
            private_key=SOURCE_PRIVATE,
        )


def test_hybrid_gate_preserves_legacy_hmac_verification():
    relation = sign_relation(
        FederationRelation(
            relation_id="rel-a-b-hmac-v1",
            source_domain="domain-a",
            target_domain="domain-b",
            source_authority="authority-a",
            target_authority="authority-b",
            contract_hash=CONTRACT,
            capabilities=("state.transfer",),
            valid_from=100,
            valid_until=200,
        ),
        source_secret=SOURCE_SECRET,
        target_secret=TARGET_SECRET,
    )
    gate = HybridRelationIntegrityGate(
        authority_secrets={
            "authority-a": SOURCE_SECRET,
            "authority-b": TARGET_SECRET,
        },
        authority_public_keys={
            "authority-a": SOURCE_PUBLIC,
            "authority-b": TARGET_PUBLIC,
        },
        now=lambda: 150,
    )
    assert gate.evaluate(relation, request()).admissible is True
    assert gate.evaluate(signed_ed_relation(), request()).admissible is True


def test_hybrid_gate_rejects_unknown_witness_scheme_without_cross_scheme_fallback():
    relation = replace(ed_relation(), witness_scheme="UNKNOWN-SCHEME")
    gate = HybridRelationIntegrityGate(now=lambda: 150)
    decision = gate.evaluate(relation, request())
    assert decision.admissible is False
    assert "unsupported_witness_scheme" in decision.reasons
    assert "missing_bilateral_witness" in decision.reasons


def test_ed25519_relation_routes_through_existing_federated_graph():
    nodes = [
        CapabilityNode("domain-a", "domain", {"quality": 0.5}),
        CapabilityNode("domain-b", "domain", {"quality": 0.9}),
    ]
    preference = PreferenceModel(
        {"quality": Criterion("quality", Direction.HIGHER_IS_BETTER, 0.0, 1.0)},
        {"quality": 1.0},
    )
    graph = FederatedCapabilityGraph(
        nodes=nodes,
        crossings=[
            FederatedCrossing(
                "domain-a",
                "domain-b",
                0.1,
                "rel-a-b-ed25519-v1",
                "state.transfer",
                CONTRACT,
            )
        ],
        capability_gate=AdmissibilityGate([]),
        preference=preference,
        relations=[signed_ed_relation()],
        relation_gate=HybridRelationIntegrityGate(
            authority_public_keys={
                "authority-a": SOURCE_PUBLIC,
                "authority-b": TARGET_PUBLIC,
            },
            now=lambda: 150,
        ),
    )
    result = graph.route(
        "domain-a",
        ["domain-b"],
        capability="state.transfer",
    )
    assert result.route.path == ("domain-a", "domain-b")
    assert result.traversed_relations == ("rel-a-b-ed25519-v1",)
