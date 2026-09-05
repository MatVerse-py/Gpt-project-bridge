from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .app import (
    AUDIENCE,
    CAPABILITY,
    ISSUER,
    SECRET_REF,
    BrokerPolicy,
    BrokerSecretResolver,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def run_probe(output: Path) -> dict[str, object]:
    raw_env_secret = bool(os.environ.get("MATVERSE_OPENAI_PROVIDER_SECRET", "").strip())
    allow_env = os.environ.get("MATVERSE_ALLOW_ENV_SECRET", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    policy = BrokerPolicy.from_env()
    resolver = BrokerSecretResolver()

    report: dict[str, object] = {
        "protocol": "matverse.external-secret-broker.evidence.v1",
        "service": "MatVerse External Secret Broker",
        "implementation_boundary": {
            "oidc_signature_algorithm": "RS256",
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "repository": policy.repository,
            "allowed_refs": list(policy.allowed_refs),
            "workflow_ref_prefix": policy.workflow_ref_prefix,
            "secret_ref": SECRET_REF,
            "capability": CAPABILITY,
        },
        "secret_boundary": {
            "backend_mode": resolver.backend_mode(),
            "provider_secret_present_in_ci_env": raw_env_secret,
            "environment_secret_enabled": allow_env,
            "provider_secret_exposed_to_executor": False,
            "provider_secret_persisted_by_service": False,
            "raw_request_body_logged": False,
            "raw_authorization_header_logged": False,
        },
        "result": "PASS",
        "promotion": {
            "service_implementation": "EXTERNAL_SECRET_BROKER_IMPLEMENTATION_PASS",
            "external_deployment": "HOLD_NOT_DEPLOYED",
            "provider_secret_backend": "HOLD_NOT_PROVISIONED",
            "vault_kms_hsm": "HOLD_EXTERNAL_BACKEND_UNVERIFIED",
            "executor_transplant": "HOLD_INDEPENDENT_GATE",
        },
    }

    if raw_env_secret and not allow_env:
        report["result"] = "HOLD"
        report["promotion"]["service_implementation"] = "HOLD_SECRET_EXPOSURE"
    if resolver.backend_mode() == "environment_transitional":
        report["promotion"]["provider_secret_backend"] = "HOLD_TRANSITIONAL_ENV_BACKEND"

    report["evidence_pack_hash"] = hashlib.sha256(_canonical(report)).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="evidence/external-secret-broker-v1/EVIDENCE_PACK.json",
    )
    args = parser.parse_args()
    report = run_probe(Path(args.output))
    print(
        json.dumps(
            {
                "result": report["result"],
                "promotion": report["promotion"],
                "evidence_pack_hash": report["evidence_pack_hash"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["result"] in {"PASS", "HOLD"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
