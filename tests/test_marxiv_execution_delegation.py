from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.marxiv_execution_delegation import (
    ExecutionDelegationError,
    build_execution_context,
    describe_delegation_request,
    request_delegation,
    required_delegation_confirmation,
    sign_delegation_request,
    verify_execution_delegation,
    verify_human_evidence_pack,
)
from app.marxiv_human_authority import (
    build_evidence_pack,
    initialize_authority,
    required_human_confirmation,
    sign_challenge,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _human_approval_evidence(tmp_path: Path) -> tuple[Path, Path, Path]:
    now = datetime.now(timezone.utc)
    challenge = {
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
    challenge_path = tmp_path / "approval-challenge.json"
    private_key = tmp_path / "human-private.pem"
    registry = tmp_path / "authority.json"
    approval_path = tmp_path / "human-approval.v2.json"
    evidence_pack = tmp_path / "human-approval-evidence-pack.v2.json"
    _write(challenge_path, challenge)

    initialize_authority(
        authority_id="author-human-authority",
        private_key_path=private_key,
        public_registry_path=registry,
    )
    sign_challenge(
        challenge_path=challenge_path,
        private_key_path=private_key,
        authority_registry_path=registry,
        output_path=approval_path,
        confirmation=required_human_confirmation(challenge),
    )
    build_evidence_pack(
        challenge_path=challenge_path,
        approval_path=approval_path,
        authority_registry_path=registry,
        output_path=evidence_pack,
    )
    return evidence_pack, private_key, registry


def _delegated(tmp_path: Path):
    evidence_pack, private_key, registry = _human_approval_evidence(tmp_path)
    request_path = tmp_path / "execution-delegation-request.v1.json"
    delegation_path = tmp_path / "execution-delegation.v1.json"
    request = request_delegation(
        evidence_pack_path=evidence_pack,
        delegatee_id="marxiv-runtime-publisher",
        capabilities=["VERIFY_PACKAGE", "PREPARE_RUNTIME_CONTEXT", "BUILD_EXTERNAL_EFFECT_REQUEST"],
        ttl_seconds=1800,
        output_path=request_path,
    )
    delegation = sign_delegation_request(
        request_path=request_path,
        private_key_path=private_key,
        authority_registry_path=registry,
        output_path=delegation_path,
        confirmation=required_delegation_confirmation(request),
    )
    return evidence_pack, private_key, registry, request_path, delegation_path, request, delegation


def test_verified_human_evidence_is_required(tmp_path: Path) -> None:
    evidence_pack, _, _ = _human_approval_evidence(tmp_path)
    valid = verify_human_evidence_pack(evidence_pack)
    assert valid["ok"] is True
    assert valid["external_effect_authorized"] is False

    tampered = json.loads(evidence_pack.read_text(encoding="utf-8"))
    tampered["package_hash"] = "f" * 64
    evidence_pack.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(ExecutionDelegationError, match="verified HumanApprovalV2 evidence required"):
        request_delegation(
            evidence_pack_path=evidence_pack,
            delegatee_id="marxiv-runtime-publisher",
            capabilities=["VERIFY_PACKAGE"],
        )


def test_delegation_confirmation_binds_delegatee_package_and_fresh_request(tmp_path: Path) -> None:
    evidence_pack, _, _ = _human_approval_evidence(tmp_path)
    request_path = tmp_path / "request.json"
    request = request_delegation(
        evidence_pack_path=evidence_pack,
        delegatee_id="marxiv-runtime-publisher",
        capabilities=["VERIFY_PACKAGE", "PREPARE_RUNTIME_CONTEXT"],
        output_path=request_path,
    )
    summary = describe_delegation_request(request_path)

    assert summary["required_human_confirmation"].startswith(
        "DELEGATE_EXECUTION matverse-2.0-v1-arxiv 4ef1c650ccf5 marxiv-runtime-publisher "
    )
    assert summary["required_human_confirmation"].endswith(summary["request_hash"][:12])
    assert required_delegation_confirmation(request) == summary["required_human_confirmation"]
    assert summary["external_effect_authorized"] is False
    assert summary["arxiv_submission_authorized"] is False


def test_delegation_requires_a_second_exact_human_signature(tmp_path: Path) -> None:
    evidence_pack, private_key, registry = _human_approval_evidence(tmp_path)
    request_path = tmp_path / "request.json"
    request = request_delegation(
        evidence_pack_path=evidence_pack,
        delegatee_id="marxiv-runtime-publisher",
        capabilities=["VERIFY_PACKAGE"],
        output_path=request_path,
    )

    with pytest.raises(ExecutionDelegationError, match="not bound"):
        sign_delegation_request(
            request_path=request_path,
            private_key_path=private_key,
            authority_registry_path=registry,
            output_path=tmp_path / "delegation.json",
            confirmation="DELEGATE_EXECUTION wrong",
        )

    delegation = sign_delegation_request(
        request_path=request_path,
        private_key_path=private_key,
        authority_registry_path=registry,
        output_path=tmp_path / "delegation.json",
        confirmation=required_delegation_confirmation(request),
    )
    assert delegation.decision == "DELEGATE_EXECUTION"
    assert delegation.external_effect_authorized is False
    assert delegation.arxiv_login_authorized is False
    assert delegation.arxiv_submission_authorized is False


def test_execution_delegation_verifies_and_materializes_local_context_only(tmp_path: Path) -> None:
    evidence_pack, _, registry, request_path, delegation_path, _, _ = _delegated(tmp_path)

    verification = verify_execution_delegation(
        request_path=request_path,
        delegation_path=delegation_path,
        authority_registry_path=registry,
        evidence_pack_path=evidence_pack,
    )
    context = build_execution_context(
        request_path=request_path,
        delegation_path=delegation_path,
        authority_registry_path=registry,
        evidence_pack_path=evidence_pack,
        output_path=tmp_path / "execution-context.v1.json",
    )

    assert verification["ok"] is True
    assert verification["checks"]["ed25519_signature_valid"] is True
    assert verification["checks"]["human_approval_evidence_valid"] is True
    assert verification["external_effect_authorization_required"] is True
    assert context.status == "DELEGATED_LOCAL_EXECUTION"
    assert context.external_effect_authorization_required is True
    assert context.external_effect_authorized is False
    assert context.arxiv_login_authorized is False
    assert context.arxiv_submission_authorized is False
    assert "ARXIV_LOGIN" in context.prohibited_capabilities
    assert "ARXIV_SUBMIT" in context.prohibited_capabilities
    assert "EXTERNAL_PUBLICATION" in context.prohibited_capabilities
    assert context.context_hash


def test_tampered_request_invalidates_signed_delegation(tmp_path: Path) -> None:
    evidence_pack, _, registry, request_path, delegation_path, _, _ = _delegated(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["delegatee_id"] = "different-runtime"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    verification = verify_execution_delegation(
        request_path=request_path,
        delegation_path=delegation_path,
        authority_registry_path=registry,
        evidence_pack_path=evidence_pack,
    )

    assert verification["ok"] is False
    assert verification["checks"]["request_hash_match"] is False
    assert verification["checks"]["delegatee_match"] is False


def test_prohibited_external_capabilities_cannot_enter_request(tmp_path: Path) -> None:
    evidence_pack, _, _ = _human_approval_evidence(tmp_path)

    with pytest.raises(ValidationError):
        request_delegation(
            evidence_pack_path=evidence_pack,
            delegatee_id="marxiv-runtime-publisher",
            capabilities=["ARXIV_SUBMIT"],
        )


def test_ttl_is_fail_closed(tmp_path: Path) -> None:
    evidence_pack, _, _ = _human_approval_evidence(tmp_path)

    for ttl in (0, 299, 86401):
        with pytest.raises(ExecutionDelegationError, match="between 300 and 86400"):
            request_delegation(
                evidence_pack_path=evidence_pack,
                delegatee_id="marxiv-runtime-publisher",
                capabilities=["VERIFY_PACKAGE"],
                ttl_seconds=ttl,
            )


def test_tampered_delegation_signature_is_rejected(tmp_path: Path) -> None:
    evidence_pack, _, registry, request_path, delegation_path, _, _ = _delegated(tmp_path)
    delegation = json.loads(delegation_path.read_text(encoding="utf-8"))
    delegation["signature_base64"] = "AA=="
    delegation_path.write_text(json.dumps(delegation), encoding="utf-8")

    verification = verify_execution_delegation(
        request_path=request_path,
        delegation_path=delegation_path,
        authority_registry_path=registry,
        evidence_pack_path=evidence_pack,
    )

    assert verification["ok"] is False
    assert verification["checks"]["ed25519_signature_valid"] is False
