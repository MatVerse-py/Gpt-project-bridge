from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.evidence import canonical_json, sha256_text
from app.marxiv_human_authority import HumanAuthority, SignedHumanApproval, required_human_confirmation

EVIDENCE_PACK_SCHEMA = "marxiv.human-approval-evidence-pack.v2"
DELEGATION_REQUEST_SCHEMA = "marxiv.execution-delegation-request.v1"
DELEGATION_SCHEMA = "marxiv.execution-delegation.v1"
EXECUTION_CONTEXT_SCHEMA = "marxiv.execution-context.v1"

AllowedCapability = Literal[
    "VERIFY_PACKAGE",
    "PREPARE_RUNTIME_CONTEXT",
    "BUILD_EXTERNAL_EFFECT_REQUEST",
]

ALLOWED_CAPABILITIES: tuple[str, ...] = (
    "VERIFY_PACKAGE",
    "PREPARE_RUNTIME_CONTEXT",
    "BUILD_EXTERNAL_EFFECT_REQUEST",
)

PROHIBITED_CAPABILITIES: tuple[str, ...] = (
    "ARXIV_LOGIN",
    "ARXIV_SUBMIT",
    "EXTERNAL_PUBLICATION",
    "FINAL_SUBMIT_CLICK",
    "RECONCILE_EXTERNAL_IDENTIFIER",
)


class ExecutionDelegationError(RuntimeError):
    pass


class ExecutionDelegationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.execution-delegation-request.v1"] = DELEGATION_REQUEST_SCHEMA
    publication_id: str = Field(min_length=3, max_length=256)
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_approval_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_id: str = Field(min_length=1, max_length=256)
    authority_key_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    delegatee_id: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$")
    capabilities: list[AllowedCapability] = Field(min_length=1)
    issued_at: str
    expires_at: str
    nonce: str = Field(min_length=32, max_length=128, pattern=r"^[0-9a-f]+$")
    external_effect_authorized: Literal[False] = False
    arxiv_login_authorized: Literal[False] = False
    arxiv_submission_authorized: Literal[False] = False

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, values: list[AllowedCapability]) -> list[AllowedCapability]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate delegation capabilities are not allowed")
        return values


class SignedExecutionDelegation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.execution-delegation.v1"] = DELEGATION_SCHEMA
    decision: Literal["DELEGATE_EXECUTION"] = "DELEGATE_EXECUTION"
    publication_id: str
    package_hash: str
    human_approval_evidence_hash: str
    authority_id: str
    authority_key_fingerprint: str
    delegatee_id: str
    capabilities: list[AllowedCapability]
    request_hash: str
    request_nonce: str
    request_issued_at: str
    request_expires_at: str
    signed_at: str
    external_effect_authorized: Literal[False] = False
    arxiv_login_authorized: Literal[False] = False
    arxiv_submission_authorized: Literal[False] = False
    signature_base64: str


class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.execution-context.v1"] = EXECUTION_CONTEXT_SCHEMA
    status: Literal["DELEGATED_LOCAL_EXECUTION"] = "DELEGATED_LOCAL_EXECUTION"
    publication_id: str
    package_hash: str
    human_approval_evidence_hash: str
    delegation_hash: str
    authority_id: str
    authority_key_fingerprint: str
    delegatee_id: str
    capabilities: list[AllowedCapability]
    prohibited_capabilities: list[str]
    external_effect_authorization_required: Literal[True] = True
    external_effect_authorized: Literal[False] = False
    arxiv_login_authorized: Literal[False] = False
    arxiv_submission_authorized: Literal[False] = False
    context_hash: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _challenge_hash(challenge: dict[str, Any]) -> str:
    if challenge.get("schema") != "marxiv.approval-challenge.v1":
        raise ExecutionDelegationError("unsupported human-approval challenge schema")
    return sha256_text(canonical_json(challenge))


def _approval_payload(approval: SignedHumanApproval) -> dict[str, Any]:
    return {
        "schema": "marxiv.human-approval.v2",
        "decision": "APPROVE_PACKAGE",
        "authority_id": approval.authority_id,
        "algorithm": "Ed25519",
        "publication_id": approval.publication_id,
        "package_hash": approval.package_hash,
        "challenge_hash": approval.challenge_hash,
        "challenge_nonce": approval.challenge_nonce,
        "challenge_issued_at": approval.challenge_issued_at,
        "challenge_expires_at": approval.challenge_expires_at,
        "confirmation": approval.confirmation,
        "authority_key_fingerprint": approval.authority_key_fingerprint,
        "signed_at": approval.signed_at,
    }


def _public_key(authority: HumanAuthority) -> Ed25519PublicKey:
    try:
        raw = base64.b64decode(authority.public_key_base64, validate=True)
    except Exception as exc:
        raise ExecutionDelegationError("authority public key is not valid base64") from exc
    if len(raw) != 32:
        raise ExecutionDelegationError("Ed25519 public key must be 32 bytes")
    if hashlib.sha256(raw).hexdigest() != authority.public_key_sha256:
        raise ExecutionDelegationError("authority public-key fingerprint mismatch")
    return Ed25519PublicKey.from_public_bytes(raw)


def _private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.expanduser().resolve().read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ExecutionDelegationError("private key is not Ed25519")
    return key


def _private_fingerprint(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def verify_human_evidence_pack(
    evidence_pack_path: Path,
    *,
    require_unexpired: bool = False,
) -> dict[str, Any]:
    try:
        pack = _read_json(evidence_pack_path)
        if pack.get("schema") != EVIDENCE_PACK_SCHEMA:
            raise ExecutionDelegationError("unsupported human approval evidence-pack schema")

        claimed_pack_hash = pack.get("evidence_pack_hash")
        hash_input = dict(pack)
        hash_input.pop("evidence_pack_hash", None)
        computed_pack_hash = sha256_text(canonical_json(hash_input))

        objects = pack.get("objects") or {}
        challenge = objects.get("challenge") or {}
        approval = SignedHumanApproval.model_validate(objects.get("approval") or {})
        authority = HumanAuthority.model_validate(objects.get("authority") or {})
        embedded_verification = objects.get("verification") or {}

        challenge_digest = _challenge_hash(challenge)
        expected_confirmation = required_human_confirmation(challenge)
        signed_at = _parse_iso(approval.signed_at)
        issued_at = _parse_iso(challenge["issued_at"])
        expires_at = _parse_iso(challenge["expires_at"])

        checks: dict[str, bool] = {
            "evidence_pack_hash_match": claimed_pack_hash == computed_pack_hash,
            "pack_approval_verified": pack.get("approval_verified") is True,
            "pack_external_side_effect_false": pack.get("external_side_effect") is False,
            "pack_submission_authorized_false": pack.get("submission_authorized") is False,
            "embedded_verification_ok": embedded_verification.get("ok") is True,
            "embedded_external_submission_false": embedded_verification.get("external_submission_authorized") is False,
            "authority_active": authority.status == "ACTIVE",
            "authority_id_match": approval.authority_id == authority.authority_id == pack.get("authority_id"),
            "authority_fingerprint_match": (
                approval.authority_key_fingerprint
                == authority.public_key_sha256
                == pack.get("authority_key_fingerprint")
            ),
            "publication_id_match": approval.publication_id == challenge.get("publication_id") == pack.get("publication_id"),
            "package_hash_match": approval.package_hash == challenge.get("package_hash") == pack.get("package_hash"),
            "challenge_hash_match": approval.challenge_hash == challenge_digest == pack.get("challenge_hash"),
            "challenge_nonce_match": approval.challenge_nonce == challenge.get("nonce"),
            "challenge_issued_at_match": approval.challenge_issued_at == challenge.get("issued_at"),
            "challenge_expires_at_match": approval.challenge_expires_at == challenge.get("expires_at"),
            "confirmation_match": approval.confirmation == expected_confirmation,
            "signed_within_challenge_window": issued_at <= signed_at < expires_at,
        }
        if require_unexpired:
            checks["challenge_not_expired"] = _utc_now() < expires_at

        signature_ok = False
        try:
            signature = base64.b64decode(approval.signature_base64, validate=True)
            _public_key(authority).verify(
                signature,
                canonical_json(_approval_payload(approval)).encode("utf-8"),
            )
            signature_ok = True
        except (InvalidSignature, ValueError, ExecutionDelegationError):
            signature_ok = False
        checks["ed25519_signature_valid"] = signature_ok

        return {
            "schema": "marxiv.human-approval-evidence-verification.v1",
            "ok": all(checks.values()),
            "checks": checks,
            "publication_id": approval.publication_id,
            "package_hash": approval.package_hash,
            "authority_id": approval.authority_id,
            "authority_key_fingerprint": approval.authority_key_fingerprint,
            "human_approval_evidence_hash": claimed_pack_hash,
            "external_effect_authorized": False,
            "arxiv_login_authorized": False,
            "arxiv_submission_authorized": False,
        }
    except Exception as exc:
        return {
            "schema": "marxiv.human-approval-evidence-verification.v1",
            "ok": False,
            "reason": str(exc),
            "external_effect_authorized": False,
            "arxiv_login_authorized": False,
            "arxiv_submission_authorized": False,
        }


def _request_hash(request: ExecutionDelegationRequest) -> str:
    return sha256_text(canonical_json(request.model_dump(mode="json")))


def required_delegation_confirmation(request: ExecutionDelegationRequest) -> str:
    digest = _request_hash(request)
    return (
        f"DELEGATE_EXECUTION {request.publication_id} "
        f"{request.package_hash[:12]} {request.delegatee_id} {digest[:12]}"
    )


def request_delegation(
    *,
    evidence_pack_path: Path,
    delegatee_id: str,
    capabilities: list[str],
    ttl_seconds: int = 3600,
    output_path: Path | None = None,
) -> ExecutionDelegationRequest:
    if not 300 <= ttl_seconds <= 86400:
        raise ExecutionDelegationError("delegation ttl_seconds must be between 300 and 86400")

    evidence = verify_human_evidence_pack(evidence_pack_path, require_unexpired=False)
    if evidence.get("ok") is not True:
        raise ExecutionDelegationError(f"verified HumanApprovalV2 evidence required: {evidence}")

    now = _utc_now()
    request = ExecutionDelegationRequest(
        publication_id=evidence["publication_id"],
        package_hash=evidence["package_hash"],
        human_approval_evidence_hash=evidence["human_approval_evidence_hash"],
        authority_id=evidence["authority_id"],
        authority_key_fingerprint=evidence["authority_key_fingerprint"],
        delegatee_id=delegatee_id,
        capabilities=capabilities,
        issued_at=_iso(now),
        expires_at=_iso(now + timedelta(seconds=ttl_seconds)),
        nonce=secrets.token_hex(16),
    )
    if output_path is not None:
        _write_json(output_path, request.model_dump(mode="json"))
    return request


def describe_delegation_request(request_path: Path) -> dict[str, Any]:
    request = ExecutionDelegationRequest.model_validate(_read_json(request_path))
    return {
        "schema": "marxiv.execution-delegation-summary.v1",
        "publication_id": request.publication_id,
        "package_hash": request.package_hash,
        "human_approval_evidence_hash": request.human_approval_evidence_hash,
        "delegatee_id": request.delegatee_id,
        "capabilities": request.capabilities,
        "request_hash": _request_hash(request),
        "issued_at": request.issued_at,
        "expires_at": request.expires_at,
        "required_human_confirmation": required_delegation_confirmation(request),
        "external_effect_authorized": False,
        "arxiv_login_authorized": False,
        "arxiv_submission_authorized": False,
    }


def _delegation_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": DELEGATION_SCHEMA,
        "decision": "DELEGATE_EXECUTION",
        "publication_id": data["publication_id"],
        "package_hash": data["package_hash"],
        "human_approval_evidence_hash": data["human_approval_evidence_hash"],
        "authority_id": data["authority_id"],
        "authority_key_fingerprint": data["authority_key_fingerprint"],
        "delegatee_id": data["delegatee_id"],
        "capabilities": data["capabilities"],
        "request_hash": data["request_hash"],
        "request_nonce": data["request_nonce"],
        "request_issued_at": data["request_issued_at"],
        "request_expires_at": data["request_expires_at"],
        "signed_at": data["signed_at"],
        "external_effect_authorized": False,
        "arxiv_login_authorized": False,
        "arxiv_submission_authorized": False,
    }


def sign_delegation_request(
    *,
    request_path: Path,
    private_key_path: Path,
    authority_registry_path: Path,
    output_path: Path,
    confirmation: str,
) -> SignedExecutionDelegation:
    request = ExecutionDelegationRequest.model_validate(_read_json(request_path))
    authority = HumanAuthority.model_validate(_read_json(authority_registry_path))
    if authority.status != "ACTIVE":
        raise ExecutionDelegationError("authority is not ACTIVE")
    if authority.authority_id != request.authority_id:
        raise ExecutionDelegationError("delegation authority id does not match HumanApprovalV2 authority")
    if authority.public_key_sha256 != request.authority_key_fingerprint:
        raise ExecutionDelegationError("delegation authority key does not match HumanApprovalV2 authority")

    now = _utc_now()
    issued_at = _parse_iso(request.issued_at)
    expires_at = _parse_iso(request.expires_at)
    if now < issued_at or now >= expires_at:
        raise ExecutionDelegationError("delegation request is not currently valid")

    expected_confirmation = required_delegation_confirmation(request)
    if confirmation.strip() != expected_confirmation:
        raise ExecutionDelegationError("human confirmation is not bound to this execution delegation request")

    private_key = _private_key(private_key_path)
    if _private_fingerprint(private_key) != authority.public_key_sha256:
        raise ExecutionDelegationError("private key does not match registered human authority")

    data = {
        "publication_id": request.publication_id,
        "package_hash": request.package_hash,
        "human_approval_evidence_hash": request.human_approval_evidence_hash,
        "authority_id": request.authority_id,
        "authority_key_fingerprint": request.authority_key_fingerprint,
        "delegatee_id": request.delegatee_id,
        "capabilities": request.capabilities,
        "request_hash": _request_hash(request),
        "request_nonce": request.nonce,
        "request_issued_at": request.issued_at,
        "request_expires_at": request.expires_at,
        "signed_at": _iso(now),
    }
    payload = _delegation_payload(data)
    signature = private_key.sign(canonical_json(payload).encode("utf-8"))
    delegation = SignedExecutionDelegation(
        **payload,
        signature_base64=base64.b64encode(signature).decode("ascii"),
    )
    _write_json(output_path, delegation.model_dump(mode="json"))
    return delegation


def verify_execution_delegation(
    *,
    request_path: Path,
    delegation_path: Path,
    authority_registry_path: Path,
    evidence_pack_path: Path,
    require_unexpired: bool = True,
) -> dict[str, Any]:
    try:
        request = ExecutionDelegationRequest.model_validate(_read_json(request_path))
        delegation = SignedExecutionDelegation.model_validate(_read_json(delegation_path))
        authority = HumanAuthority.model_validate(_read_json(authority_registry_path))
        evidence = verify_human_evidence_pack(evidence_pack_path, require_unexpired=False)

        request_hash = _request_hash(request)
        signed_at = _parse_iso(delegation.signed_at)
        issued_at = _parse_iso(request.issued_at)
        expires_at = _parse_iso(request.expires_at)

        checks: dict[str, bool] = {
            "human_approval_evidence_valid": evidence.get("ok") is True,
            "authority_active": authority.status == "ACTIVE",
            "authority_id_match": (
                delegation.authority_id == request.authority_id == authority.authority_id == evidence.get("authority_id")
            ),
            "authority_fingerprint_match": (
                delegation.authority_key_fingerprint
                == request.authority_key_fingerprint
                == authority.public_key_sha256
                == evidence.get("authority_key_fingerprint")
            ),
            "publication_id_match": (
                delegation.publication_id == request.publication_id == evidence.get("publication_id")
            ),
            "package_hash_match": delegation.package_hash == request.package_hash == evidence.get("package_hash"),
            "evidence_hash_match": (
                delegation.human_approval_evidence_hash
                == request.human_approval_evidence_hash
                == evidence.get("human_approval_evidence_hash")
            ),
            "request_hash_match": delegation.request_hash == request_hash,
            "request_nonce_match": delegation.request_nonce == request.nonce,
            "request_issued_at_match": delegation.request_issued_at == request.issued_at,
            "request_expires_at_match": delegation.request_expires_at == request.expires_at,
            "delegatee_match": delegation.delegatee_id == request.delegatee_id,
            "capabilities_match": delegation.capabilities == request.capabilities,
            "capabilities_allowed": all(cap in ALLOWED_CAPABILITIES for cap in delegation.capabilities),
            "no_prohibited_capability": all(cap not in PROHIBITED_CAPABILITIES for cap in delegation.capabilities),
            "signed_within_request_window": issued_at <= signed_at < expires_at,
            "external_effect_not_authorized": delegation.external_effect_authorized is False,
            "arxiv_login_not_authorized": delegation.arxiv_login_authorized is False,
            "arxiv_submission_not_authorized": delegation.arxiv_submission_authorized is False,
        }
        if require_unexpired:
            checks["delegation_not_expired"] = _utc_now() < expires_at

        signature_ok = False
        try:
            signature = base64.b64decode(delegation.signature_base64, validate=True)
            _public_key(authority).verify(
                signature,
                canonical_json(_delegation_payload(delegation.model_dump(mode="json"))).encode("utf-8"),
            )
            signature_ok = True
        except (InvalidSignature, ValueError, ExecutionDelegationError):
            signature_ok = False
        checks["ed25519_signature_valid"] = signature_ok

        delegation_hash = sha256_text(canonical_json(delegation.model_dump(mode="json")))
        return {
            "schema": "marxiv.execution-delegation-verification.v1",
            "ok": all(checks.values()),
            "checks": checks,
            "publication_id": delegation.publication_id,
            "package_hash": delegation.package_hash,
            "human_approval_evidence_hash": delegation.human_approval_evidence_hash,
            "delegation_hash": delegation_hash,
            "authority_id": delegation.authority_id,
            "authority_key_fingerprint": delegation.authority_key_fingerprint,
            "delegatee_id": delegation.delegatee_id,
            "capabilities": delegation.capabilities,
            "external_effect_authorization_required": True,
            "external_effect_authorized": False,
            "arxiv_login_authorized": False,
            "arxiv_submission_authorized": False,
        }
    except Exception as exc:
        return {
            "schema": "marxiv.execution-delegation-verification.v1",
            "ok": False,
            "reason": str(exc),
            "external_effect_authorization_required": True,
            "external_effect_authorized": False,
            "arxiv_login_authorized": False,
            "arxiv_submission_authorized": False,
        }


def build_execution_context(
    *,
    request_path: Path,
    delegation_path: Path,
    authority_registry_path: Path,
    evidence_pack_path: Path,
    output_path: Path,
) -> ExecutionContext:
    verification = verify_execution_delegation(
        request_path=request_path,
        delegation_path=delegation_path,
        authority_registry_path=authority_registry_path,
        evidence_pack_path=evidence_pack_path,
        require_unexpired=True,
    )
    if verification.get("ok") is not True:
        raise ExecutionDelegationError(f"cannot build execution context from invalid delegation: {verification}")

    context_core = {
        "schema": EXECUTION_CONTEXT_SCHEMA,
        "status": "DELEGATED_LOCAL_EXECUTION",
        "publication_id": verification["publication_id"],
        "package_hash": verification["package_hash"],
        "human_approval_evidence_hash": verification["human_approval_evidence_hash"],
        "delegation_hash": verification["delegation_hash"],
        "authority_id": verification["authority_id"],
        "authority_key_fingerprint": verification["authority_key_fingerprint"],
        "delegatee_id": verification["delegatee_id"],
        "capabilities": verification["capabilities"],
        "prohibited_capabilities": list(PROHIBITED_CAPABILITIES),
        "external_effect_authorization_required": True,
        "external_effect_authorized": False,
        "arxiv_login_authorized": False,
        "arxiv_submission_authorized": False,
    }
    context_hash = sha256_text(canonical_json(context_core))
    context = ExecutionContext(**context_core, context_hash=context_hash)
    _write_json(output_path, context.model_dump(mode="json"))
    return context


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marxiv-execution-delegation")
    sub = parser.add_subparsers(dest="command", required=True)

    request = sub.add_parser("request")
    request.add_argument("--evidence-pack", required=True, type=Path)
    request.add_argument("--delegatee", required=True)
    request.add_argument("--capability", action="append", choices=ALLOWED_CAPABILITIES)
    request.add_argument("--ttl-seconds", type=int, default=3600)
    request.add_argument("--output", required=True, type=Path)

    describe = sub.add_parser("describe")
    describe.add_argument("--request", required=True, type=Path)

    sign = sub.add_parser("sign")
    sign.add_argument("--request", required=True, type=Path)
    sign.add_argument("--private-key", required=True, type=Path)
    sign.add_argument("--authority-registry", required=True, type=Path)
    sign.add_argument("--output", required=True, type=Path)
    sign.add_argument("--confirm", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--request", required=True, type=Path)
    verify.add_argument("--delegation", required=True, type=Path)
    verify.add_argument("--authority-registry", required=True, type=Path)
    verify.add_argument("--evidence-pack", required=True, type=Path)
    verify.add_argument("--allow-expired", action="store_true")

    context = sub.add_parser("context")
    context.add_argument("--request", required=True, type=Path)
    context.add_argument("--delegation", required=True, type=Path)
    context.add_argument("--authority-registry", required=True, type=Path)
    context.add_argument("--evidence-pack", required=True, type=Path)
    context.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "request":
        capabilities = args.capability or ["VERIFY_PACKAGE", "PREPARE_RUNTIME_CONTEXT"]
        result = request_delegation(
            evidence_pack_path=args.evidence_pack,
            delegatee_id=args.delegatee,
            capabilities=capabilities,
            ttl_seconds=args.ttl_seconds,
            output_path=args.output,
        ).model_dump(mode="json")
    elif args.command == "describe":
        result = describe_delegation_request(args.request)
    elif args.command == "sign":
        result = sign_delegation_request(
            request_path=args.request,
            private_key_path=args.private_key,
            authority_registry_path=args.authority_registry,
            output_path=args.output,
            confirmation=args.confirm,
        ).model_dump(mode="json")
    elif args.command == "verify":
        result = verify_execution_delegation(
            request_path=args.request,
            delegation_path=args.delegation,
            authority_registry_path=args.authority_registry,
            evidence_pack_path=args.evidence_pack,
            require_unexpired=not args.allow_expired,
        )
        if result.get("ok") is not True:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 2
    else:
        result = build_execution_context(
            request_path=args.request,
            delegation_path=args.delegation,
            authority_registry_path=args.authority_registry,
            evidence_pack_path=args.evidence_pack,
            output_path=args.output,
        ).model_dump(mode="json")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
