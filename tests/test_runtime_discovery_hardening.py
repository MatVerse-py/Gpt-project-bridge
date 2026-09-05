from __future__ import annotations

from typing import Any

from app.runtime_discovery import RuntimeState, discover_runtime_capabilities


def test_llama_cli_without_server_is_degraded_and_not_selected() -> None:
    def binary_probe(candidates: tuple[str, ...]) -> tuple[str | None, str | None, str]:
        if candidates == ("ollama",):
            return None, None, "binary_not_found"
        if candidates == ("llama-server", "llama-cli"):
            return "/usr/local/bin/llama-cli", "version 9999", "binary_present"
        return None, None, "binary_not_found"

    def getter(url: str, timeout: float) -> dict[str, Any]:
        raise ConnectionError("service unavailable")

    report = discover_runtime_capabilities(binary_probe=binary_probe, getter=getter)
    llama = next(item for item in report["capabilities"] if item["runtime_id"] == "llama_cpp")

    assert llama["state"] == RuntimeState.DEGRADED.value
    assert llama["reason"] == "llama_cli_present_server_absent"
    assert report["selector"]["decision"] == "HOLD"
    assert "llama_cpp" in report["selector"]["degraded_candidates"]
