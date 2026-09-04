from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.evidence import canonical_json, evidence_receipt, sha256_text
from app.publication_bridge import (
    ArxivManifest,
    PublicationBridgeError,
    authorize_login,
    prepare as prepare_arxiv,
)

MARXIV_OBJECT_SCHEMA = "marxiv.scientific-object.v1"
MARXIV_PUBLISHER_SCHEMA = "marxiv.runtime-publisher.v1"
MARXIV_STATE_SCHEMA = "marxiv.publisher-state.v1"
MARXIV_APPROVAL_SCHEMA = "marxiv.human-approval.v1"
MARXIV_CHALLENGE_SCHEMA = "marxiv.approval-challenge.v1"
APPROVAL_SECRET_ENV = "MARXIV_APPROVAL_SECRET"
ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")


class MarxivPublisherError(RuntimeError):
    pass


class ArxivSubmissionError(MarxivPublisherError):
    def __init__(self, message: str, *, final_click_performed: bool = False) -> None:
        super().__init__(message)
        self.final_click_performed = final_click_performed


class MarxivAuthor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=256)
    orcid: str | None = Field(default=None, max_length=32)
    affiliation: str | None = Field(default=None, max_length=512)

    @field_validator("name", "orcid", "affiliation")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class ArxivTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: Literal["arxiv"] = "arxiv"
    primary_archive: str
    primary_category: str = Field(min_length=3)
    crosslist_archives: list[str] = Field(default_factory=list)
    crosslist_categories: list[str] = Field(default_factory=list)
    license: str
    keep_all_files: bool | None = None
    comments: str | None = None
    report_number: str | None = None
    journal_reference: str | None = None
    acm_class: str | None = None
    msc_class: str | None = None
    doi: str | None = None

    @model_validator(mode="after")
    def validate_crosslists(self) -> "ArxivTarget":
        if len(self.crosslist_archives) != len(self.crosslist_categories):
            raise ValueError("crosslist_archives and crosslist_categories must have equal lengths")
        return self


class MarxivScientificObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.scientific-object.v1"] = MARXIV_OBJECT_SCHEMA
    object_id: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    version: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    title: str = Field(min_length=3, max_length=512)
    authors: list[MarxivAuthor] = Field(min_length=1)
    abstract: str = Field(min_length=20)
    manuscript_file: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    parent_object_id: str | None = None
    publication: ArxivTarget

    @field_validator("keywords", "claims", "evidence_refs")
    @classmethod
    def normalize_string_list(cls, values: list[str]) -> list[str]:
        cleaned = [item.strip() for item in values if item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("duplicate values are not allowed")
        return cleaned


class PublisherState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.publisher-state.v1"] = MARXIV_STATE_SCHEMA
    object_id: str
    version: str
    publication_id: str
    venue: Literal["arxiv"] = "arxiv"
    status: Literal[
        "HUMAN_REVIEW_REQUIRED",
        "APPROVED",
        "SUBMITTING",
        "SUBMITTED_TO_ARXIV",
        "HOLD_PRE_SUBMIT",
        "HOLD_RECONCILIATION_REQUIRED",
        "RECONCILED",
        "BLOCK",
    ]
    object_hash: str
    manifest_hash: str
    manuscript_sha256: str
    arxiv_subfile_sha256: str
    review_packet_hash: str
    package_hash: str
    object_snapshot_path: str
    arxiv_manifest_path: str
    arxiv_state_path: str
    review_packet_path: str
    approval_path: str | None = None
    external_identifier: str | None = None
    receipt: dict[str, Any]


class ApprovalChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.approval-challenge.v1"] = MARXIV_CHALLENGE_SCHEMA
    publication_id: str
    package_hash: str
    object_hash: str
    manifest_hash: str
    manuscript_sha256: str
    arxiv_subfile_sha256: str
    review_packet_hash: str
    destination: Literal["arxiv"] = "arxiv"
    issued_at: str
    expires_at: str
    nonce: str


class HumanApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.human-approval.v1"] = MARXIV_APPROVAL_SCHEMA
    decision: Literal["APPROVE"] = "APPROVE"
    publication_id: str
    challenge_hash: str
    package_hash: str
    approver_id: str = Field(min_length=1, max_length=256)
    approved_at: str
    expires_at: str
    signature: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_object(path: Path) -> MarxivScientificObject:
    return MarxivScientificObject.model_validate(_read_json(path.resolve()))


def _resolve_manuscript(object_path: Path, manuscript_file: str) -> Path:
    candidate = Path(manuscript_file).expanduser()
    if not candidate.is_absolute():
        candidate = (object_path.resolve().parent / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.is_file():
        raise MarxivPublisherError(f"manuscript file not found: {candidate}")
    return candidate


def _publication_id(obj: MarxivScientificObject) -> str:
    return f"{obj.object_id}-{obj.version}-arxiv"


def build_arxiv_manifest(obj: MarxivScientificObject, manuscript: Path) -> ArxivManifest:
    target = obj.publication
    payload = {
        "schema": "matverse.publication-bridge.v1",
        "publication_id": _publication_id(obj),
        "manuscript_file": str(manuscript),
        "primary_archive": target.primary_archive,
        "primary_category": target.primary_category,
        "crosslist_archives": target.crosslist_archives,
        "crosslist_categories": target.crosslist_categories,
        "license": target.license,
        "title": obj.title,
        "authors": [author.name for author in obj.authors],
        "abstract": obj.abstract,
        "keep_all_files": target.keep_all_files,
        "comments": target.comments,
        "report_number": target.report_number,
        "journal_reference": target.journal_reference,
        "acm_class": target.acm_class,
        "msc_class": target.msc_class,
        "doi": target.doi,
    }
    return ArxivManifest.model_validate(payload)


def _package_hash(core: dict[str, str]) -> str:
    return sha256_text(canonical_json(core))


def prepare_sandbox(object_path: Path, sandbox_root: Path) -> PublisherState:
    object_path = object_path.resolve()
    obj = _load_object(object_path)
    manuscript = _resolve_manuscript(object_path, obj.manuscript_file)
    sandbox = (sandbox_root.resolve() / obj.object_id / obj.version).resolve()
    sandbox.mkdir(parents=True, exist_ok=True)

    object_snapshot = obj.model_dump(mode="json")
    object_hash = sha256_text(canonical_json(object_snapshot))
    manuscript_hash = _sha256_file(manuscript)
    object_snapshot_path = sandbox / "scientific-object.snapshot.json"
    _write_json(object_snapshot_path, object_snapshot)

    arxiv_manifest = build_arxiv_manifest(obj, manuscript)
    arxiv_manifest_path = sandbox / "arxiv-manifest.json"
    _write_json(arxiv_manifest_path, arxiv_manifest.model_dump(mode="json"))
    manifest_hash = sha256_text(canonical_json(arxiv_manifest.model_dump(mode="json")))

    bridge_state = prepare_arxiv(arxiv_manifest_path, sandbox / "transport")
    arxiv_state_path = sandbox / "transport" / bridge_state.publication_id / "publication-state.json"
    subfile_path = Path(bridge_state.subfile_path)
    subfile_hash = _sha256_file(subfile_path)

    review_packet = {
        "schema": "marxiv.review-packet.v1",
        "object_id": obj.object_id,
        "version": obj.version,
        "title": obj.title,
        "authors": [author.model_dump(mode="json") for author in obj.authors],
        "abstract": obj.abstract,
        "keywords": obj.keywords,
        "claims": obj.claims,
        "evidence_refs": obj.evidence_refs,
        "lineage": {"parent_object_id": obj.parent_object_id},
        "destination": "arxiv",
        "publication_metadata": arxiv_manifest.model_dump(mode="json"),
        "artifacts": {
            "manuscript_sha256": manuscript_hash,
            "object_hash": object_hash,
            "publication_manifest_hash": manifest_hash,
            "arxiv_subfile_sha256": subfile_hash,
        },
        "human_decision_required": True,
    }
    review_packet_path = sandbox / "review-packet.json"
    _write_json(review_packet_path, review_packet)
    review_packet_hash = sha256_text(canonical_json(review_packet))

    core = {
        "object_hash": object_hash,
        "manifest_hash": manifest_hash,
        "manuscript_sha256": manuscript_hash,
        "arxiv_subfile_sha256": subfile_hash,
        "review_packet_hash": review_packet_hash,
        "destination": "arxiv",
    }
    package_hash = _package_hash(core)
    receipt = evidence_receipt(
        "MARXIV_PUBLICATION_SANDBOX_PREPARED",
        {"object_id": obj.object_id, "version": obj.version},
        {**core, "package_hash": package_hash, "status": "HUMAN_REVIEW_REQUIRED"},
    )
    state = PublisherState(
        object_id=obj.object_id,
        version=obj.version,
        publication_id=bridge_state.publication_id,
        status="HUMAN_REVIEW_REQUIRED",
        object_hash=object_hash,
        manifest_hash=manifest_hash,
        manuscript_sha256=manuscript_hash,
        arxiv_subfile_sha256=subfile_hash,
        review_packet_hash=review_packet_hash,
        package_hash=package_hash,
        object_snapshot_path=str(object_snapshot_path),
        arxiv_manifest_path=str(arxiv_manifest_path),
        arxiv_state_path=str(arxiv_state_path),
        review_packet_path=str(review_packet_path),
        receipt=receipt,
    )
    _write_json(sandbox / "publisher-state.json", state.model_dump(mode="json"))
    return state


def _load_state(sandbox: Path) -> PublisherState:
    return PublisherState.model_validate(_read_json(sandbox.resolve() / "publisher-state.json"))


def _save_state(sandbox: Path, state: PublisherState) -> None:
    _write_json(sandbox.resolve() / "publisher-state.json", state.model_dump(mode="json"))


def _current_package_checks(state: PublisherState) -> dict[str, bool]:
    object_snapshot_path = Path(state.object_snapshot_path)
    manifest_path = Path(state.arxiv_manifest_path)
    arxiv_state_path = Path(state.arxiv_state_path)
    review_path = Path(state.review_packet_path)
    if not object_snapshot_path.is_file() or not manifest_path.is_file() or not arxiv_state_path.is_file() or not review_path.is_file():
        return {
            "object_snapshot_exists": object_snapshot_path.is_file(),
            "manifest_exists": manifest_path.is_file(),
            "arxiv_state_exists": arxiv_state_path.is_file(),
            "review_packet_exists": review_path.is_file(),
        }
    manifest = ArxivManifest.model_validate(_read_json(manifest_path))
    manuscript = Path(manifest.manuscript_file).resolve()
    if not manuscript.is_file():
        return {"manuscript_exists": False}
    bridge_state = _read_json(arxiv_state_path)
    subfile = Path(bridge_state["subfile_path"])
    checks = {
        "object_hash_match": sha256_text(canonical_json(_read_json(object_snapshot_path))) == state.object_hash,
        "manifest_hash_match": sha256_text(canonical_json(manifest.model_dump(mode="json"))) == state.manifest_hash,
        "manuscript_hash_match": _sha256_file(manuscript) == state.manuscript_sha256,
        "review_packet_hash_match": sha256_text(canonical_json(_read_json(review_path))) == state.review_packet_hash,
        "arxiv_subfile_exists": subfile.is_file(),
    }
    if subfile.is_file():
        checks["arxiv_subfile_hash_match"] = _sha256_file(subfile) == state.arxiv_subfile_sha256
    return checks


def request_approval(sandbox: Path, ttl_seconds: int = 7200) -> ApprovalChallenge:
    sandbox = sandbox.resolve()
    state = _load_state(sandbox)
    checks = _current_package_checks(state)
    if not checks or not all(checks.values()):
        raise MarxivPublisherError(f"sandbox integrity check failed: {checks}")
    if state.status != "HUMAN_REVIEW_REQUIRED":
        raise MarxivPublisherError(f"approval cannot be requested from status {state.status}")
    now = _utc_now()
    challenge = ApprovalChallenge(
        publication_id=state.publication_id,
        package_hash=state.package_hash,
        object_hash=state.object_hash,
        manifest_hash=state.manifest_hash,
        manuscript_sha256=state.manuscript_sha256,
        arxiv_subfile_sha256=state.arxiv_subfile_sha256,
        review_packet_hash=state.review_packet_hash,
        issued_at=_iso(now),
        expires_at=_iso(now + timedelta(seconds=ttl_seconds)),
        nonce=secrets.token_hex(16),
    )
    _write_json(sandbox / "approval-challenge.json", challenge.model_dump(mode="json"))
    return challenge


def _approval_secret() -> bytes:
    value = os.getenv(APPROVAL_SECRET_ENV)
    if not value or len(value.encode("utf-8")) < 32:
        raise MarxivPublisherError(f"{APPROVAL_SECRET_ENV} must be set locally to at least 32 bytes")
    return value.encode("utf-8")


def _approval_signature(challenge_hash: str, package_hash: str, approver_id: str, expires_at: str) -> str:
    payload = canonical_json(
        {
            "challenge_hash": challenge_hash,
            "package_hash": package_hash,
            "approver_id": approver_id,
            "expires_at": expires_at,
            "decision": "APPROVE",
        }
    )
    return hmac.new(_approval_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def required_confirmation(challenge: ApprovalChallenge) -> str:
    return f"PUBLISH {challenge.publication_id} {challenge.package_hash[:12]}"


def approve(sandbox: Path, approver_id: str, confirmation: str) -> HumanApproval:
    sandbox = sandbox.resolve()
    state = _load_state(sandbox)
    challenge_path = sandbox / "approval-challenge.json"
    if not challenge_path.is_file():
        raise MarxivPublisherError("approval challenge missing; run request-approval first")
    challenge = ApprovalChallenge.model_validate(_read_json(challenge_path))
    if confirmation.strip() != required_confirmation(challenge):
        raise MarxivPublisherError("human confirmation phrase does not match the prepared publication package")
    if _utc_now() >= _parse_iso(challenge.expires_at):
        raise MarxivPublisherError("approval challenge expired")
    if challenge.package_hash != state.package_hash or challenge.publication_id != state.publication_id:
        raise MarxivPublisherError("approval challenge is not bound to the current sandbox")
    checks = _current_package_checks(state)
    if not checks or not all(checks.values()):
        raise MarxivPublisherError(f"sandbox changed after preparation: {checks}")

    challenge_hash = sha256_text(canonical_json(challenge.model_dump(mode="json")))
    approved_at = _iso(_utc_now())
    signature = _approval_signature(challenge_hash, state.package_hash, approver_id, challenge.expires_at)
    approval = HumanApproval(
        publication_id=state.publication_id,
        challenge_hash=challenge_hash,
        package_hash=state.package_hash,
        approver_id=approver_id,
        approved_at=approved_at,
        expires_at=challenge.expires_at,
        signature=signature,
    )
    approval_path = sandbox / "human-approval.json"
    _write_json(approval_path, approval.model_dump(mode="json"))
    receipt = evidence_receipt(
        "MARXIV_HUMAN_PUBLICATION_APPROVAL",
        {"prior_receipt_hash": state.receipt["receipt_hash"], "package_hash": state.package_hash},
        {"status": "APPROVED", "approver_id": approver_id, "challenge_hash": challenge_hash},
    )
    updated = state.model_copy(update={"status": "APPROVED", "approval_path": str(approval_path), "receipt": receipt})
    _save_state(sandbox, updated)
    return approval


def verify_approval(sandbox: Path) -> dict[str, Any]:
    sandbox = sandbox.resolve()
    state = _load_state(sandbox)
    if not state.approval_path:
        return {"ok": False, "reason": "approval_missing"}
    approval_path = Path(state.approval_path)
    challenge_path = sandbox / "approval-challenge.json"
    if not approval_path.is_file() or not challenge_path.is_file():
        return {"ok": False, "reason": "approval_or_challenge_file_missing"}
    approval = HumanApproval.model_validate(_read_json(approval_path))
    challenge = ApprovalChallenge.model_validate(_read_json(challenge_path))
    challenge_hash = sha256_text(canonical_json(challenge.model_dump(mode="json")))
    expected = _approval_signature(challenge_hash, state.package_hash, approval.approver_id, approval.expires_at)
    checks = {
        "status_approved": state.status == "APPROVED",
        "challenge_hash_match": approval.challenge_hash == challenge_hash,
        "package_hash_match": approval.package_hash == state.package_hash == challenge.package_hash,
        "publication_id_match": approval.publication_id == state.publication_id == challenge.publication_id,
        "signature_match": hmac.compare_digest(approval.signature, expected),
        "not_expired": _utc_now() < _parse_iso(approval.expires_at),
    }
    checks.update({f"package_{key}": value for key, value in _current_package_checks(state).items()})
    return {"ok": all(checks.values()), "checks": checks}


def _click_continue(page) -> None:
    for role, name in (("button", "Save and Continue"), ("button", "Continue"), ("link", "Continue")):
        locator = page.get_by_role(role, name=name)
        if locator.count():
            locator.last.click()
            return
    raise ArxivSubmissionError("could not find a Continue control before final arXiv review")


def _execute_arxiv_submission(manifest: ArxivManifest, *, headless: bool = False) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
        from paperpush.venues.arxiv.arxiv import ArxivVenue, license_url
        from paperpush.venues.common import apply_default_timeouts, open_run_context, _try
    except ImportError as exc:
        raise MarxivPublisherError("publication dependencies missing; install requirements-publication.txt") from exc

    final_click_performed = False
    venue = ArxivVenue()
    license_uri = license_url(manifest.license)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = open_run_context(browser, venue.session_path(), new_session=False)
            apply_default_timeouts(context, 120.0)
            page = context.new_page()
            venue.ensure_signed_in(page, context, debug=False)
            page.get_by_role("link", name="START NEW SUBMISSION").click()

            page.locator('input[name="userinfo"]').check()
            terms_checkbox = page.locator('input[name="agree_terms_conditions"]')
            terms_checkbox.click()
            terms = page.locator("#modal-1-content")
            terms.evaluate("(el) => el.scrollTop = el.scrollHeight")
            page.get_by_role("button", name="Accept these Terms and return").click()

            page.get_by_role("radio").first.check()
            radio = page.locator(f'input[name="license"][value="{license_uri}"]')
            if radio.count() == 0:
                radio = page.locator(f'input[name="license"][value$="{license_uri.split("://", 1)[1]}"]')
            radio.check()
            page.locator("#select_arch").select_option(manifest.primary_archive)
            page.locator("#select_sc").select_option(manifest.primary_category)
            page.get_by_role("button", name="Continue").nth(1).click()

            manuscript = str(Path(manifest.manuscript_file).resolve())
            with page.expect_file_chooser() as chooser_info:
                page.get_by_role("button", name="Choose File").click()
            chooser_info.value.set_files(manuscript)
            page.get_by_role("button", name="Upload").click()
            page.wait_for_timeout(3_000)
            page.locator("#check-files-button-top").click()
            if manuscript.lower().endswith((".tex", ".zip", ".tar.gz", ".tgz")):
                page.wait_for_timeout(3_000)
                keep_all_files = True if manifest.keep_all_files is None else manifest.keep_all_files
                if keep_all_files:
                    _try(lambda: page.get_by_role("link", name="Keep All").click(), "Keep All")
                    _try(lambda: page.locator("#check-files-button-top").click(timeout=120_000), "Accept and Continue")
                else:
                    _try(lambda: page.locator("#check-files-button-top").click(), "Accept and Continue")
                    _try(lambda: page.get_by_role("button", name="Confirm").click(), "Confirm")
                page.wait_for_timeout(3_000)
            page.get_by_role("link", name="Continue").click()

            page.get_by_role("textbox", name="*Title").fill(manifest.title)
            page.get_by_role("textbox", name="*Author(s)").fill(", ".join(manifest.authors))
            page.locator('textarea[name="abstract"]').fill(manifest.abstract)
            optional_fields = {
                'textarea[name="comments"]': manifest.comments,
                'input[name="report_num"]': manifest.report_number,
                'input[name="journal_ref"]': manifest.journal_reference,
                'input[name="acm_class"]': manifest.acm_class,
                'input[name="msc_class"]': manifest.msc_class,
                'input[name="doi"]': manifest.doi,
            }
            for selector, value in optional_fields.items():
                if value:
                    page.locator(selector).fill(value)
            page.get_by_role("button", name="Save and Continue").first.click()

            if manifest.crosslist_archives:
                for archive, category in zip(manifest.crosslist_archives, manifest.crosslist_categories):
                    page.locator("#select_arch").select_option(archive)
                    page.locator("#select_sc").select_option(category)
                    _click_continue(page)

            submit_button = page.get_by_role("button", name="Submit Article")
            if submit_button.count() == 0:
                submit_button = page.locator('input[value="Submit Article"]')
            if submit_button.count() == 0:
                raise ArxivSubmissionError("final arXiv 'Submit Article' control not found")
            submit_button.first.click()
            final_click_performed = True
            page.wait_for_load_state("networkidle")

            page.goto(venue.USER_URL)
            page.wait_for_load_state("domcontentloaded")
            body = page.locator("body").inner_text()
            confirmed = manifest.title in body and "Unsubmit" in body
            result = {
                "final_click_performed": True,
                "portal_confirmation_observed": confirmed,
                "user_page_url": page.url,
                "external_identifier": None,
            }
            context.close()
            browser.close()
            return result
    except Exception as exc:
        if isinstance(exc, ArxivSubmissionError):
            if final_click_performed and not exc.final_click_performed:
                exc.final_click_performed = True
            raise
        raise ArxivSubmissionError(str(exc), final_click_performed=final_click_performed) from exc


def publish(
    sandbox: Path,
    *,
    headless: bool = False,
    transport: Callable[[ArxivManifest], dict[str, Any]] | None = None,
) -> PublisherState:
    sandbox = sandbox.resolve()
    state = _load_state(sandbox)
    verification = verify_approval(sandbox)
    if not verification.get("ok"):
        raise MarxivPublisherError(f"publication blocked: valid human approval required: {verification}")
    if state.status != "APPROVED":
        raise MarxivPublisherError(f"publication blocked from status {state.status}")

    manifest = ArxivManifest.model_validate(_read_json(Path(state.arxiv_manifest_path)))
    workdir = Path(state.arxiv_state_path).parent
    authorize_login(workdir)
    submitting_receipt = evidence_receipt(
        "MARXIV_PUBLICATION_SUBMITTING",
        {"prior_receipt_hash": state.receipt["receipt_hash"], "package_hash": state.package_hash},
        {"status": "SUBMITTING", "destination": "arxiv"},
    )
    state = state.model_copy(update={"status": "SUBMITTING", "receipt": submitting_receipt})
    _save_state(sandbox, state)

    try:
        result = transport(manifest) if transport is not None else _execute_arxiv_submission(manifest, headless=headless)
    except ArxivSubmissionError as exc:
        status = "HOLD_RECONCILIATION_REQUIRED" if exc.final_click_performed else "HOLD_PRE_SUBMIT"
        receipt = evidence_receipt(
            "MARXIV_PUBLICATION_HOLD",
            {"prior_receipt_hash": state.receipt["receipt_hash"], "package_hash": state.package_hash},
            {"status": status, "final_click_performed": exc.final_click_performed},
        )
        held = state.model_copy(update={"status": status, "receipt": receipt})
        _save_state(sandbox, held)
        raise
    except Exception as exc:
        receipt = evidence_receipt(
            "MARXIV_PUBLICATION_HOLD",
            {"prior_receipt_hash": state.receipt["receipt_hash"], "package_hash": state.package_hash},
            {"status": "HOLD_PRE_SUBMIT", "final_click_performed": False},
        )
        held = state.model_copy(update={"status": "HOLD_PRE_SUBMIT", "receipt": receipt})
        _save_state(sandbox, held)
        raise MarxivPublisherError(str(exc)) from exc

    if not result.get("final_click_performed"):
        raise MarxivPublisherError("transport returned without performing the final arXiv submission action")
    status = "SUBMITTED_TO_ARXIV" if result.get("portal_confirmation_observed") else "HOLD_RECONCILIATION_REQUIRED"
    receipt = evidence_receipt(
        "MARXIV_ARXIV_SUBMISSION_ACTION",
        {"prior_receipt_hash": state.receipt["receipt_hash"], "package_hash": state.package_hash},
        {
            "status": status,
            "final_click_performed": True,
            "portal_confirmation_observed": bool(result.get("portal_confirmation_observed")),
        },
    )
    updated = state.model_copy(update={"status": status, "receipt": receipt})
    _save_state(sandbox, updated)
    _write_json(sandbox / "submission-result.json", result)
    return updated


def reconcile(sandbox: Path, arxiv_id: str) -> PublisherState:
    sandbox = sandbox.resolve()
    state = _load_state(sandbox)
    if state.status not in {"SUBMITTED_TO_ARXIV", "HOLD_RECONCILIATION_REQUIRED"}:
        raise MarxivPublisherError(f"cannot reconcile arXiv identifier from status {state.status}")
    if not ARXIV_ID_RE.fullmatch(arxiv_id.strip()):
        raise MarxivPublisherError("invalid arXiv identifier format")
    identifier = arxiv_id.strip()
    receipt = evidence_receipt(
        "MARXIV_PUBLICATION_RECONCILED",
        {"prior_receipt_hash": state.receipt["receipt_hash"], "package_hash": state.package_hash},
        {"status": "RECONCILED", "arxiv_id": identifier},
    )
    updated = state.model_copy(update={"status": "RECONCILED", "external_identifier": identifier, "receipt": receipt})
    _save_state(sandbox, updated)
    return updated


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marxiv-runtime-publisher")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--object", required=True, type=Path)
    p_prepare.add_argument("--sandbox-root", default=Path(".marxiv"), type=Path)

    p_request = sub.add_parser("request-approval")
    p_request.add_argument("--sandbox", required=True, type=Path)
    p_request.add_argument("--ttl-seconds", type=int, default=7200)

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("--sandbox", required=True, type=Path)
    p_approve.add_argument("--approver", required=True)
    p_approve.add_argument("--confirm", required=True)

    p_verify = sub.add_parser("verify-approval")
    p_verify.add_argument("--sandbox", required=True, type=Path)

    p_publish = sub.add_parser("publish")
    p_publish.add_argument("--sandbox", required=True, type=Path)
    p_publish.add_argument("--headless", action="store_true")

    p_reconcile = sub.add_parser("reconcile")
    p_reconcile.add_argument("--sandbox", required=True, type=Path)
    p_reconcile.add_argument("--arxiv-id", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "prepare":
        result = prepare_sandbox(args.object, args.sandbox_root)
        print(canonical_json(result.model_dump(mode="json")))
        return 0
    if args.command == "request-approval":
        challenge = request_approval(args.sandbox, args.ttl_seconds)
        print(canonical_json({"challenge": challenge.model_dump(mode="json"), "required_confirmation": required_confirmation(challenge)}))
        return 0
    if args.command == "approve":
        result = approve(args.sandbox, args.approver, args.confirm)
        print(canonical_json(result.model_dump(mode="json")))
        return 0
    if args.command == "verify-approval":
        result = verify_approval(args.sandbox)
        print(canonical_json(result))
        return 0 if result.get("ok") else 2
    if args.command == "publish":
        result = publish(args.sandbox, headless=args.headless)
        print(canonical_json(result.model_dump(mode="json")))
        return 0 if result.status == "SUBMITTED_TO_ARXIV" else 3
    if args.command == "reconcile":
        result = reconcile(args.sandbox, args.arxiv_id)
        print(canonical_json(result.model_dump(mode="json")))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
