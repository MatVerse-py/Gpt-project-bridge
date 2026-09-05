from __future__ import annotations

from email.message import Message
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from app.runtime_discovery import (
    DiscoveryConfig,
    RuntimeState,
    _NoRedirectHandler,
    _safe_json_get,
    discover_runtime_capabilities,
)


def _runtime(report: dict[str, Any], runtime_id: str) -> dict[str, Any]:
    return next(item for item in report["capabilities"] if item["runtime_id"] == runtime_id)


def _local_only_getter(calls: list[str]):
    def getter(url: str, timeout: float) -> dict[str, Any]:
        calls.append(url)
        assert "remote.invalid" not in url, "denied remote URL must not be fetched"
        raise ConnectionError("local service not running")

    return getter


def test_denied_remote_ollama_stays_unknown_even_if_local_binary_exists() -> None:
    calls: list[str] = []

    def binary_probe(candidates: tuple[str, ...]) -> tuple[str | None, str | None, str]:
        if candidates == ("ollama",):
            return "/usr/bin/ollama", None, "binary_present_not_executed"
        return None, None, "binary_not_found"

    report = discover_runtime_capabilities(
        DiscoveryConfig(ollama_url="https://remote.invalid:11434"),
        getter=_local_only_getter(calls),
        binary_probe=binary_probe,
    )

    assert calls == ["http://127.0.0.1:8080/v1/models"]
    ollama = _runtime(report, "ollama")
    assert ollama["state"] == RuntimeState.UNKNOWN.value
    assert ollama["reason"] == "remote_probe_denied"


def test_denied_remote_llama_cpp_stays_unknown_even_if_server_binary_exists() -> None:
    calls: list[str] = []

    def binary_probe(candidates: tuple[str, ...]) -> tuple[str | None, str | None, str]:
        if candidates == ("llama-server", "llama-cli"):
            return "/usr/local/bin/llama-server", None, "binary_present_not_executed"
        return None, None, "binary_not_found"

    report = discover_runtime_capabilities(
        DiscoveryConfig(llama_cpp_url="https://remote.invalid:8080"),
        getter=_local_only_getter(calls),
        binary_probe=binary_probe,
    )

    assert calls == [
        "http://127.0.0.1:11434/api/version",
        "http://127.0.0.1:11434/api/tags",
    ]
    llama_cpp = _runtime(report, "llama_cpp")
    assert llama_cpp["state"] == RuntimeState.UNKNOWN.value
    assert llama_cpp["reason"] == "remote_probe_denied"


def test_redirect_handler_fails_closed() -> None:
    handler = _NoRedirectHandler()
    request = Request("http://127.0.0.1:11434/api/version")
    headers = Message()
    headers["Location"] = "https://remote.invalid/escape"

    with pytest.raises(HTTPError) as exc_info:
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            headers,
            "https://remote.invalid/escape",
        )

    assert exc_info.value.code == 302
    assert "redirect denied" in str(exc_info.value)


def test_non_http_scheme_is_rejected_without_probe() -> None:
    calls: list[str] = []

    def getter(url: str, timeout: float) -> dict[str, Any]:
        calls.append(url)
        return {"marker": "must-not-be-read"}

    payload, reason = _safe_json_get(
        "file://localhost/tmp/matverse-runtime-probe.json",
        DiscoveryConfig(allow_remote_endpoints=True),
        getter,
    )

    assert payload is None
    assert reason == "unsupported_url_scheme"
    assert calls == []


def test_arbitrary_json_does_not_prove_ollama_readiness() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        if "/api/" in url:
            return {}
        raise ConnectionError("llama absent")

    report = discover_runtime_capabilities(
        getter=getter,
        binary_probe=lambda candidates: (None, None, "binary_not_found"),
    )

    ollama = _runtime(report, "ollama")
    assert ollama["state"] == RuntimeState.ABSENT.value
    assert report["selector"]["decision"] == "HOLD"


def test_partial_ollama_api_evidence_is_degraded_not_absent() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        if url.endswith("/api/version"):
            return {"version": "0.11.0"}
        if url.endswith("/api/tags"):
            raise ConnectionError("temporary tags failure")
        raise ConnectionError("llama absent")

    report = discover_runtime_capabilities(
        getter=getter,
        binary_probe=lambda candidates: (None, None, "binary_not_found"),
    )

    ollama = _runtime(report, "ollama")
    assert ollama["state"] == RuntimeState.DEGRADED.value
    assert ollama["version"] == "0.11.0"
    assert "version_api" in ollama["reason"]
    assert "ollama" in report["selector"]["degraded_candidates"]


def test_llama_server_binary_without_live_api_is_degraded() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        raise ConnectionError("service not running")

    def binary_probe(candidates: tuple[str, ...]) -> tuple[str | None, str | None, str]:
        if candidates == ("llama-server", "llama-cli"):
            return "/usr/local/bin/llama-server", None, "binary_present_not_executed"
        return None, None, "binary_not_found"

    report = discover_runtime_capabilities(getter=getter, binary_probe=binary_probe)
    llama = _runtime(report, "llama_cpp")
    assert llama["state"] == RuntimeState.DEGRADED.value
    assert report["selector"]["decision"] == "HOLD"


def test_policy_declares_non_execution_no_redirects_and_no_proxies() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        raise ConnectionError("not running")

    report = discover_runtime_capabilities(
        getter=getter,
        binary_probe=lambda candidates: (None, None, "binary_not_found"),
    )

    assert report["policy"]["executes_discovered_binaries"] is False
    assert report["policy"]["follows_http_redirects"] is False
    assert report["policy"]["uses_environment_proxies"] is False
