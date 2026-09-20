from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from app.core import stable_hash

STATUS_URL = "https://matverse-external-bridge-verifier-8iuh4o.v2.appdeploy.ai/api/status"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def main() -> int:
    try:
        request = urllib.request.Request(
            STATUS_URL,
            headers={"user-agent": "MatVerse-GitHub-CrossInfrastructure-Probe/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            status_code = int(response.status)
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(json.dumps({"status": "HOLD", "error": type(exc).__name__}, sort_keys=True))
        return 2

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print(json.dumps({"status": "HOLD", "error": "INVALID_JSON"}, sort_keys=True))
        return 2

    selftest = payload.get("external_selftest") or {}
    state = payload.get("state") or {}
    hard_checks = {
        "http_200": status_code == 200,
        "service_pass": payload.get("status") == "PASS",
        "service_identity": payload.get("service_identity") == "APPDEPLOY_EXTERNAL_RUNTIME",
        "explicit_non_independent_boundary": payload.get("independent_administrative_witness") is False,
        "contract_id": payload.get("contract_id") == "BRIDGE-CAP-ACCUMULATE-0.1",
        "contract_hash_shape": isinstance(payload.get("contract_hash"), str)
        and _SHA256_RE.fullmatch(payload["contract_hash"]) is not None,
        "state_hash_shape": isinstance(payload.get("state_hash"), str)
        and _SHA256_RE.fullmatch(payload["state_hash"]) is not None,
        "external_substrate": state.get("substrate") == "appdeploy-external-runtime",
        "selftest_pass": selftest.get("status") == "PASS",
        "valid_signature": selftest.get("valid_signature") is True,
        "contract_hash_match": selftest.get("contract_hash_match") is True,
        "authority_valid": selftest.get("authority_valid") is True,
        "wrong_authority_blocked": selftest.get("wrong_authority_blocked") is True,
        "prestate_bound": selftest.get("prestate_bound") is True,
        "stale_replay_blocked": selftest.get("stale_replay_blocked") is True,
        "mnbk_boundary_preserved": selftest.get("mnbk_status") == "EXPERIMENTAL_CANDIDATE",
        "persisted_counter": selftest.get("persisted_counter") == 3,
        "selftest_boundary": selftest.get("independent_administrative_witness") is False,
    }
    passed = all(hard_checks.values())

    report = {
        "schema": "matverse.appdeploy-live-runtime-probe.v1",
        "scope": "LIVE_PUBLIC_HTTPS_CROSS_INFRASTRUCTURE_SELFTEST",
        "source_runtime": "GITHUB_HOSTED_RUNNER",
        "target_runtime": "APPDEPLOY_EXTERNAL_RUNTIME",
        "status_url": STATUS_URL,
        "http_status": status_code,
        "hard_checks": hard_checks,
        "live_cross_infrastructure_selftest_pass": passed,
        "classification": (
            "LIVE_CROSS_INFRASTRUCTURE_SELFTEST_PASS" if passed else "HOLD"
        ),
        "observed": {
            "contract_id": payload.get("contract_id"),
            "contract_hash": payload.get("contract_hash"),
            "state_hash": payload.get("state_hash"),
            "state": state,
            "external_selftest": selftest,
        },
        "claim_boundary": {
            "independent_administrative_witness": False,
            "external_pass": "HOLD",
            "world_real_pass": "HOLD",
            "live_provider_model_execution": "NOT_TESTED",
            "third_party_governance": "NOT_PRESENT",
        },
    }
    report["result_hash"] = stable_hash(report)
    Path("appdeploy-live-runtime-probe-v1.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
