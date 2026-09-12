from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.qtn_watch import DEFAULT_THRESHOLD, FETCHERS, collect, evaluate, render_markdown

_DIGEST_MARKER = re.compile(r"qtn-watch:([0-9a-f]{64})")


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MatVerse-QTN-Watch/1.0",
    }


def _reported_digests(client: httpx.Client, repo: str, token: str) -> set[str]:
    max_pages = max(1, int(os.environ.get("QTN_WATCH_ISSUE_SCAN_PAGES", "10")))
    reported: set[str] = set()
    headers = _github_headers(token)
    for page in range(1, max_pages + 1):
        response = client.get(
            f"https://api.github.com/repos/{repo}/issues",
            params={"state": "all", "per_page": 100, "page": page, "sort": "created", "direction": "desc"},
            headers=headers,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise RuntimeError("GitHub issues API returned a non-list payload")
        for row in rows:
            body = row.get("body") or ""
            reported.update(_DIGEST_MARKER.findall(body))
        if len(rows) < 100:
            break
    return reported


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
    payload = response.json()
    issue_url = payload.get("html_url")
    if not isinstance(issue_url, str) or not issue_url.startswith("https://github.com/"):
        raise RuntimeError("GitHub issue creation returned no valid html_url")
    return issue_url


def publish_github(findings, errors: dict[str, str]) -> tuple[int, str | None]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo or "/" not in repo:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required for --github-issues")

    max_alerts = max(1, int(os.environ.get("QTN_WATCH_MAX_ALERTS", "20")))
    timeout = float(os.environ.get("QTN_WATCH_TIMEOUT", "20"))
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        reported = _reported_digests(client, repo, token)
        new_findings = [f for f in findings if f.digest not in reported][:max_alerts]
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
    if args.min_sources < 1 or args.min_sources > len(FETCHERS):
        parser.error(f"--min-sources must be between 1 and {len(FETCHERS)}")

    items, errors = collect()
    successful_sources = len(FETCHERS) - len(errors)
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
                    "successful_sources": successful_sources,
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
