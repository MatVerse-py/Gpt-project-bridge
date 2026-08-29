from __future__ import annotations

import argparse
import json

from app.runtime_binding import build_execution_binding
from app.runtime_discovery import DiscoveryConfig, discover_runtime_capabilities


def main() -> int:
    parser = argparse.ArgumentParser(description="COGNISYMBIOSIS governed local-runtime preflight")
    parser.add_argument("--model", default="qwen2.5:0.5b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--llama-cpp-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--require-container", action="store_true")
    args = parser.parse_args()

    discovery = discover_runtime_capabilities(
        DiscoveryConfig(
            ollama_url=args.ollama_url,
            llama_cpp_url=args.llama_cpp_url,
            timeout_seconds=args.timeout,
            allow_remote_endpoints=False,
        )
    )
    binding = build_execution_binding(
        discovery,
        required_model=args.model,
        require_container=args.require_container,
    )
    output = {"discovery": discovery, "execution_binding": binding}
    print(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if binding["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
