from __future__ import annotations

import json

import httpx
import pytest

from app.agent_runtime import (
    AgentBroker,
    AgentConfigurationError,
    AgentSettings,
    agent_catalog,
)

PAYLOAD = {
    "kind": "agent_task",
    "public_summary": "Return one observable result.",
    "state": {"probe": "provider-adapter-contract"},
}


def test_grok_uses_xai_responses_and_discards_reasoning(monkeypatch):
    monkeypatch.setenv("XAI_MODEL", "grok-4.7")
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret-material")
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"request-id": "xai-request-1"},
            json={
                "id": "resp_xai_1",
                "model": "grok-4.7",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": [{"type": "summary_text", "text": "PRIVATE REASONING"}]},
                    {"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": "public Grok answer"}
                    ]},
                ],
                "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
            },
        )

    broker = AgentBroker(
        AgentSettings.from_env("grok"),
        actor="operator",
        transport=httpx.MockTransport(handler),
    )
    result = broker.invoke(PAYLOAD)

    assert captured["url"] == "https://api.x.ai/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer xai-test-secret-material"
    assert captured["body"]["model"] == "grok-4.7"
    assert captured["body"]["store"] is False
    assert captured["body"]["max_output_tokens"] == 1024
    assert result["status"] == "COMPLETE"
    assert result["output_text"] == "public Grok answer"
    assert result["response_id"] == "resp_xai_1"
    assert "PRIVATE REASONING" not in json.dumps(result)


def test_huggingface_uses_inference_providers_chat_endpoint(monkeypatch):
    monkeypatch.setenv("HF_MODEL", "openai/gpt-oss-120b:cheapest")
    monkeypatch.setenv("HF_TOKEN", "hf-test-secret-material")
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "hf-response-1",
                "model": "openai/gpt-oss-120b:cheapest",
                "choices": [
                    {"message": {"role": "assistant", "content": "public HF answer"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
            },
        )

    broker = AgentBroker(
        AgentSettings.from_env("huggingface"),
        actor="operator",
        transport=httpx.MockTransport(handler),
    )
    result = broker.invoke(PAYLOAD)

    assert captured["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert captured["authorization"] == "Bearer hf-test-secret-material"
    assert captured["body"]["model"] == "openai/gpt-oss-120b:cheapest"
    assert captured["body"]["stream"] is False
    assert result["status"] == "COMPLETE"
    assert result["output_text"] == "public HF answer"
    assert "hf-test-secret-material" not in json.dumps(result)


def test_ollama_local_adapter_uses_loopback_and_no_provider_secret(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:3b")
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "message": {"role": "assistant", "content": "local Ollama answer"},
                "done": True,
                "prompt_eval_count": 7,
                "eval_count": 5,
            },
        )

    broker = AgentBroker(
        AgentSettings.from_env("ollama"),
        actor="operator",
        transport=httpx.MockTransport(handler),
    )
    result = broker.invoke(PAYLOAD)

    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["body"]["model"] == "qwen2.5:3b"
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["num_predict"] == 1024
    assert result["status"] == "COMPLETE"
    assert result["output_text"] == "local Ollama answer"


def test_llama_cpp_local_adapter_uses_openai_compatible_loopback(monkeypatch):
    monkeypatch.setenv("LLAMA_CPP_MODEL", "local-gguf-model")
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "llama-response-1",
                "model": "local-gguf-model",
                "choices": [
                    {"message": {"role": "assistant", "content": "local llama.cpp answer"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
            },
        )

    broker = AgentBroker(
        AgentSettings.from_env("llama-cpp"),
        actor="operator",
        transport=httpx.MockTransport(handler),
    )
    result = broker.invoke(PAYLOAD)

    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert captured["body"]["model"] == "local-gguf-model"
    assert result["status"] == "COMPLETE"
    assert result["output_text"] == "local llama.cpp answer"


@pytest.mark.parametrize(
    ("agent_id", "model_env", "url_env", "bad_url"),
    [
        ("ollama", "OLLAMA_MODEL", "OLLAMA_URL", "http://example.com:11434"),
        ("llama-cpp", "LLAMA_CPP_MODEL", "LLAMA_CPP_URL", "https://127.0.0.1:8080"),
    ],
)
def test_local_adapters_reject_non_loopback_endpoints(monkeypatch, agent_id, model_env, url_env, bad_url):
    monkeypatch.setenv(model_env, "unit-local-model")
    monkeypatch.setenv(url_env, bad_url)
    broker = AgentBroker(
        AgentSettings.from_env(agent_id),
        actor="operator",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    with pytest.raises(AgentConfigurationError):
        broker.invoke(PAYLOAD)


def test_docker_host_gateway_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("OLLAMA_URL", "http://host.docker.internal:11434")
    monkeypatch.delenv("MATVERSE_ALLOW_HOST_DOCKER_INTERNAL", raising=False)
    broker = AgentBroker(
        AgentSettings.from_env("ollama"),
        actor="operator",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "message": {"content": "ok"}, "done": True
        })),
    )
    with pytest.raises(AgentConfigurationError):
        broker.invoke(PAYLOAD)

    monkeypatch.setenv("MATVERSE_ALLOW_HOST_DOCKER_INTERNAL", "true")
    result = broker.invoke(PAYLOAD)
    assert result["status"] == "COMPLETE"


def test_new_agent_catalog_entries_never_claim_live_connection(monkeypatch):
    for key in (
        "XAI_MODEL", "XAI_API_KEY", "HF_MODEL", "HF_TOKEN",
        "OLLAMA_MODEL", "LLAMA_CPP_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    rows = {item["agent_id"]: item for item in agent_catalog()}
    for agent_id in ("grok", "huggingface", "ollama", "llama-cpp"):
        assert rows[agent_id]["connection_verified"] is False
        assert rows[agent_id]["status"] == "BLOCKED_CONFIGURATION"


def test_grok_web_is_manual_handoff_not_a_live_browser_connection(monkeypatch):
    monkeypatch.delenv("GROK_WEB_MODEL", raising=False)
    settings = AgentSettings.from_env("grok-web")
    assert settings.spec.transport == "external_handoff"
    assert settings.model == "grok-web-manual"
    broker = AgentBroker(settings, actor="operator")
    result = broker.invoke(PAYLOAD)
    assert result["status"] == "WAITING_EXTERNAL"
    assert result["prompt"]
    assert "Return one observable result." in result["prompt"]
    assert "connection_verified" not in result or result.get("connection_verified") is False


def test_grok_web_catalog_is_configured_as_manual_but_never_live(monkeypatch):
    monkeypatch.delenv("GROK_WEB_MODEL", raising=False)
    row = next(item for item in agent_catalog() if item["agent_id"] == "grok-web")
    assert row["configured"] is True
    assert row["connection_verified"] is False
