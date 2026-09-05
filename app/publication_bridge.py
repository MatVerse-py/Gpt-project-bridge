from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.evidence import canonical_json, evidence_receipt, sha256_text


ARXIV_LICENSES = (
    "arXiv.org perpetual, non-exclusive license 1.0",
    "CC BY 4.0",
    "CC BY-SA 4.0",
    "CC BY-NC-SA 4.0",
    "CC BY-NC-ND 4.0",
    "CC0 1.0",
)
ARXIV_ARCHIVES = {
    "astro-ph",
    "cond-mat",
    "cs",
    "econ",
    "eess",
    "gr-qc",
    "hep-ex",
    "hep-lat",
    "hep-ph",
    "hep-th",
    "math",
    "math-ph",
    "nlin",
    "nucl-ex",
    "nucl-th",
    "physics",
    "q-bio",
    "q-fin",
    "quant-ph",
    "stat",
}
SECRET_MARKERS = ("password", "passwd", "secret", "token", "api_key", "credential")
PUBLICATION_SCHEMA = "matverse.publication-bridge.v1"


class PublicationBridgeError(RuntimeError):
    pass


class ArxivManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["matverse.publication-bridge.v1"] = PUBLICATION_SCHEMA
    publication_id: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    manuscript_file: str = Field(min_length=1)
    primary_archive: str
    primary_category: str = Field(min_length=3)
    crosslist_archives: list[str] = Field(default_factory=list)
    crosslist_categories: list[str] = Field(default_factory=list)
    license: Literal[
        "arXiv.org perpetual, non-exclusive license 1.0",
        "CC BY 4.0",
        "CC BY-SA 4.0",
        "CC BY-NC-SA 4.0",
        "CC BY-NC-ND 4.0",
        "CC0 1.0",
    ]
    title: str = Field(min_length=3, max_length=512)
    authors: list[str] = Field(min_length=1)
    abstract: str = Field(min_length=20)
    keep_all_files: bool | None = None
    comments: str | None = None
    report_number: str | None = None
    journal_reference: str | None = None
    acm_class: str | None = None
    msc_class: str | None = None
    doi: str | None = None

    @field_validator("primary_archive")
    @classmethod
    def validate_primary_archive(cls, value: str) -> str:
        if value not in ARXIV_ARCHIVES:
            raise ValueError(f"unsupported arXiv archive: {value}")
        return value

    @field_validator("crosslist_archives")
    @classmethod
    def validate_crosslist_archives(cls, values: list[str]) -> list[str]:
        invalid = sorted(set(values) - ARXIV_ARCHIVES)
        if invalid:
            raise ValueError(f"unsupported arXiv cross-list archive(s): {', '.join(invalid)}")
        return values

    @field_validator("authors")
    @classmethod
    def validate_authors(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("author names must be non-empty")
        return cleaned

    @model_validator(mode="after")
    def validate_crosslists(self) -> "ArxivManifest":
        if len(self.crosslist_archives) != len(self.crosslist_categories):
            raise ValueError("crosslist_archives and crosslist_categories must have equal lengths")
        return self


class PublicationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["matverse.publication-state.v1"] = "matverse.publication-state.v1"
    publication_id: str
    venue: Literal["arxiv"] = "arxiv"
    status: Literal[
        "VALIDATED",
        "AUTH_REQUIRED",
        "READY_FOR_AUTHOR_REVIEW",
        "AUTHOR_REVIEW_SESSION_COMPLETED",
        "HOLD",
        "BLOCK",
    ]
    manifest_hash: str
    manuscript_sha256: str
    subfile_path: str
    values_path: str
    receipt: dict[str, Any]


def _redact(text: str) -> str:
    redacted = text
    for env_name in ("ARXIV_EMAIL", "ARXIV_PASSWORD", "PAPERPUSH_USERNAME", "PAPERPUSH_PASSWORD"):
        value = os.getenv(env_name)
        if value:
            redacted = redacted.replace(value, "<redacted>")
    return redacted


def _assert_secret_free(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in SECRET_MARKERS):
                raise PublicationBridgeError(f"secret-like field forbidden in publication manifest: {path}.{key}")
            _assert_secret_free(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_secret_free(child, f"{path}[{index}]")


def _load_manifest(path: Path) -> ArxivManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    _assert_secret_free(raw)
    return ArxivManifest.model_validate(raw)


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_manuscript(manifest_path: Path, manuscript_file: str) -> Path:
    candidate = Path(manuscript_file).expanduser()
    if not candidate.is_absolute():
        candidate = (manifest_path.parent / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.is_file():
        raise PublicationBridgeError(f"manuscript file not found: {candidate}")
    return candidate


def _paperpush_bin() -> str:
    explicit = os.getenv("PAPERPUSH_BIN")
    if explicit:
        return explicit
    located = shutil.which("paperpush")
    if not located:
        raise PublicationBridgeError(
            "paperpush executable not found; install requirements-publication.txt and run `playwright install chromium`"
        )
    return located


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    interactive: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [_paperpush_bin(), *args]
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=not interactive,
        check=False,
    )
    if completed.returncode != 0:
        stderr = _redact(completed.stderr or "")
        stdout = _redact(completed.stdout or "")
        raise PublicationBridgeError(
            f"paperpush command failed (exit={completed.returncode}): {' '.join(args)}\n{stdout}\n{stderr}".strip()
        )
    return completed


def _field(field_id: str, value: Any, source: str = "publication manifest") -> dict[str, Any]:
    return {"id": field_id, "value": value, "confidence": "high", "source": source}


def build_values(manifest: ArxivManifest, manuscript_path: Path) -> dict[str, Any]:
    # PaperPush receives manuscript_path.parent through --data-dir/-d, so the
    # manuscript field only needs the basename. Persisting the resolved absolute
    # path here would leak host/sandbox paths into arxiv.sub and make an otherwise
    # identical publication package hash differently across runtimes.
    stable_manuscript_name = manuscript_path.name
    fields: list[dict[str, Any]] = [
        _field("primary_archive", manifest.primary_archive),
        _field("primary_category", manifest.primary_category),
        _field("license", manifest.license, "explicit author choice in publication manifest"),
        _field("title", manifest.title),
        _field("authors", "\n".join(manifest.authors)),
        _field("abstract", manifest.abstract),
        _field("manuscript_file", stable_manuscript_name, "filename relative to PaperPush data directory"),
    ]
    if manifest.crosslist_archives:
        fields.append(_field("crosslist_archives", ", ".join(manifest.crosslist_archives)))
        fields.append(_field("crosslist_categories", ", ".join(manifest.crosslist_categories)))
    if manifest.keep_all_files is not None:
        fields.append(_field("keep_all_files", "yes" if manifest.keep_all_files else "no"))
    for field_id in ("comments", "report_number", "journal_reference", "acm_class", "msc_class", "doi"):
        value = getattr(manifest, field_id)
        if value:
            fields.append(_field(field_id, value))
    return {
        "fields": fields,
        "unfilled": [
            {
                "id": "final_submission_confirmation",
                "reason": "arXiv expects self-submission; the author must review the portal and perform the final submission action",
            }
        ],
    }


def _write_state(path: Path, state: PublicationState) -> None:
    path.write_text(canonical_json(state.model_dump(mode="json")) + "\n", encoding="utf-8")


def prepare(manifest_path: Path, work_root: Path) -> PublicationState:
    manifest_path = manifest_path.resolve()
    manifest = _load_manifest(manifest_path)
    manuscript_path = _resolve_manuscript(manifest_path, manifest.manuscript_file)
    workdir = (work_root.resolve() / manifest.publication_id)
    workdir.mkdir(parents=True, exist_ok=True)

    manifest_snapshot = manifest.model_dump(mode="json")
    _assert_secret_free(manifest_snapshot)
    manifest_hash = sha256_text(canonical_json(manifest_snapshot))
    manuscript_hash = _sha256_file(manuscript_path)

    values = build_values(manifest, manuscript_path)
    values_path = workdir / "values.json"
    values_path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (workdir / "manifest.snapshot.json").write_text(
        json.dumps(manifest_snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    subfile_path = workdir / "arxiv.sub"
    if subfile_path.exists():
        subfile_path.unlink()
    _run(["subfile", "arxiv"], cwd=workdir)
    if not subfile_path.is_file():
        raise PublicationBridgeError("paperpush did not create arxiv.sub")

    # PaperPush's filemap resolver serializes manuscript_dir / filename into the
    # .sub file. Supplying an absolute -d therefore makes an otherwise identical
    # transport artifact host-path-dependent. Keep -d relative to the PaperPush
    # cwd; its relative relationship to the staged manuscript is stable across
    # sandbox roots while still resolving to the same bytes.
    paperpush_data_dir = os.path.relpath(manuscript_path.parent, start=workdir)
    _run(
        [
            "autofill",
            str(subfile_path),
            "-d",
            paperpush_data_dir,
            "--engine",
            "manual",
            "--values",
            str(values_path),
        ],
        cwd=workdir,
    )
    _run(["validate", str(subfile_path)], cwd=workdir)

    outputs = {
        "status": "VALIDATED",
        "publication_id": manifest.publication_id,
        "manifest_hash": manifest_hash,
        "manuscript_sha256": manuscript_hash,
        "subfile_sha256": _sha256_file(subfile_path),
        "values_sha256": _sha256_file(values_path),
    }
    receipt = evidence_receipt(
        "PUBLICATION_ARXIV_PREPARED",
        {"manifest_hash": manifest_hash, "manuscript_sha256": manuscript_hash},
        outputs,
    )
    state = PublicationState(
        publication_id=manifest.publication_id,
        status="VALIDATED",
        manifest_hash=manifest_hash,
        manuscript_sha256=manuscript_hash,
        subfile_path=str(subfile_path),
        values_path=str(values_path),
        receipt=receipt,
    )
    _write_state(workdir / "publication-state.json", state)
    return state


def login_status(workdir: Path) -> str:
    result = _run(["login", "arxiv", "--status"], cwd=workdir.resolve())
    return _redact(result.stdout or "").strip()


def authorize_login(workdir: Path, *, allow_interactive_fallback: bool = False) -> None:
    email = os.getenv("ARXIV_EMAIL")
    password = os.getenv("ARXIV_PASSWORD")
    if bool(email) != bool(password):
        raise PublicationBridgeError("ARXIV_EMAIL and ARXIV_PASSWORD must be supplied together")

    env = os.environ.copy()
    if email and password:
        env["PAPERPUSH_USERNAME"] = email
        env["PAPERPUSH_PASSWORD"] = password
        _run(["login", "arxiv"], cwd=workdir.resolve(), env=env, interactive=True)
        return

    if not allow_interactive_fallback:
        raise PublicationBridgeError(
            "no arXiv credentials in environment; set ARXIV_EMAIL and ARXIV_PASSWORD locally, "
            "or rerun with --interactive-login"
        )
    _run(["login", "arxiv"], cwd=workdir.resolve(), interactive=True)


def open_author_review(state_path: Path) -> PublicationState:
    state_path = state_path.resolve()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    _assert_secret_free(raw)
    state = PublicationState.model_validate(raw)
    workdir = state_path.parent
    subfile = Path(state.subfile_path)
    if not subfile.is_file():
        raise PublicationBridgeError(f"arXiv subfile missing: {subfile}")

    _run(["validate", str(subfile)], cwd=workdir)
    _run(["submit", str(subfile)], cwd=workdir, interactive=True)

    receipt = evidence_receipt(
        "PUBLICATION_ARXIV_AUTHOR_REVIEW_SESSION",
        {
            "prior_receipt_hash": state.receipt["receipt_hash"],
            "manifest_hash": state.manifest_hash,
            "manuscript_sha256": state.manuscript_sha256,
        },
        {
            "status": "AUTHOR_REVIEW_SESSION_COMPLETED",
            "final_submission_performed_by_bridge": False,
        },
    )
    updated = state.model_copy(update={"status": "AUTHOR_REVIEW_SESSION_COMPLETED", "receipt": receipt})
    _write_state(state_path, updated)
    return updated


def verify(state_path: Path, manifest_path: Path) -> dict[str, Any]:
    state_path = state_path.resolve()
    manifest_path = manifest_path.resolve()
    state = PublicationState.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    manifest = _load_manifest(manifest_path)
    manuscript = _resolve_manuscript(manifest_path, manifest.manuscript_file)
    manifest_hash = sha256_text(canonical_json(manifest.model_dump(mode="json")))
    manuscript_hash = _sha256_file(manuscript)
    checks = {
        "manifest_hash_match": manifest_hash == state.manifest_hash,
        "manuscript_hash_match": manuscript_hash == state.manuscript_sha256,
        "subfile_exists": Path(state.subfile_path).is_file(),
        "values_exists": Path(state.values_path).is_file(),
    }
    return {"ok": all(checks.values()), "checks": checks}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matverse-publication-bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--manifest", required=True, type=Path)
    prepare_parser.add_argument("--work-root", default=Path(".publication"), type=Path)

    status_parser = sub.add_parser("login-status")
    status_parser.add_argument("--workdir", default=Path.cwd(), type=Path)

    login_parser = sub.add_parser("login")
    login_parser.add_argument("--workdir", default=Path.cwd(), type=Path)
    login_parser.add_argument("--interactive-login", action="store_true")

    review_parser = sub.add_parser("open-author-review")
    review_parser.add_argument("--state", required=True, type=Path)

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--state", required=True, type=Path)
    verify_parser.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(args.manifest, args.work_root).model_dump(mode="json"), ensure_ascii=False, indent=2))
        elif args.command == "login-status":
            print(login_status(args.workdir))
        elif args.command == "login":
            authorize_login(args.workdir, allow_interactive_fallback=args.interactive_login)
            print("arXiv login flow completed; credentials were not persisted by the MatVerse Publication Bridge")
        elif args.command == "open-author-review":
            print(json.dumps(open_author_review(args.state).model_dump(mode="json"), ensure_ascii=False, indent=2))
        elif args.command == "verify":
            result = verify(args.state, args.manifest)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 2
        return 0
    except (PublicationBridgeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(_redact(str(exc)), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
