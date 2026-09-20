from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from app.core import stable_hash

BASE_URL = "https://matverse-secret-broker-r0992d.v2.appdeploy.ai"
EXPECTED_SECRET_REF = "secret_ref://openai/matverse/executor-transplant"
EXPECTED_CAPABILITY = "openai.responses.create"


def get_json(path: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE_URL + path,
        headers={"user-agent": "MatVerse-GitHub-SecretPlane-Probe/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return int(response.status), json.loads(response.read().decode("utf-8"))


def anonymous_provider_probe() -> tuple[int, dict]:
    body = json.dumps(
        {"model": "gpt-5.6-sol", "store": False, "input": "policy probe"}
    ).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + "/v1/responses",
        data=body,
        headers={
            "content-type": "application/json",
            "user-agent": "MatVerse-GitHub-SecretPlane-Probe/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def main() -> int:
    try:
        status_code, status = get_json("/api/status")
        policy_code, policy = get_json("/api/policy")
        anonymous_code, anonymous = anonymous_provider_probe()
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "HOLD", "error": type(exc).__name__}, sort_keys=True))
        return 2

    error = anonymous.get("error") if isinstance(anonymous, dict) else None
    hard_checks = {
        "status_http_200": status_code == 200,
        "broker_ready": status.get("ready") is True,
        "external_oidc_mode": status.get("mode") == "external_oidc_broker",
        "secret_ref_bound": status.get("secret_ref") == EXPECTED_SECRET_REF,
        "provider_secret_not_exposed": status.get("provider_secret_exposed_to_executor") is False,
        "provider_secret_currently_absent": status.get("provider_secret_configured") is False,
        "policy_http_200": policy_code == 200,
        "issuer_github_oidc": policy.get("issuer") == "https://token.actions.githubusercontent.com",
        "audience_bound": policy.get("audience") == "matverse-secret-broker",
        "repository_bound": policy.get("repository") == "MatVerse-py/Gpt-project-bridge",
        "event_bound": policy.get("event") == "workflow_dispatch",
        "runner_bound": policy.get("runner_environment") == "github-hosted",
        "secret_ref_policy_bound": policy.get("secret_ref") == EXPECTED_SECRET_REF,
        "capability_bound": policy.get("capability") == EXPECTED_CAPABILITY,
        "models_bounded": set(policy.get("allowed_models") or []) == {"gpt-5.6-sol", "gpt-6-astra"},
        "anonymous_denied": anonymous_code == 401,
        "anonymous_denial_code": isinstance(error, dict) and error.get("code") == "oidc_required",
    }
    passed = all(hard_checks.values())

    report = {
        "schema": "matverse.appdeploy-secret-plane-probe.v1",
        "scope": "LIVE_PUBLIC_HTTPS_SECRET_PLANE_POLICY",
        "source_runtime": "GITHUB_HOSTED_RUNNER",
        "target_runtime": "APPDEPLOY_SECRET_BROKER",
        "hard_checks": hard_checks,
        "live_secret_plane_policy_pass": passed,
        "classification": "LIVE_SECRET_PLANE_POLICY_PASS" if passed else "HOLD",
        "observed": {
            "status": status,
            "policy": policy,
            "anonymous_probe_http_status": anonymous_code,
            "anonymous_probe": anonymous,
        },
        "claim_boundary": {
            "provider_secret_configured": False,
            "live_provider_execution": "HOLD_NO_PROVIDER_SECRET",
            "external_pass": "HOLD",
            "world_real_pass": "HOLD",
            "independent_administrative_witness": False,
        },
    }
    report["result_hash"] = stable_hash(report)
    Path("appdeploy-secret-plane-probe-v1.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
