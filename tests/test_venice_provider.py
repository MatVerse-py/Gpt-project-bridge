from __future__ import annotations

import json

import httpx
import pytest

from app.agent_runtime import AgentBroker, AgentConfigurationError, AgentProviderError, AgentSettings
from app.secret_plane import SecretExposureError

SECRET = "venice-unit-secret-key-material"
PAYLOAD = {
    "kind": "agent_task",
    "public_summary": "Return one observable result.",
    "state": {"probe": "venice-adapter"},
}


def configured(monkeypatch):
    monkeypatch.setenv("VENICE_MODEL", "venice-uncensored")
    monkeypatch.setenv("VENICE_API_KEY", SECRET)
    return AgentSettings.from_env("venice")


def test_venice_uses_corpus_base_url_and_openai_compatible_chat_contract(monkeypatch):
    settings = configured(monkeypatch)
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"x-request-id": "venice_req_001"},
            json={
                "id": "venice_response_001",
                "model": "venice-uncensored",
                "choices": [
                    {"message": {"role": "assistant", "content": "public Venice result"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            },
        )

    broker = AgentBroker(settings, actor="operator", transport=httpx.MockTransport(handler))
    result = broker.invoke(PAYLOAD)

    assert captured["url"] == "https://api.venice.ai/api/v1/chat/completions"
    assert captured["authorization"] == f"Bearer {SECRET}"
    assert captured["body"]["model"] == "venice-uncensored"
    assert captured["body"]["stream"] is False
    assert captured["body"]["max_tokens"] == 1024
    assert result["status"] == "COMPLETE"
    assert result["output_text"] == "public Venice result"
    assert result["provider_request_id"] == "venice_req_001"
    assert SECRET not in json.dumps(result, sort_keys=True)


def test_venice_base_url_cannot_be_overridden_by_environment(monkeypatch):
    settings = configured(monkeypatch)
    monkeypatch.setenv("VENICE_BASE_URL", "https://attacker.invalid/v1")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "id": "r",
                "model": settings.model,
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )

    AgentBroker(settings, actor="operator", transport=httpx.MockTransport(handler)).invoke(PAYLOAD)
    assert seen == ["https://api.venice.ai/api/v1/chat/completions"]


def test_venice_requires_explicit_model_binding(monkeypatch):
    monkeypatch.delenv("VENICE_MODEL", raising=False)
    monkeypatch.setenv("VENICE_API_KEY", SECRET)
    with pytest.raises(AgentConfigurationError, match="model"):
        AgentSettings.from_env("venice")


def test_venice_partial_output_is_not_marked_complete(monkeypatch):
    settings = configured(monkeypatch)
    broker = AgentBroker(
        settings,
        actor="operator",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "id": "r",
                    "model": settings.model,
                    "choices": [{"message": {"content": "truncated"}, "finish_reason": "length"}],
                },
            )
        ),
    )
    assert broker.invoke(PAYLOAD)["status"] == "PARTIAL"


def test_venice_provider_errors_are_sanitized(monkeypatch):
    settings = configured(monkeypatch)
    broker = AgentBroker(
        settings,
        actor="operator",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, text="private rate-limit body with secret " + SECRET)
        ),
    )
    with pytest.raises(AgentProviderError) as caught:
        broker.invoke(PAYLOAD)
    assert caught.value.status_code == 429
    assert SECRET not in str(caught.value)
    assert "private rate-limit body" not in str(caught.value)


def test_venice_secret_echo_is_rejected(monkeypatch):
    settings = configured(monkeypatch)
    broker = AgentBroker(
        settings,
        actor="operator",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "id": "r",
                    "model": settings.model,
                    "choices": [{"message": {"content": SECRET}, "finish_reason": "stop"}],
                },
            )
        ),
    )
    with pytest.raises(SecretExposureError):
        broker.invoke(PAYLOAD)


def test_venice_catalog_binding_is_configured_not_verified(monkeypatch):
    settings = configured(monkeypatch)
    status = settings.status()
    assert status["configured"] is True
    assert status["connection_verified"] is False
    assert status["status"] == "CONFIGURED_UNVERIFIED"
