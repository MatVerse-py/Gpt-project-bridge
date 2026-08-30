from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import storage
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
from app.federation_relation import FederationRelation, RelationRequest

CONTRACT = "a" * 64
SOURCE_V1_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("31" * 32))
SOURCE_V2_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("32" * 32))
SOURCE_ALT_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("33" * 32))
TARGET_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("41" * 32))
SOURCE_V1_PUBLIC = ed25519_public_key_hex(SOURCE_V1_PRIVATE)
SOURCE_V2_PUBLIC = ed25519_public_key_hex(SOURCE_V2_PRIVATE)
SOURCE_ALT_PUBLIC = ed25519_public_key_hex(SOURCE_ALT_PRIVATE)
TARGET_PUBLIC = ed25519_public_key_hex(TARGET_PRIVATE)
SOURCE_V1_ID = authority_key_id(SOURCE_V1_PUBLIC)
SOURCE_V2_ID = authority_key_id(SOURCE_V2_PUBLIC)
SOURCE_ALT_ID = authority_key_id(SOURCE_ALT_PUBLIC)
TARGET_ID = authority_key_id(TARGET_PUBLIC)


def key_record(
    authority_id: str,
    public_key_hex: str,
    valid_from: int,
    valid_until: int,
    *,
    previous_key_id: str | None = None,
) -> AuthorityKeyRecord:
    return AuthorityKeyRecord(
        authority_id=authority_id,
        key_id=authority_key_id(public_key_hex),
        public_key_hex=public_key_hex,
        valid_from=valid_from,
        valid_until=valid_until,
        previous_key_id=previous_key_id,
    )


def relation(
    relation_id: str,
    *,
    valid_from: int = 120,
    valid_until: int = 180,
    source_private: Ed25519PrivateKey = SOURCE_V1_PRIVATE,
) -> FederationRelation:
    unsigned = FederationRelation(
        relation_id=relation_id,
        source_domain="domain-a",
        target_domain="domain-b",
        source_authority="authority-a",
        target_authority="authority-b",
        contract_hash=CONTRACT,
        capabilities=("state.transfer",),
        valid_from=valid_from,
        valid_until=valid_until,
        witness_scheme=ED25519_PUBLIC_KEY_SCHEME,
    )
    return sign_relation_ed25519_target(
        sign_relation_ed25519_source(unsigned, private_key=source_private),
        private_key=TARGET_PRIVATE,
    )


def request() -> RelationRequest:
    return RelationRequest("domain-a", "domain-b", CONTRACT, "state.transfer")


def register_genesis(registry: FederationAuthorityKeyRegistry, *, source_until: int = 300):
    source = registry.register_key(
        key_record("authority-a", SOURCE_V1_PUBLIC, 100, source_until),
        actor_id="admin",
    )
    target = registry.register_key(
        key_record("authority-b", TARGET_PUBLIC, 100, 300),
        actor_id="admin",
    )
    return source, target


def test_registry_binds_relation_and_governed_gate_verifies_public_keys_only():
    registry = FederationAuthorityKeyRegistry()
    register_genesis(registry)
    signed = relation("rel-governed-v1")
    result = registry.register_relation_binding(
        signed,
        source_key_id=SOURCE_V1_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )

    decision = GovernedEd25519RelationIntegrityGate(
        registry, now=lambda: 150
    ).evaluate(signed, request())

    assert decision.admissible is True
    assert decision.reasons == ()
    assert result["binding"].relation_sha256 == signed.payload_sha256()
    assert result["binding_sha256"] == result["binding"].binding_sha256()


def test_key_registration_and_binding_are_ledgered_in_one_chain():
    registry = FederationAuthorityKeyRegistry()
    register_genesis(registry)
    signed = relation("rel-ledgered-binding-v1")
    registry.register_relation_binding(
        signed,
        source_key_id=SOURCE_V1_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )

    rows = storage.read_ledger()
    event_types = [storage.json.loads(row["event_json"])["event_type"] for row in rows]
    assert event_types == [
        "FEDERATION_AUTHORITY_KEY_REGISTERED",
        "FEDERATION_AUTHORITY_KEY_REGISTERED",
        "FEDERATION_RELATION_KEY_BOUND",
    ]
    assert storage.verify_chain()["ok"] is True


def test_relation_payload_tampering_breaks_immutable_key_binding():
    registry = FederationAuthorityKeyRegistry()
    register_genesis(registry)
    signed = relation("rel-binding-tamper-v1")
    registry.register_relation_binding(
        signed,
        source_key_id=SOURCE_V1_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )
    tampered = replace(signed, evidence_policy="tampered")

    decision = GovernedEd25519RelationIntegrityGate(
        registry, now=lambda: 150
    ).evaluate(tampered, request())

    assert decision.admissible is False
    assert "binding_relation_hash_mismatch" in decision.reasons
    assert "witness_payload_hash_mismatch" in decision.reasons


def test_binding_cannot_be_reassigned_to_different_keys():
    registry = FederationAuthorityKeyRegistry()
    register_genesis(registry)
    signed = relation("rel-no-rebind-v1")
    registry.register_relation_binding(
        signed,
        source_key_id=SOURCE_V1_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )
    registry.register_key(
        key_record(
            "authority-a",
            SOURCE_V2_PUBLIC,
            300,
            400,
            previous_key_id=SOURCE_V1_ID,
        ),
        actor_id="admin",
    )

    with pytest.raises(ValueError, match="already bound"):
        registry.register_relation_binding(
            signed,
            source_key_id=SOURCE_V2_ID,
            target_key_id=TARGET_ID,
            actor_id="admin",
        )


def test_wrong_authority_key_cannot_be_bound_to_relation():
    registry = FederationAuthorityKeyRegistry()
    register_genesis(registry)
    signed = relation("rel-wrong-authority-v1")

    with pytest.raises(PermissionError, match="source key authority"):
        registry.register_relation_binding(
            signed,
            source_key_id=TARGET_ID,
            target_key_id=SOURCE_V1_ID,
            actor_id="admin",
        )


def test_revocation_is_temporal_and_blocks_reentry_from_effective_time():
    registry = FederationAuthorityKeyRegistry()
    register_genesis(registry)
    signed = relation("rel-revocation-v1", valid_until=220)
    registry.register_relation_binding(
        signed,
        source_key_id=SOURCE_V1_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )
    registry.revoke_key(
        "authority-a",
        SOURCE_V1_ID,
        effective_at=170,
        reason="compromise-detected",
        actor_id="admin",
    )

    before = GovernedEd25519RelationIntegrityGate(
        registry, now=lambda: 160
    ).evaluate(signed, request())
    after = GovernedEd25519RelationIntegrityGate(
        registry, now=lambda: 180
    ).evaluate(signed, request())

    assert before.admissible is True
    assert after.admissible is False
    assert "source_key_revoked" in after.reasons


def test_rotation_preserves_historical_key_binding_without_mutating_relation_v1():
    registry = FederationAuthorityKeyRegistry()
    registry.register_key(
        key_record("authority-a", SOURCE_V1_PUBLIC, 100, 200), actor_id="admin"
    )
    registry.register_key(
        key_record(
            "authority-a",
            SOURCE_V2_PUBLIC,
            200,
            300,
            previous_key_id=SOURCE_V1_ID,
        ),
        actor_id="admin",
    )
    registry.register_key(
        key_record("authority-b", TARGET_PUBLIC, 100, 300), actor_id="admin"
    )

    old_relation = relation("rel-before-rotation-v1", valid_from=120, valid_until=180)
    new_relation = relation(
        "rel-after-rotation-v1",
        valid_from=220,
        valid_until=280,
        source_private=SOURCE_V2_PRIVATE,
    )
    old_payload = old_relation.canonical_payload()
    new_payload = new_relation.canonical_payload()

    registry.register_relation_binding(
        old_relation,
        source_key_id=SOURCE_V1_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )
    registry.register_relation_binding(
        new_relation,
        source_key_id=SOURCE_V2_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )

    assert GovernedEd25519RelationIntegrityGate(
        registry, now=lambda: 150
    ).evaluate(old_relation, request()).admissible is True
    assert GovernedEd25519RelationIntegrityGate(
        registry, now=lambda: 250
    ).evaluate(new_relation, request()).admissible is True
    assert old_relation.canonical_payload() == old_payload
    assert new_relation.canonical_payload() == new_payload
    assert "source_key_id" not in old_payload
    assert "target_key_id" not in old_payload


def test_rotation_chain_rejects_forks_and_unlinked_successors():
    registry = FederationAuthorityKeyRegistry()
    registry.register_key(
        key_record("authority-a", SOURCE_V1_PUBLIC, 100, 200), actor_id="admin"
    )
    registry.register_key(
        key_record(
            "authority-a",
            SOURCE_V2_PUBLIC,
            200,
            300,
            previous_key_id=SOURCE_V1_ID,
        ),
        actor_id="admin",
    )

    with pytest.raises(ValueError, match="requires previous_key_id"):
        registry.register_key(
            key_record("authority-a", SOURCE_ALT_PUBLIC, 300, 400), actor_id="admin"
        )

    with pytest.raises(ValueError, match="rotation successor"):
        registry.register_key(
            key_record(
                "authority-a",
                SOURCE_ALT_PUBLIC,
                200,
                350,
                previous_key_id=SOURCE_V1_ID,
            ),
            actor_id="admin",
        )


def test_relation_binding_requires_key_validity_to_cover_full_relation_window():
    registry = FederationAuthorityKeyRegistry()
    registry.register_key(
        key_record("authority-a", SOURCE_V1_PUBLIC, 100, 160), actor_id="admin"
    )
    registry.register_key(
        key_record("authority-b", TARGET_PUBLIC, 100, 300), actor_id="admin"
    )
    signed = relation("rel-window-too-wide-v1", valid_from=120, valid_until=180)

    with pytest.raises(PermissionError, match="source key validity"):
        registry.register_relation_binding(
            signed,
            source_key_id=SOURCE_V1_ID,
            target_key_id=TARGET_ID,
            actor_id="admin",
        )


def test_public_key_identity_is_deterministic_and_cannot_cross_authorities():
    registry = FederationAuthorityKeyRegistry()
    first = key_record("authority-a", SOURCE_V1_PUBLIC, 100, 200)
    assert first.key_id == authority_key_id(SOURCE_V1_PUBLIC)
    registry.register_key(first, actor_id="admin")

    with pytest.raises(ValueError, match="collision|mismatch"):
        registry.register_key(
            key_record("authority-b", SOURCE_V1_PUBLIC, 100, 200), actor_id="admin"
        )


def test_registration_binding_and_revocation_are_idempotent_only_for_identical_state():
    registry = FederationAuthorityKeyRegistry()
    source = key_record("authority-a", SOURCE_V1_PUBLIC, 100, 300)
    target = key_record("authority-b", TARGET_PUBLIC, 100, 300)
    assert registry.register_key(source, actor_id="admin")["idempotent"] is False
    assert registry.register_key(source, actor_id="admin")["idempotent"] is True
    registry.register_key(target, actor_id="admin")
    signed = relation("rel-idempotent-v1")
    assert registry.register_relation_binding(
        signed,
        source_key_id=SOURCE_V1_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )["idempotent"] is False
    assert registry.register_relation_binding(
        signed,
        source_key_id=SOURCE_V1_ID,
        target_key_id=TARGET_ID,
        actor_id="admin",
    )["idempotent"] is True
    assert registry.revoke_key(
        "authority-a",
        SOURCE_V1_ID,
        effective_at=250,
        reason="planned-retirement",
        actor_id="admin",
    )["idempotent"] is False
    assert registry.revoke_key(
        "authority-a",
        SOURCE_V1_ID,
        effective_at=250,
        reason="planned-retirement",
        actor_id="admin",
    )["idempotent"] is True

    with pytest.raises(ValueError, match="different revocation state"):
        registry.revoke_key(
            "authority-a",
            SOURCE_V1_ID,
            effective_at=240,
            reason="different-reason",
            actor_id="admin",
        )
