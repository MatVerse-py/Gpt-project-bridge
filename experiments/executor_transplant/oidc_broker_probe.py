from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

import httpx

from experiments.executor_transplant.secret_broker import (
    GitHubOIDCBrokerConfig,
    GitHubOIDCSecretBroker,
    SecretBrokerConfigurationError,
    SecretBrokerError,
)

DEFAULT_EVIDENCE = Path("evidence/secret-plane-oidc-broker-v1/EVIDENCE_PACK.json")
SECRET_REF = "secret_ref://openai/matverse/executor-transplant"
CAPABILITY = "openai.responses.create"
PROTOCOL = "matverse.secret-plane.oidc-broker.v1"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _hash(payload: Mapping[str, object]) -> str:
    body = dict(payload)
    body.pop("evidence_pack_hash", None)
    return hashlib.sha256(_canonical_json(body)).hexdigest()


def _write(path: Path, payload: dict[str, object]) -> dict[str, object]:
    payload["evidence_pack_hash"] = _hash(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def run_probe(
    *,
    evidence_path: Path = DEFAULT_EVIDENCE,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, object]:
    raw_secret_present = bool(
        os.environ.get("OPENAI_API_KEY", "").strip()
        or os.environ.get("PROVIDER_OPENAI_API_KEY", "").strip()
    )
    base: dict[str, object] = {
        "protocol": PROTOCOL,
        "secret_ref": SECRET_REF,
        "capability": CAPABILITY,
        "secret_source": "github_oidc_broker",
        "provider_secret_in_runner": raw_secret_present,
        "provider_secret_in_local_process": False,
        "provider_secret_persisted": False,
        "oidc_request_token_persisted": False,
        "raw_provider_output_persisted": False,
        "claim_boundary": {
            "broker_side_secret_resolution": "REQUIRED_BY_CONTRACT_NOT_LOCALLY_OBSERVABLE",
            "hsm_kms_backend": "HOLD_EXTERNAL_BACKEND_UNVERIFIED",
            "world_real_pass": "HOLD",
        },
    }

    if raw_secret_present:
        return _write(
            evidence_path,
            {
                **base,
                "result": "HOLD",
                "promotion": "HOLD_SECRET_EXPOSURE",
                "reason": "static provider secret is present in the OIDC probe environment",
            },
        )

    broker_url = os.environ.get("MATVERSE_SECRET_BROKER_URL", "").strip()
    if not broker_url:
        return _write(
            evidence_path,
            {
                **base,
                "result": "HOLD",
                "promotion": "HOLD_NOT_CONFIGURED",
                "reason": "OIDC secret broker endpoint is not configured",
            },
        )

    try:
        config = GitHubOIDCBrokerConfig.from_env()
    except SecretBrokerConfigurationError as exc:
        return _write(
            evidence_path,
            {
                **base,
                "result": "HOLD",
                "promotion": "HOLD_IDENTITY_UNAVAILABLE",
                "reason": str(exc),
            },
        )

    body = {
        "model": "gpt-5.6-sol",
        "input": (
            "Return exactly: SECRET_PLANE_OIDC_BROKER=PASS. "
            "Do not include reasoning or credentials."
        ),
        "store": False,
        "max_output_tokens": 32,
        "metadata": {
            "matverse_probe": "secret-plane-oidc-broker-v1",
        },
    }

    broker = GitHubOIDCSecretBroker(config, transport=transport)
    try:
        response = broker.forward(
            body=_canonical_json(body),
            secret_ref=SECRET_REF,
            capability=CAPABILITY,
        )
    except (SecretBrokerError, SecretBrokerConfigurationError) as exc:
        return _write(
            evidence_path,
            {
                **base,
                "result": "HOLD",
                "promotion": "HOLD_BROKER_ACCESS",
                "reason": str(exc),
            },
        )

    if response.status_code < 200 or response.status_code >= 300:
        return _write(
            evidence_path,
            {
                **base,
                "result": "HOLD",
                "promotion": "HOLD_BROKER_ACCESS",
                "reason": f"broker returned HTTP {response.status_code}",
                "provider_request_id": (
                    response.headers.get("x-request-id")
                    or response.headers.get("request-id")
                ),
            },
        )

    try:
        payload = response.json()
    except ValueError:
        return _write(
            evidence_path,
            {
                **base,
                "result": "HOLD",
                "promotion": "HOLD_BROKER_RESPONSE",
                "reason": "broker returned non-JSON provider response",
            },
        )
    if not isinstance(payload, Mapping):
        return _write(
            evidence_path,
            {
                **base,
                "result": "HOLD",
                "promotion": "HOLD_BROKER_RESPONSE",
                "reason": "broker response shape is invalid",
            },
        )

    response_id = payload.get("id")
    model = payload.get("model")
    if not isinstance(response_id, str) or not response_id:
        promotion = "HOLD_BROKER_RESPONSE"
        result = "HOLD"
        reason = "provider response id is missing"
    elif model != "gpt-5.6-sol":
        promotion = "HOLD_MODEL_BINDING"
        result = "HOLD"
        reason = "provider response model does not match requested model"
    else:
        promotion = "OIDC_SECRET_BROKER_PATH_PASS"
        result = "PASS"
        reason = "OIDC-authenticated secret_ref broker path returned a bound provider response"

    return _write(
        evidence_path,
        {
            **base,
            "result": result,
            "promotion": promotion,
            "reason": reason,
            "response_id_hash": (
                hashlib.sha256(response_id.encode("utf-8")).hexdigest()
                if isinstance(response_id, str)
                else None
            ),
            "returned_model": model if isinstance(model, str) else None,
            "provider_request_id": (
                response.headers.get("x-request-id")
                or response.headers.get("request-id")
            ),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args()
    report = run_probe(evidence_path=Path(args.output))
    print(
        json.dumps(
            {
                "result": report["result"],
                "promotion": report["promotion"],
                "reason": report["reason"],
                "evidence_pack_hash": report["evidence_pack_hash"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["result"] in {"PASS", "HOLD"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
