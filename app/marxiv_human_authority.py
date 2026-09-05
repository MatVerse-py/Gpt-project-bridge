from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from app.evidence import canonical_json, sha256_text

AUTHORITY_SCHEMA = "marxiv.human-authority.v2"
APPROVAL_SCHEMA = "marxiv.human-approval.v2"
CHALLENGE_SCHEMA = "marxiv.approval-challenge.v1"


class HumanAuthorityError(RuntimeError):
    pass


class HumanAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.human-authority.v2"] = AUTHORITY_SCHEMA
    authority_id: str = Field(min_length=1, max_length=256)
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_base64: str
    public_key_sha256: str
    created_at: str
    status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"


class SignedHumanApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.human-approval.v2"] = APPROVAL_SCHEMA
    decision: Literal["APPROVE_PACKAGE"] = "APPROVE_PACKAGE"
    authority_id: str
    algorithm: Literal["Ed25519"] = "Ed25519"
    publication_id: str
    package_hash: str
    challenge_hash: str
    challenge_nonce: str
    challenge_issued_at: str
    challenge_expires_at: str
    confirmation: str
    authority_key_fingerprint: str
    signed_at: str
    signature_base64: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _challenge_hash(challenge: dict[str, Any]) -> str:
    if challenge.get("schema") != CHALLENGE_SCHEMA:
        raise HumanAuthorityError("unsupported approval challenge schema")
    return sha256_text(canonical_json(challenge))


def required_human_confirmation(challenge: dict[str, Any]) -> str:
    digest = _challenge_hash(challenge)
    return f"APPROVE_PACKAGE {challenge['publication_id']} {challenge['package_hash'][:12]} {digest[:12]}"


def _public_raw(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _fingerprint(raw_public_key: bytes) -> str:
    return hashlib.sha256(raw_public_key).hexdigest()


def initialize_authority(*, authority_id: str, private_key_path: Path, public_registry_path: Path) -> HumanAuthority:
    private_key_path = private_key_path.expanduser().resolve()
    public_registry_path = public_registry_path.expanduser().resolve()
    if private_key_path.exists():
        raise HumanAuthorityError(f"refusing to overwrite existing private key: {private_key_path}")
    if public_registry_path.exists():
        raise HumanAuthorityError(f"refusing to overwrite existing authority registry: {public_registry_path}")

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    raw_public = _public_raw(public_key)

    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(private_key_path, 0o600)

    authority = HumanAuthority(
        authority_id=authority_id,
        public_key_base64=base64.b64encode(raw_public).decode("ascii"),
        public_key_sha256=_fingerprint(raw_public),
        created_at=_iso(_utc_now()),
        status="ACTIVE",
    )
    _write_json(public_registry_path, authority.model_dump(mode="json"))
    return authority


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.expanduser().resolve().read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise HumanAuthorityError("private key is not Ed25519")
    return key


def _load_public_key(authority: HumanAuthority) -> Ed25519PublicKey:
    try:
        raw = base64.b64decode(authority.public_key_base64, validate=True)
    except Exception as exc:
        raise HumanAuthorityError("authority public key is not valid base64") from exc
    if len(raw) != 32:
        raise HumanAuthorityError("Ed25519 public key must be 32 bytes")
    if _fingerprint(raw) != authority.public_key_sha256:
        raise HumanAuthorityError("authority public-key fingerprint mismatch")
    return Ed25519PublicKey.from_public_bytes(raw)


def _approval_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": APPROVAL_SCHEMA,
        "decision": "APPROVE_PACKAGE",
        "authority_id": data["authority_id"],
        "algorithm": "Ed25519",
        "publication_id": data["publication_id"],
        "package_hash": data["package_hash"],
        "challenge_hash": data["challenge_hash"],
        "challenge_nonce": data["challenge_nonce"],
        "challenge_issued_at": data["challenge_issued_at"],
        "challenge_expires_at": data["challenge_expires_at"],
        "confirmation": data["confirmation"],
        "authority_key_fingerprint": data["authority_key_fingerprint"],
        "signed_at": data["signed_at"],
    }


def describe_challenge(challenge_path: Path) -> dict[str, Any]:
    challenge = _read_json(challenge_path.expanduser().resolve())
    digest = _challenge_hash(challenge)
    return {
        "schema": "marxiv.human-challenge-summary.v2",
        "publication_id": challenge["publication_id"],
        "package_hash": challenge["package_hash"],
        "challenge_hash": digest,
        "issued_at": challenge["issued_at"],
        "expires_at": challenge["expires_at"],
        "required_human_confirmation": required_human_confirmation(challenge),
        "external_submission_authorized": False,
    }


def sign_challenge(
    *,
    challenge_path: Path,
    private_key_path: Path,
    authority_registry_path: Path,
    output_path: Path,
    confirmation: str,
) -> SignedHumanApproval:
    challenge = _read_json(challenge_path.expanduser().resolve())
    authority = HumanAuthority.model_validate(_read_json(authority_registry_path.expanduser().resolve()))
    if authority.status != "ACTIVE":
        raise HumanAuthorityError("authority is not ACTIVE")

    now = _utc_now()
    issued_at = _parse_iso(challenge["issued_at"])
    expires_at = _parse_iso(challenge["expires_at"])
    if now < issued_at or now >= expires_at:
        raise HumanAuthorityError("approval challenge is not currently valid")

    expected_confirmation = required_human_confirmation(challenge)
    if confirmation.strip() != expected_confirmation:
        raise HumanAuthorityError("human confirmation is not bound to this fresh challenge")

    private_key = _load_private_key(private_key_path)
    private_raw_public = _public_raw(private_key.public_key())
    if _fingerprint(private_raw_public) != authority.public_key_sha256:
        raise HumanAuthorityError("private key does not match registered human authority")

    data = {
        "authority_id": authority.authority_id,
        "publication_id": challenge["publication_id"],
        "package_hash": challenge["package_hash"],
        "challenge_hash": _challenge_hash(challenge),
        "challenge_nonce": challenge["nonce"],
        "challenge_issued_at": challenge["issued_at"],
        "challenge_expires_at": challenge["expires_at"],
        "confirmation": expected_confirmation,
        "authority_key_fingerprint": authority.public_key_sha256,
        "signed_at": _iso(now),
    }
    payload = _approval_payload(data)
    signature = private_key.sign(canonical_json(payload).encode("utf-8"))
    approval = SignedHumanApproval(
        **payload,
        signature_base64=base64.b64encode(signature).decode("ascii"),
    )
    _write_json(output_path.expanduser().resolve(), approval.model_dump(mode="json"))
    return approval


def verify_signed_approval(
    *,
    challenge_path: Path,
    approval_path: Path,
    authority_registry_path: Path,
    require_unexpired: bool = True,
) -> dict[str, Any]:
    challenge = _read_json(challenge_path.expanduser().resolve())
    approval = SignedHumanApproval.model_validate(_read_json(approval_path.expanduser().resolve()))
    authority = HumanAuthority.model_validate(_read_json(authority_registry_path.expanduser().resolve()))
    challenge_digest = _challenge_hash(challenge)
    expected_confirmation = required_human_confirmation(challenge)

    checks: dict[str, bool] = {
        "authority_active": authority.status == "ACTIVE",
        "authority_id_match": approval.authority_id == authority.authority_id,
        "authority_fingerprint_match": approval.authority_key_fingerprint == authority.public_key_sha256,
        "publication_id_match": approval.publication_id == challenge.get("publication_id"),
        "package_hash_match": approval.package_hash == challenge.get("package_hash"),
        "challenge_hash_match": approval.challenge_hash == challenge_digest,
        "challenge_nonce_match": approval.challenge_nonce == challenge.get("nonce"),
        "challenge_issued_at_match": approval.challenge_issued_at == challenge.get("issued_at"),
        "challenge_expires_at_match": approval.challenge_expires_at == challenge.get("expires_at"),
        "confirmation_match": approval.confirmation == expected_confirmation,
    }

    signed_at = _parse_iso(approval.signed_at)
    issued_at = _parse_iso(challenge["issued_at"])
    expires_at = _parse_iso(challenge["expires_at"])
    checks["signed_within_challenge_window"] = issued_at <= signed_at < expires_at
    if require_unexpired:
        checks["challenge_not_expired"] = _utc_now() < expires_at

    signature_ok = False
    try:
        signature = base64.b64decode(approval.signature_base64, validate=True)
        public_key = _load_public_key(authority)
        payload = _approval_payload(approval.model_dump(mode="json"))
        public_key.verify(signature, canonical_json(payload).encode("utf-8"))
        signature_ok = True
    except (InvalidSignature, ValueError, HumanAuthorityError):
        signature_ok = False
    checks["ed25519_signature_valid"] = signature_ok

    ok = all(checks.values())
    return {
        "schema": "marxiv.human-approval-verification.v2",
        "ok": ok,
        "authority_id": approval.authority_id,
        "publication_id": approval.publication_id,
        "package_hash": approval.package_hash,
        "challenge_hash": approval.challenge_hash,
        "checks": checks,
        "external_submission_authorized": False,
    }


def build_evidence_pack(
    *,
    challenge_path: Path,
    approval_path: Path,
    authority_registry_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    verification = verify_signed_approval(
        challenge_path=challenge_path,
        approval_path=approval_path,
        authority_registry_path=authority_registry_path,
    )
    if verification["ok"] is not True:
        raise HumanAuthorityError(f"cannot build evidence pack from invalid approval: {verification['checks']}")

    challenge = _read_json(challenge_path.expanduser().resolve())
    approval = _read_json(approval_path.expanduser().resolve())
    authority = _read_json(authority_registry_path.expanduser().resolve())
    pack = {
        "schema": "marxiv.human-approval-evidence-pack.v2",
        "publication_id": verification["publication_id"],
        "package_hash": verification["package_hash"],
        "challenge_hash": verification["challenge_hash"],
        "authority_id": verification["authority_id"],
        "authority_key_fingerprint": approval["authority_key_fingerprint"],
        "approval_verified": True,
        "external_side_effect": False,
        "submission_authorized": False,
        "objects": {
            "challenge": challenge,
            "approval": approval,
            "authority": authority,
            "verification": verification,
        },
    }
    pack["evidence_pack_hash"] = sha256_text(canonical_json(pack))
    _write_json(output_path.expanduser().resolve(), pack)
    return pack


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marxiv-human-authority")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--authority-id", required=True)
    init.add_argument("--private-key", required=True, type=Path)
    init.add_argument("--public-registry", required=True, type=Path)

    describe = sub.add_parser("describe")
    describe.add_argument("--challenge", required=True, type=Path)
    describe.add_argument("--output", type=Path)

    sign = sub.add_parser("sign")
    sign.add_argument("--challenge", required=True, type=Path)
    sign.add_argument("--private-key", required=True, type=Path)
    sign.add_argument("--authority-registry", required=True, type=Path)
    sign.add_argument("--output", required=True, type=Path)
    sign.add_argument("--confirm", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--challenge", required=True, type=Path)
    verify.add_argument("--approval", required=True, type=Path)
    verify.add_argument("--authority-registry", required=True, type=Path)
    verify.add_argument("--allow-expired", action="store_true")

    bundle = sub.add_parser("bundle")
    bundle.add_argument("--challenge", required=True, type=Path)
    bundle.add_argument("--approval", required=True, type=Path)
    bundle.add_argument("--authority-registry", required=True, type=Path)
    bundle.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "init":
        result = initialize_authority(
            authority_id=args.authority_id,
            private_key_path=args.private_key,
            public_registry_path=args.public_registry,
        ).model_dump(mode="json")
    elif args.command == "describe":
        result = describe_challenge(args.challenge)
        if args.output:
            _write_json(args.output, result)
    elif args.command == "sign":
        result = sign_challenge(
            challenge_path=args.challenge,
            private_key_path=args.private_key,
            authority_registry_path=args.authority_registry,
            output_path=args.output,
            confirmation=args.confirm,
        ).model_dump(mode="json")
    elif args.command == "verify":
        result = verify_signed_approval(
            challenge_path=args.challenge,
            approval_path=args.approval,
            authority_registry_path=args.authority_registry,
            require_unexpired=not args.allow_expired,
        )
        if result["ok"] is not True:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 2
    else:
        result = build_evidence_pack(
            challenge_path=args.challenge,
            approval_path=args.approval,
            authority_registry_path=args.authority_registry,
            output_path=args.output,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
