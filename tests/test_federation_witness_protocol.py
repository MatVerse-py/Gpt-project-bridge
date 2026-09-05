from dataclasses import replace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.federation_ed25519 import (
    ED25519_PUBLIC_KEY_SCHEME,
    sign_relation_ed25519_source,
)
from app.federation_relation import (
    FederationRelation,
    RelationIntegrityGate,
    RelationRequest,
)

CONTRACT = "a" * 64


def test_federation_relation_accepts_structural_ed25519_witness_shape():
    relation = FederationRelation(
        relation_id="rel-structural-ed25519",
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
    signed = sign_relation_ed25519_source(
        relation,
        private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex("33" * 32)),
    )
    assert signed.witness is not None
    assert signed.witness.scheme == ED25519_PUBLIC_KEY_SCHEME
    assert signed.witness.payload_sha256 == signed.payload_sha256()


def test_hmac_gate_rejects_foreign_witness_shape_without_attribute_error():
    relation = FederationRelation(
        relation_id="rel-hmac-foreign-witness",
        source_domain="domain-a",
        target_domain="domain-b",
        source_authority="authority-a",
        target_authority="authority-b",
        contract_hash=CONTRACT,
        capabilities=("state.transfer",),
        valid_from=100,
        valid_until=200,
    )
    ed25519_shaped = sign_relation_ed25519_source(
        replace(relation, witness_scheme=ED25519_PUBLIC_KEY_SCHEME),
        private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex("44" * 32)),
    ).witness
    malformed_hmac_relation = replace(relation, witness=ed25519_shaped)

    decision = RelationIntegrityGate(
        {"authority-a": "source-secret", "authority-b": "target-secret"},
        now=lambda: 150,
    ).evaluate(
        malformed_hmac_relation,
        RelationRequest("domain-a", "domain-b", CONTRACT, "state.transfer"),
    )

    assert decision.admissible is False
    assert decision.reasons == ("witness_type_mismatch",)
