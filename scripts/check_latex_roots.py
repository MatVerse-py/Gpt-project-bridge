from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.latex_source import latex_closure


SCHEMA = "matverse.latex-roots.v1"
REQUIRED = ("id", "entry_tex", "source_root", "deposited_pdf")


def _load_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"unexpected schema: {payload.get('schema')!r}")
    roots = payload.get("roots")
    if not isinstance(roots, list):
        raise ValueError("roots must be a list")
    return payload


def diagnose(manifest_path: Path, *, repo_root: Path) -> dict:
    manifest = _load_manifest(manifest_path)
    results: list[dict] = []

    for index, item in enumerate(manifest["roots"]):
        if not isinstance(item, dict):
            results.append({"index": index, "state": "INVALID", "reason": "root entry must be object"})
            continue

        missing_fields = [key for key in REQUIRED if not isinstance(item.get(key), str) or not item[key].strip()]
        if missing_fields:
            results.append(
                {
                    "index": index,
                    "id": item.get("id"),
                    "state": "INVALID",
                    "reason": "missing required fields",
                    "missing_fields": missing_fields,
                }
            )
            continue

        root = (repo_root / item["source_root"]).resolve()
        entry = (repo_root / item["entry_tex"]).resolve()
        pdf = (repo_root / item["deposited_pdf"]).resolve()

        if not root.is_dir():
            results.append({"id": item["id"], "state": "HOLD", "reason": "source_root missing", "path": str(root)})
            continue
        if not entry.is_file():
            results.append({"id": item["id"], "state": "HOLD", "reason": "entry_tex missing", "path": str(entry)})
            continue
        if not pdf.is_file():
            results.append({"id": item["id"], "state": "HOLD", "reason": "deposited_pdf missing", "path": str(pdf)})
            continue

        try:
            closure = latex_closure(entry, root=root)
        except Exception as exc:
            results.append(
                {
                    "id": item["id"],
                    "state": "BLOCK",
                    "reason": f"closure error: {type(exc).__name__}: {exc}",
                }
            )
            continue

        expected = item.get("expected_closure_sha256")
        digest_match = expected is None or expected == closure.digest
        state = "CLOSURE_PASS" if closure.complete and digest_match else "HOLD"
        reason = None
        if not closure.complete:
            reason = "unresolved local references"
        elif not digest_match:
            reason = "closure digest mismatch"

        results.append(
            {
                "id": item["id"],
                "state": state,
                "reason": reason,
                "closure_complete": closure.complete,
                "closure_sha256": closure.digest,
                "closure_files": list(closure.relative_files),
                "unresolved_references": list(closure.unresolved),
                "expected_closure_sha256": expected,
                "deposited_pdf": item["deposited_pdf"],
            }
        )

    counts: dict[str, int] = {}
    for result in results:
        counts[result["state"]] = counts.get(result["state"], 0) + 1

    if not results:
        overall = "HOLD_NO_DECLARED_CANONICAL_ROOTS"
    elif any(result["state"] in {"INVALID", "BLOCK"} for result in results):
        overall = "BLOCK"
    elif any(result["state"] == "HOLD" for result in results):
        overall = "HOLD"
    else:
        overall = "CLOSURE_PASS"

    return {
        "schema": "matverse.latex-closure-diagnostic.v1",
        "manifest": manifest_path.as_posix(),
        "overall": overall,
        "counts": counts,
        "roots": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose declared MatVerse LaTeX source closures")
    parser.add_argument("--manifest", default="evidence/latex_roots.json")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    manifest_path = (repo_root / args.manifest).resolve()
    report = diagnose(manifest_path, repo_root=repo_root)
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

    if report["overall"] == "BLOCK":
        return 2
    if report["overall"] == "HOLD_NO_DECLARED_CANONICAL_ROOTS" and not args.allow_empty:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
