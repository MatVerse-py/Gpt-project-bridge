from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.qtn_watch import DEFAULT_THRESHOLD, collect, evaluate, render_markdown


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MatVerse-QTN-Watch/1.0",
    }


def _already_reported(client: httpx.Client, repo: str, token: str, digest: str) -> bool:
    query = f'repo:{repo} in:body "qtn-watch:{digest}"'
    response = client.get(
        "https://api.github.com/search/issues",
        params={"q": query, "per_page": 1},
        headers=_github_headers(token),
    )
    response.raise_for_status()
    return int(response.json().get("total_count", 0)) > 0


def _create_issue(client: httpx.Client, repo: str, token: str, body: str, finding_count: int) -> str:
    date = datetime.now(timezone.utc).date().isoformat()
    response = client.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers=_github_headers(token),
        json={
            "title": f"[QTN-WATCH] {date} — {finding_count} material development(s)",
            "body": body,
        },
    )
    response.raise_for_status()
    return str(response.json()["html_url"])


def publish_github(findings, errors: dict[str, str]) -> tuple[int, str | None]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo or "/" not in repo:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required for --github-issues")

    timeout = float(os.environ.get("QTN_WATCH_TIMEOUT", "20"))
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        new_findings = [f for f in findings if not _already_reported(client, repo, token, f.digest)]
        if not new_findings:
            return 0, None
        body = render_markdown(new_findings, errors)
        issue_url = _create_issue(client, repo, token, body, len(new_findings))
        return len(new_findings), issue_url


def main() -> int:
    parser = argparse.ArgumentParser(description="MatVerse Bridge QTN-001..QTN-028 external intelligence watch")
    parser.add_argument("--threshold", type=float, default=float(os.environ.get("QTN_WATCH_THRESHOLD", str(DEFAULT_THRESHOLD))))
    parser.add_argument("--min-sources", type=int, default=int(os.environ.get("QTN_WATCH_MIN_SOURCES", "2")))
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--github-issues", action="store_true")
    args = parser.parse_args()

    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold must be between 0 and 1")
    if args.min_sources < 1:
        parser.error("--min-sources must be >= 1")

    items, errors = collect()
    successful_sources = 4 - len(errors)
    if successful_sources < args.min_sources:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "reason": "source_quorum_not_met",
                    "successful_sources": successful_sources,
                    "required_sources": args.min_sources,
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    findings = evaluate(items, threshold=args.threshold)

    if args.json_output:
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "collected": len(items),
                    "findings": [f.to_dict() for f in findings],
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_markdown(findings, errors))

    if args.github_issues:
        count, issue_url = publish_github(findings, errors)
        if issue_url:
            print(f"QTN Watch published {count} new finding(s): {issue_url}")
        else:
            print("QTN Watch: no unreported material finding; no issue created.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
