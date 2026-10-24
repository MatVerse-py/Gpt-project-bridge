"""Offline CLI to bind an exact excerpt to version-pinned bytes.

No external fetching or authority granted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.qtn_evidence_binding import bind_claim_excerpt


def main() -> int:
    parser = argparse.ArgumentParser(description="Local text-source SHA-256 / excerpt receipt, non-authoritative")
    parser.add_argument("--file", type=Path, required=True, help="Existing UTF-8 text bytes file")
    parser.add_argument("--source-url", required=True, help="Pinned arXiv/IETF/RFC version URL")
    parser.add_argument("--expected-sha256", required=True, help="SHA-256 from external manifest; root trust not checked here")
    parser.add_argument("--excerpt", required=True, help="Exact UTF-8 text excerpt, required to occur once")
    args = parser.parse_args()
    try:
        if not args.file.is_file() or args.file.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("input must be an existing text file of <=10 MiB")
        receipt = bind_claim_excerpt(
            source_url=args.source_url,
            document_bytes=args.file.read_bytes(),
            expected_sha256=args.expected_sha256,
            exact_excerpt=args.excerpt,
        )
    except (ValueError, OSError, UnicodeError) as exc:
        print(f"HOLD: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
