from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.marxiv_human_authority import (
    HumanAuthorityError,
    build_evidence_pack,
    describe_challenge,
    initialize_authority,
    required_human_confirmation,
    sign_challenge,
    verify_signed_approval,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _challenge() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "schema": "marxiv.approval-challenge.v1",
        "publication_id": "matverse-2.0-v1-arxiv",
        "package_hash": "4ef1c650ccf52054cb77adc5d1a1e8d5a19785bcdbe23a644470ee707e97b2aa",
        "object_hash": "a" * 64,
        "manifest_hash": "b" * 64,
        "manuscript_sha256": "c" * 64,
        "arxiv_subfile_sha256": "d" * 64,
        "review_packet_hash": "e" * 64,
        "destination": "arxiv",
        "issued_at": _iso(now - timedelta(seconds=1)),
        "expires_at": _iso(now + timedelta(minutes=10)),
        "nonce": "0123456789abcdef0123456789abcdef",
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _authority(tmp_path: Path):
    private_key = tmp_path / "private.pem"
    registry = tmp_path / "authority.json"
    authority = initialize_authority(
        authority_id="author-human-authority",
        private_key_path=private_key,
        public_registry_path=registry,
    )
    return private_key, registry, authority


def test_initialize_authority_rejects_colliding_paths_without_writing(tmp_path: Path) -> None:
    colliding_path = tmp_path / "authority-material.pem"

    with pytest.raises(HumanAuthorityError, match="must use different paths"):
        initialize_authority(
            authority_id="author-human-authority",
            private_key_path=colliding_path,
            public_registry_path=colliding_path,
        )

    assert colliding_path.exists() is False


def test_confirmation_binds_package_and_fresh_challenge_hash(tmp_path: Path) -> None:
    challenge_path = tmp_path / "challenge.json"
    _write(challenge_path, _challenge())

    summary = describe_challenge(challenge_path)

    assert summary["package_hash"].startswith("4ef1c650ccf5")
    assert summary["challenge_hash"]
    assert summary["required_human_confirmation"].startswith(
        "APPROVE_PACKAGE matverse-2.0-v1-arxiv 4ef1c650ccf5 "
    )
    assert summary["required_human_confirmation"].endswith(summary["challenge_hash"][:12])
    assert summary["external_submission_authorized"] is False


def test_ed25519_approval_requires_exact_fresh_human_confirmation(tmp_path: Path) -> None:
    challenge_path = tmp_path / "challenge.json"
    challenge = _challenge()
    _write(challenge_path, challenge)
    private_key, registry, _ = _authority(tmp_path)

    with pytest.raises(HumanAuthorityError):
        sign_challenge(
            challenge_path=challenge_path,
            private_key_path=private_key,
            authority_registry_path=registry,
            output_path=tmp_path / "approval.json",
            confirmation="APPROVE_PACKAGE wrong",
        )

    approval = sign_challenge(
        challenge_path=challenge_path,
        private_key_path=private_key,
        authority_registry_path=registry,
        output_path=tmp_path / "approval.json",
        confirmation=required_human_confirmation(challenge),
    )

    assert approval.decision == "APPROVE_PACKAGE"
    assert approval.confirmation.endswith(approval.challenge_hash[:12])


def test_ed25519_signature_verifies_and_builds_durable_pack(tmp_path: Path) -> None:
    challenge_path = tmp_path / "challenge.json"
    challenge = _challenge()
    _write(challenge_path, challenge)
    private_key, registry, authority = _authority(tmp_path)
    approval_path = tmp_path / "approval.json"

    sign_challenge(
        challenge_path=challenge_path,
        private_key_path=private_key,
        authority_registry_path=registry,
        output_path=approval_path,
        confirmation=required_human_confirmation(challenge),
    )

    verification = verify_signed_approval(
        challenge_path=challenge_path,
        approval_path=approval_path,
        authority_registry_path=registry,
    )
    pack = build_evidence_pack(
        challenge_path=challenge_path,
        approval_path=approval_path,
        authority_registry_path=registry,
        output_path=tmp_path / "evidence-pack.json",
    )

    assert verification["ok"] is True
    assert verification["checks"]["ed25519_signature_valid"] is True
    assert verification["checks"]["challenge_nonce_match"] is True
    assert verification["external_submission_authorized"] is False
    assert pack["approval_verified"] is True
    assert pack["authority_key_fingerprint"] == authority.public_key_sha256
    assert pack["evidence_pack_hash"]
    assert pack["external_side_effect"] is False
    assert pack["submission_authorized"] is False


def test_tampered_challenge_is_rejected(tmp_path: Path) -> None:
    challenge_path = tmp_path / "challenge.json"
    challenge = _challenge()
    _write(challenge_path, challenge)
    private_key, registry, _ = _authority(tmp_path)
    approval_path = tmp_path / "approval.json"

    sign_challenge(
        challenge_path=challenge_path,
        private_key_path=private_key,
        authority_registry_path=registry,
        output_path=approval_path,
        confirmation=required_human_confirmation(challenge),
    )

    challenge["nonce"] = "ffffffffffffffffffffffffffffffff"
    _write(challenge_path, challenge)
    verification = verify_signed_approval(
        challenge_path=challenge_path,
        approval_path=approval_path,
        authority_registry_path=registry,
    )

    assert verification["ok"] is False
    assert verification["checks"]["challenge_hash_match"] is False
    assert verification["checks"]["challenge_nonce_match"] is False
    assert verification["checks"]["ed25519_signature_valid"] is True


def test_wrong_authority_key_is_rejected(tmp_path: Path) -> None:
    challenge_path = tmp_path / "challenge.json"
    challenge = _challenge()
    _write(challenge_path, challenge)
    private_key, registry, _ = _authority(tmp_path / "a")
    approval_path = tmp_path / "approval.json"

    sign_challenge(
        challenge_path=challenge_path,
        private_key_path=private_key,
        authority_registry_path=registry,
        output_path=approval_path,
        confirmation=required_human_confirmation(challenge),
    )

    _, wrong_registry, _ = _authority(tmp_path / "b")
    verification = verify_signed_approval(
        challenge_path=challenge_path,
        approval_path=approval_path,
        authority_registry_path=wrong_registry,
    )

    assert verification["ok"] is False
    assert verification["checks"]["authority_fingerprint_match"] is False
    assert verification["checks"]["ed25519_signature_valid"] is False
