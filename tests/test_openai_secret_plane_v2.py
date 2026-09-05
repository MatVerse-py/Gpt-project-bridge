from __future__ import annotations

import json

import httpx
import pytest

from app.openai_runtime import OpenAIConfigurationError
from app.openai_secret_plane import OpenAISecretPlaneBroker, secret_plane_status_from_env
from app.secret_plane import SecretNotAvailable


def _configure(monkeypatch: pytest.MonkeyPatch, *, secret: str | None = "sk-test-openai-secret-plane-v2-value") -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "16")
    monkeypatch.setenv("OPENAI_SECRET_VERSION", "7")
    if secret is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", secret)


def test_status_reports_secret_plane_without_exposing_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-test-openai-secret-plane-v2-value"
    _configure(monkeypatch, secret=secret)
    status = secret_plane_status_from_env()
    rendered = json.dumps(status, sort_keys=True)
    assert status["configured"] is True
    assert status["credential_mode"] == "secret_plane"
    assert status["direct_provider_route_reads_api_key"] is False
    assert status["secret_version"] == 7
    assert secret not in rendered
    assert "OPENAI_API_KEY" not in rendered


def test_governance_blocks_before_secret_disclosure(monkeypatch: pytest.MonkeyPatch) -> None:
    # No credential is configured. A SECRET-classified request must still return
    # the governance BLOCK instead of attempting a vault read.
    _configure(monkeypatch, secret=None)
    broker = OpenAISecretPlaneBroker.from_env(lease_signing_key=b"L" * 32)
    result = broker.governed_invoke(
        actor="operator-a",
        input_text="do not send",
        human={"sensitivity": "SECRET", "consent": True, "purpose": "test"},
    )
    assert result["decision"] == "BLOCK"
    assert result["executed"] is False
    assert result["secret_disclosed"] is False
    assert "lease_id" not in result
    assert not any(event["event_type"] == "LEASE_ISSUED" for event in broker.public_audit())


def test_pass_requires_vault_material(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, secret=None)
    broker = OpenAISecretPlaneBroker.from_env(lease_signing_key=b"L" * 32)
    with pytest.raises(SecretNotAvailable):
        broker.governed_invoke(actor="operator-a", input_text="hello")


def test_secret_plane_executes_mocked_openai_with_one_use_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-test-openai-secret-plane-v2-value"
    _configure(monkeypatch, secret=secret)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"x-request-id": "req_secret_plane_v2"},
            json={
                "id": "resp_secret_plane_v2",
                "model": "gpt-test",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "MATVERSE_SECRET_PLANE_V2_PASS"}],
                    }
                ],
                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            },
        )

    broker = OpenAISecretPlaneBroker.from_env(
        lease_signing_key=b"L" * 32,
        transport=httpx.MockTransport(handler),
    )
    result = broker.governed_invoke(
        actor="operator-a",
        input_text="Return exactly MATVERSE_SECRET_PLANE_V2_PASS",
        metadata={"matverse_scope": "secret-plane-v2-test"},
    )

    assert result["decision"] == "PASS"
    assert result["executed"] is True
    assert result["secret_disclosed"] is True
    assert result["credential_mode"] == "secret_plane"
    assert result["secret_id"] == "provider.openai.api_key"
    assert result["secret_version"] == 7
    assert result["output_text"] == "MATVERSE_SECRET_PLANE_V2_PASS"
    assert captured["authorization"] == f"Bearer {secret}"
    assert captured["body"]["store"] is False

    public = json.dumps({"result": result, "audit": broker.public_audit()}, sort_keys=True)
    assert secret not in public
    assert "OPENAI_API_KEY" not in public
    event_types = [event["event_type"] for event in broker.public_audit()]
    assert event_types.count("LEASE_ISSUED") == 1
    assert event_types.count("LEASE_USED") == 1


def test_secret_plane_rejects_malformed_provider_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, secret="bad key with whitespace")
    broker = OpenAISecretPlaneBroker.from_env(lease_signing_key=b"L" * 32)
    with pytest.raises(OpenAIConfigurationError, match="malformed"):
        broker.governed_invoke(actor="operator-a", input_text="hello")
