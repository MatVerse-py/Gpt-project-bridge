from __future__ import annotations

from typing import Any

from app.runtime_discovery import DiscoveryConfig, RuntimeState, discover_runtime_capabilities


def _binary_map(mapping: dict[str, tuple[str, str]]):
    def probe(candidates: tuple[str, ...]) -> tuple[str | None, str | None, str]:
        for candidate in candidates:
            if candidate in mapping:
                path, version = mapping[candidate]
                return path, version, "binary_present"
        return None, None, "binary_not_found"

    return probe


def _runtime(report: dict[str, Any], runtime_id: str) -> dict[str, Any]:
    return next(item for item in report["capabilities"] if item["runtime_id"] == runtime_id)


def test_ollama_api_and_model_digest_are_discovered() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        assert timeout == 0.5
        if url.endswith("/api/version"):
            return {"version": "0.11.0"}
        if url.endswith("/api/tags"):
            return {
                "models": [
                    {"name": "qwen2.5:0.5b", "digest": "sha256:abc", "size": 398000000},
                    {"name": "phi3:mini", "digest": "sha256:def", "size": 2200000000},
                ]
            }
        raise ConnectionError("not running")

    report = discover_runtime_capabilities(
        getter=getter,
        binary_probe=_binary_map({"ollama": ("/usr/bin/ollama", "ollama 0.11.0")}),
    )

    assert report["selector"]["decision"] == "PASS"
    assert report["selector"]["runtime_id"] == "ollama"
    ollama = _runtime(report, "ollama")
    assert ollama["state"] == RuntimeState.AVAILABLE.value
    assert ollama["version"] == "0.11.0"
    assert ollama["models"][0]["name"] == "phi3:mini"
    assert {item["digest"] for item in ollama["models"]} == {"sha256:abc", "sha256:def"}


def test_llama_cpp_is_failover_when_ollama_is_degraded() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        raise ConnectionError("service unavailable")

    report = discover_runtime_capabilities(
        getter=getter,
        binary_probe=_binary_map(
            {
                "ollama": ("/usr/bin/ollama", "ollama 0.11.0"),
                "llama-server": ("/usr/local/bin/llama-server", "version 9999"),
            }
        ),
    )

    assert _runtime(report, "ollama")["state"] == RuntimeState.DEGRADED.value
    assert _runtime(report, "llama_cpp")["state"] == RuntimeState.AVAILABLE.value
    assert report["selector"] == {
        "decision": "PASS",
        "runtime_id": "llama_cpp",
        "reason": "server_binary_ready_not_running",
    }


def test_absent_llm_runtimes_produce_hold_not_block() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        raise ConnectionError("absent")

    report = discover_runtime_capabilities(getter=getter, binary_probe=_binary_map({}))

    assert report["selector"]["decision"] == "HOLD"
    assert report["selector"]["runtime_id"] is None
    assert _runtime(report, "ollama")["state"] == RuntimeState.ABSENT.value
    assert _runtime(report, "llama_cpp")["state"] == RuntimeState.ABSENT.value


def test_remote_probes_are_fail_closed_by_default() -> None:
    calls: list[str] = []

    def getter(url: str, timeout: float) -> dict[str, Any]:
        calls.append(url)
        raise AssertionError("remote getter must not be called")

    report = discover_runtime_capabilities(
        DiscoveryConfig(
            ollama_url="http://example.invalid:11434",
            llama_cpp_url="https://example.invalid:8080",
        ),
        getter=getter,
        binary_probe=_binary_map({}),
    )

    assert calls == []
    assert _runtime(report, "ollama")["state"] == RuntimeState.UNKNOWN.value
    assert _runtime(report, "llama_cpp")["state"] == RuntimeState.UNKNOWN.value
    assert report["selector"]["decision"] == "HOLD"


def test_report_is_deterministic_for_same_observed_state() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        if url.endswith("/api/version"):
            return {"version": "1.0.0"}
        if url.endswith("/api/tags"):
            return {"models": []}
        raise ConnectionError("not running")

    binary_probe = _binary_map(
        {
            "ollama": ("/opt/ollama", "ollama 1.0.0"),
            "git": ("/usr/bin/git", "git version 2"),
        }
    )
    left = discover_runtime_capabilities(getter=getter, binary_probe=binary_probe)
    right = discover_runtime_capabilities(getter=getter, binary_probe=binary_probe)

    assert left == right
    assert left["report_hash"] == right["report_hash"]


def test_discovery_never_auto_installs_and_exposes_trusted_sources() -> None:
    def getter(url: str, timeout: float) -> dict[str, Any]:
        raise ConnectionError("absent")

    report = discover_runtime_capabilities(getter=getter, binary_probe=_binary_map({}))

    assert report["policy"]["auto_install"] is False
    assert report["policy"]["discovery_only"] is True
    assert report["policy"]["trusted_upstreams_only"] is True
    assert _runtime(report, "ollama")["upstream_repo"] == "https://github.com/ollama/ollama"
    assert _runtime(report, "llama_cpp")["upstream_repo"] == "https://github.com/ggml-org/llama.cpp"
