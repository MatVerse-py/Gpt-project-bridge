from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_discovery import DiscoveryConfig, discover_runtime_capabilities


def main() -> int:
    parser = argparse.ArgumentParser(description="MatVerse governed runtime discovery preflight")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--llama-cpp-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args()

    report = discover_runtime_capabilities(
        DiscoveryConfig(
            ollama_url=args.ollama_url,
            llama_cpp_url=args.llama_cpp_url,
            timeout_seconds=args.timeout,
            allow_remote_endpoints=args.allow_remote,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if report["selector"]["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
