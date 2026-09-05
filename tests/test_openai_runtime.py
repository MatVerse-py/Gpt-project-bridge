from __future__ import annotations

import json

import httpx
import pytest

from app.openai_runtime import (
    OPENAI_BASE_URL,
    OpenAIConfigurationError,
    OpenAIProviderError,
    OpenAIResponsesRuntime,
    OpenAIRuntimeConfig,
    runtime_status_from_env,
)


def test_from_env_fails_closed_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-astra")
    with pytest.raises(OpenAIConfigurationError, match="OPENAI_API_KEY"):
        OpenAIRuntimeConfig.from_env()


def test_from_env_requires_explicit_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-key-material")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(OpenAIConfigurationError, match="OPENAI_MODEL"):
        OpenAIRuntimeConfig.from_env()


def test_status_never_exposes_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-secret-key-material"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-astra")
    status = runtime_status_from_env()
    assert status["configured"] is True
    assert status["api_key_present"] is True
    assert secret not in json.dumps(status, sort_keys=True)


def test_config_repr_redacts_key() -> None:
    secret = "test-secret-key-material"
    config = OpenAIRuntimeConfig(api_key=secret, model="gpt-6-astra")
    assert secret not in repr(config)


def test_governed_invoke_blocks_before_network_for_secret_human_data() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network boundary must not be crossed after BLOCK")

    runtime = OpenAIResponsesRuntime(
        OpenAIRuntimeConfig(api_key="test-secret-key-material", model="gpt-6-astra"),
        transport=httpx.MockTransport(handler),
    )
    result = runtime.governed_invoke(
        input_text="do not send this",
        human={"sensitivity": "SECRET", "consent": True, "purpose": "test"},
    )
    assert result["decision"] == "BLOCK"
    assert result["executed"] is False
    assert "output_text" not in result


def test_successful_response_is_minimized_and_hashed() -> None:
    secret = "test-secret-key-material"
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        body = json.loads(request.content.decode("utf-8"))
        captured["body"] = body
        return httpx.Response(
            200,
            headers={"x-request-id": "req_provider_123"},
            json={
                "id": "resp_123",
                "object": "response",
                "model": "gpt-6-astra",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "operational"},
                        ],
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            },
        )

    runtime = OpenAIResponsesRuntime(
        OpenAIRuntimeConfig(api_key=secret, model="gpt-6-astra", max_output_tokens=64),
        transport=httpx.MockTransport(handler),
    )
    result = runtime.governed_invoke(
        input_text="return one word",
        instructions="be concise",
        metadata={"matverse_scope": "test"},
    )

    assert result["decision"] == "PASS"
    assert result["executed"] is True
    assert result["output_text"] == "operational"
    assert result["provider_request_id"] == "req_provider_123"
    assert len(result["request_hash"]) == 64
    assert len(result["response_hash"]) == 64
    assert secret not in json.dumps(result, sort_keys=True)
    assert captured["url"] == f"{OPENAI_BASE_URL}/responses"
    assert captured["authorization"] == f"Bearer {secret}"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["store"] is False
    assert body["model"] == "gpt-6-astra"
    assert body["max_output_tokens"] == 64
    assert body["metadata"]["matverse_request_hash"] == result["request_hash"]


def test_provider_error_is_sanitized() -> None:
    secret = "test-secret-key-material"
    prompt = "sensitive prompt content"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-request-id": "req_rate_1"},
            json={"error": {"code": "rate_limit_exceeded", "message": "too many requests"}},
        )

    runtime = OpenAIResponsesRuntime(
        OpenAIRuntimeConfig(api_key=secret, model="gpt-6-astra"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OpenAIProviderError) as caught:
        runtime.invoke(input_text=prompt)
    error = caught.value
    assert error.status_code == 429
    assert error.request_id == "req_rate_1"
    assert error.provider_code == "rate_limit_exceeded"
    rendered = str(error)
    assert secret not in rendered
    assert prompt not in rendered


def test_reserved_metadata_key_is_rejected() -> None:
    runtime = OpenAIResponsesRuntime(
        OpenAIRuntimeConfig(api_key="test-secret-key-material", model="gpt-6-astra")
    )
    with pytest.raises(ValueError, match="reserved"):
        runtime.invoke(
            input_text="hello",
            metadata={"matverse_request_hash": "forged"},
        )
