from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from app.marxiv_runtime_publisher import (
    approve,
    prepare_sandbox,
    request_approval,
    required_confirmation,
    verify_approval,
)


class ApprovalSealError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seal_approval(
    *,
    approval_intent_path: Path,
    scientific_object_path: Path,
    sandbox_root: Path,
    approver_id: str,
    evidence_dir: Path,
    ttl_seconds: int = 7200,
) -> dict[str, Any]:
    approval_intent_path = approval_intent_path.resolve()
    scientific_object_path = scientific_object_path.resolve()
    sandbox_root = sandbox_root.resolve()
    evidence_dir = evidence_dir.resolve()

    intent = _read_json(approval_intent_path)
    if intent.get("schema") != "marxiv.approval-intent.v1":
        raise ApprovalSealError("unsupported approval intent schema")
    if intent.get("decision") != "APPROVE_PACKAGE_INTENT":
        raise ApprovalSealError("approval intent decision is not APPROVE_PACKAGE_INTENT")
    if intent.get("human_confirmation") != "CONFIRMED":
        raise ApprovalSealError("human approval intent is not confirmed")
    if intent.get("authority_scope") != "APPROVE_EXACT_PACKAGE_ONLY":
        raise ApprovalSealError("approval intent authority scope is not package-bound")
    if intent.get("external_submission_authorized") is not False:
        raise ApprovalSealError("approval intent must not authorize external submission")
    if intent.get("arxiv_login_authorized") is not False:
        raise ApprovalSealError("approval intent must not authorize arXiv login")
    if intent.get("browser_submit_authorized") is not False:
        raise ApprovalSealError("approval intent must not authorize browser submit")

    state = prepare_sandbox(scientific_object_path, sandbox_root)
    if state.status != "HUMAN_REVIEW_REQUIRED":
        raise ApprovalSealError(f"unexpected prepared state: {state.status}")
    if state.package_hash != intent.get("package_hash"):
        raise ApprovalSealError("reconstructed package hash does not match approval intent")
    if state.publication_id != intent.get("publication_id"):
        raise ApprovalSealError("reconstructed publication id does not match approval intent")

    sandbox = sandbox_root / state.object_id / state.version
    challenge = request_approval(sandbox, ttl_seconds=ttl_seconds)
    confirmation = required_confirmation(challenge)
    if confirmation != intent.get("required_runtime_confirmation"):
        raise ApprovalSealError("fresh challenge confirmation does not match tracked approval intent")

    approval = approve(
        sandbox,
        approver_id=approver_id,
        confirmation=confirmation,
    )
    verification = verify_approval(sandbox)
    if verification.get("ok") is not True:
        raise ApprovalSealError(f"cryptographic approval verification failed: {verification}")

    publisher_state_path = sandbox / "publisher-state.json"
    publisher_state = _read_json(publisher_state_path)
    if publisher_state.get("status") != "APPROVED":
        raise ApprovalSealError("publisher state did not transition to APPROVED")
    if publisher_state.get("external_identifier") is not None:
        raise ApprovalSealError("external identifier appeared during approval-only ceremony")
    if (sandbox / "submission-result.json").exists():
        raise ApprovalSealError("external submission artifact appeared during approval-only ceremony")

    evidence_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(approval_intent_path, evidence_dir / "approval-intent.json")
    shutil.copyfile(sandbox / "approval-challenge.json", evidence_dir / "approval-challenge.json")
    shutil.copyfile(sandbox / "human-approval.json", evidence_dir / "human-approval.json")
    shutil.copyfile(publisher_state_path, evidence_dir / "publisher-state.json")
    _write_json(evidence_dir / "approval-verification.json", verification)

    summary = {
        "schema": "marxiv.approval-seal-evidence.v1",
        "object_id": state.object_id,
        "version": state.version,
        "publication_id": state.publication_id,
        "package_hash": state.package_hash,
        "status": "APPROVED",
        "approval_verified": True,
        "approver_id": approval.approver_id,
        "challenge_hash": approval.challenge_hash,
        "external_side_effect": False,
        "external_identifier": None,
        "secret_persisted": False,
        "submission_authorized": False,
    }
    _write_json(evidence_dir / "summary.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marxiv-approval-seal")
    parser.add_argument("--intent", required=True, type=Path)
    parser.add_argument("--object", required=True, type=Path)
    parser.add_argument("--sandbox-root", required=True, type=Path)
    parser.add_argument("--approver", required=True)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--ttl-seconds", type=int, default=7200)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = seal_approval(
        approval_intent_path=args.intent,
        scientific_object_path=args.object,
        sandbox_root=args.sandbox_root,
        approver_id=args.approver,
        evidence_dir=args.evidence_dir,
        ttl_seconds=args.ttl_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
