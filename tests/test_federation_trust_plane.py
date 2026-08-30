from __future__ import annotations

import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import storage
from app.federation_ed25519 import (
    ED25519_PUBLIC_KEY_SCHEME,
    ed25519_public_key_hex,
    sign_relation_ed25519_source,
    sign_relation_ed25519_target,
)
from app.federation_key_registry import authority_key_id
from app.federation_relation import FederationRelation
from conftest import auth_request

CONTRACT = "a" * 64
SOURCE_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("31" * 32))
TARGET_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("42" * 32))
SOURCE_PUBLIC = ed25519_public_key_hex(SOURCE_PRIVATE)
TARGET_PUBLIC = ed25519_public_key_hex(TARGET_PRIVATE)
SOURCE_KEY_ID = authority_key_id(SOURCE_PUBLIC)
TARGET_KEY_ID = authority_key_id(TARGET_PUBLIC)


def _events() -> list[dict[str, object]]:
    return [json.loads(row["event_json"]) for row in storage.read_ledger()]


def _register(client, authority_id: str, public_key_hex: str, *, valid_from: int = 100, valid_until: int = 200):
    return auth_request(
        client,
        "admin",
        "POST",
        f"/trust/federation/authorities/{authority_id}/keys",
        {
            "public_key_hex": public_key_hex,
            "valid_from": valid_from,
            "valid_until": valid_until,
        },
    )


def test_genesis_key_actor_is_authenticated_principal(client):
    response = _register(client, "authority-a", SOURCE_PUBLIC)
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated_actor"] == "admin"
    assert body["record"]["authority_id"] == "authority-a"
    assert body["record"]["key_id"] == SOURCE_KEY_ID

    event = _events()[-1]
    assert event["event_type"] == "FEDERATION_AUTHORITY_KEY_REGISTERED"
    assert event["registered_by"] == "admin"
    assert "actor_id" not in event


def test_forged_actor_fields_are_rejected_before_mutation(client):
    response = auth_request(
        client,
        "admin",
        "POST",
        "/trust/federation/authorities/authority-a/keys",
        {
            "public_key_hex": SOURCE_PUBLIC,
            "valid_from": 100,
            "valid_until": 200,
            "actor_id": "forged-actor",
        },
    )
    assert response.status_code == 422
    assert storage.read_ledger() == []


def test_missing_capability_cannot_mutate_registry(client):
    response = auth_request(
        client,
        "gpt",
        "POST",
        "/trust/federation/authorities/authority-a/keys",
        {
            "public_key_hex": SOURCE_PUBLIC,
            "valid_from": 100,
            "valid_until": 200,
        },
    )
    assert response.status_code == 403
    assert storage.read_ledger() == []


def test_invalid_signature_cannot_mutate_registry(client):
    response = auth_request(
        client,
        "admin",
        "POST",
        "/trust/federation/authorities/authority-a/keys",
        {
            "public_key_hex": SOURCE_PUBLIC,
            "valid_from": 100,
            "valid_until": 200,
        },
        signature_override="0" * 64,
    )
    assert response.status_code == 401
    assert storage.read_ledger() == []


def test_nonce_replay_cannot_repeat_authenticated_mutation(client):
    nonce = "0123456789abcdef0123456789abcdef"
    first = auth_request(
        client,
        "admin",
        "POST",
        "/trust/federation/authorities/authority-a/keys",
        {
            "public_key_hex": SOURCE_PUBLIC,
            "valid_from": 100,
            "valid_until": 200,
        },
        nonce=nonce,
    )
    assert first.status_code == 200

    second = auth_request(
        client,
        "admin",
        "POST",
        "/trust/federation/authorities/authority-a/keys",
        {
            "public_key_hex": SOURCE_PUBLIC,
            "valid_from": 100,
            "valid_until": 200,
        },
        nonce=nonce,
    )
    assert second.status_code == 409
    assert [event["event_type"] for event in _events()] == ["FEDERATION_AUTHORITY_KEY_REGISTERED"]


def test_rotation_derives_lineage_and_revocation_records_authenticated_actor(client):
    first = _register(client, "authority-a", SOURCE_PUBLIC, valid_from=100, valid_until=200)
    assert first.status_code == 200

    rotated_private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("51" * 32))
    rotated_public = ed25519_public_key_hex(rotated_private)
    rotated_key_id = authority_key_id(rotated_public)
    rotate = auth_request(
        client,
        "admin",
        "POST",
        f"/trust/federation/authorities/authority-a/keys/{SOURCE_KEY_ID}/rotate",
        {"public_key_hex": rotated_public, "valid_until": 300},
    )
    assert rotate.status_code == 200
    rotated = rotate.json()["record"]
    assert rotated["key_id"] == rotated_key_id
    assert rotated["previous_key_id"] == SOURCE_KEY_ID
    assert rotated["valid_from"] == 200
    assert rotated["valid_until"] == 300
    assert rotate.json()["authenticated_actor"] == "admin"

    revoke = auth_request(
        client,
        "admin",
        "POST",
        f"/trust/federation/authorities/authority-a/keys/{rotated_key_id}/revoke",
        {"effective_at": 250, "reason": "operator compromise"},
    )
    assert revoke.status_code == 200
    assert revoke.json()["record"]["revoked_at"] == 250
    assert revoke.json()["authenticated_actor"] == "admin"

    events = _events()
    assert events[-2]["event_type"] == "FEDERATION_AUTHORITY_KEY_REGISTERED"
    assert events[-2]["registered_by"] == "admin"
    assert events[-1]["event_type"] == "FEDERATION_AUTHORITY_KEY_REVOKED"
    assert events[-1]["revoked_by"] == "admin"


def test_relation_binding_is_authenticated_and_ledgered(client):
    assert _register(client, "authority-a", SOURCE_PUBLIC).status_code == 200
    assert _register(client, "authority-b", TARGET_PUBLIC).status_code == 200

    relation = FederationRelation(
        relation_id="rel-a-b-authenticated-v1",
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
    signed = sign_relation_ed25519_target(
        sign_relation_ed25519_source(relation, private_key=SOURCE_PRIVATE),
        private_key=TARGET_PRIVATE,
    )
    witness = signed.witness
    assert witness is not None

    payload = {
        "source_domain": signed.source_domain,
        "target_domain": signed.target_domain,
        "source_authority": signed.source_authority,
        "target_authority": signed.target_authority,
        "contract_hash": signed.contract_hash,
        "capabilities": list(signed.capabilities),
        "valid_from": signed.valid_from,
        "valid_until": signed.valid_until,
        "evidence_policy": signed.evidence_policy,
        "witness_payload_sha256": witness.payload_sha256,
        "source_signature_hex": witness.source_signature_hex,
        "target_signature_hex": witness.target_signature_hex,
        "source_key_id": SOURCE_KEY_ID,
        "target_key_id": TARGET_KEY_ID,
    }
    response = auth_request(
        client,
        "admin",
        "POST",
        "/trust/federation/relations/rel-a-b-authenticated-v1/key-binding",
        payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated_actor"] == "admin"
    assert body["binding"]["relation_id"] == "rel-a-b-authenticated-v1"
    assert body["binding"]["source_key_id"] == SOURCE_KEY_ID
    assert body["binding"]["target_key_id"] == TARGET_KEY_ID

    event = _events()[-1]
    assert event["event_type"] == "FEDERATION_RELATION_KEY_BOUND"
    assert event["bound_by"] == "admin"

    readback = auth_request(
        client,
        "admin",
        "GET",
        "/trust/federation/relations/rel-a-b-authenticated-v1/key-binding",
    )
    assert readback.status_code == 200
    assert readback.json()["binding"] == body["binding"]
