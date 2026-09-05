from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from experiments.executor_transplant.oidc_broker_probe import run_probe


def test_probe_holds_when_broker_is_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MATVERSE_SECRET_BROKER_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PROVIDER_OPENAI_API_KEY", raising=False)

    report = run_probe(evidence_path=tmp_path / "evidence.json")
    assert report["result"] == "HOLD"
    assert report["promotion"] == "HOLD_NOT_CONFIGURED"
    assert report["provider_secret_in_runner"] is False


def test_probe_blocks_if_static_provider_secret_is_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-be-here")
    monkeypatch.setenv("MATVERSE_SECRET_BROKER_URL", "https://broker.example")

    report = run_probe(evidence_path=tmp_path / "evidence.json")
    assert report["result"] == "HOLD"
    assert report["promotion"] == "HOLD_SECRET_EXPOSURE"


def test_probe_passes_mocked_oidc_broker_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PROVIDER_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("MATVERSE_SECRET_BROKER_URL", "https://broker.example")
    monkeypatch.setenv(
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "https://pipelines.actions.githubusercontent.com/token?api-version=1",
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-request-token")
    monkeypatch.setenv("MATVERSE_SECRET_AUDIENCE", "matverse-secret-broker")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pipelines.actions.githubusercontent.com":
            return httpx.Response(
                200,
                json={"value": "oidc-jwt-" + ("z" * 40)},
            )
        assert request.url == httpx.URL("https://broker.example/v1/responses")
        assert "OPENAI_API_KEY" not in request.content.decode("utf-8")
        body = json.loads(request.content.decode("utf-8"))
        assert body["store"] is False
        return httpx.Response(
            200,
            headers={"x-request-id": "req-secret-plane"},
            json={
                "id": "resp-secret-plane",
                "model": "gpt-5.6-sol",
                "output": [],
                "usage": {},
            },
        )

    report = run_probe(
        evidence_path=tmp_path / "evidence.json",
        transport=httpx.MockTransport(handler),
    )

    assert report["result"] == "PASS"
    assert report["promotion"] == "OIDC_SECRET_BROKER_PATH_PASS"
    assert report["provider_secret_in_runner"] is False
    assert report["provider_secret_in_local_process"] is False
    assert report["provider_secret_persisted"] is False
