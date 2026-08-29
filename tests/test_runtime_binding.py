from __future__ import annotations

from copy import deepcopy

from app.runtime_binding import build_execution_binding


def _report() -> dict:
    return {
        "report_hash": "a" * 64,
        "selector": {"decision": "PASS", "runtime_id": "ollama", "reason": "api_ready"},
        "capabilities": [
            {
                "runtime_id": "ollama",
                "runtime_class": "LLM_RUNTIME",
                "state": "AVAILABLE",
                "version": "0.11.0",
                "executable": "/usr/bin/ollama",
                "endpoint": "http://127.0.0.1:11434",
                "models": [
                    {"name": "qwen2.5:0.5b", "digest": "sha256:qwen", "size": 398000000},
                    {"name": "phi3:mini", "digest": "sha256:phi", "size": 2200000000},
                ],
                "upstream_repo": "https://github.com/ollama/ollama",
            },
            {
                "runtime_id": "docker",
                "runtime_class": "CONTAINER_RUNTIME",
                "state": "ABSENT",
                "version": None,
                "executable": None,
                "endpoint": None,
                "models": [],
                "upstream_repo": "https://github.com/docker/cli",
            },
            {
                "runtime_id": "podman",
                "runtime_class": "CONTAINER_RUNTIME",
                "state": "ABSENT",
                "version": None,
                "executable": None,
                "endpoint": None,
                "models": [],
                "upstream_repo": "https://github.com/containers/podman",
            },
        ],
    }


def test_required_model_is_bound_with_digest() -> None:
    binding = build_execution_binding(_report(), required_model="qwen2.5:0.5b")

    assert binding["decision"] == "PASS"
    assert binding["runtime"]["runtime_id"] == "ollama"
    assert binding["model"] == {
        "name": "qwen2.5:0.5b",
        "digest": "sha256:qwen",
        "size": 398000000,
    }
    assert len(binding["binding_hash"]) == 64


def test_missing_required_model_is_hold() -> None:
    binding = build_execution_binding(_report(), required_model="missing:1b")

    assert binding["decision"] == "HOLD"
    assert binding["reason"] == "required_model_not_observed"
    assert binding["observed_models"] == ["phi3:mini", "qwen2.5:0.5b"]


def test_container_requirement_is_independent_and_fail_closed() -> None:
    binding = build_execution_binding(_report(), required_model="qwen2.5:0.5b", require_container=True)

    assert binding["decision"] == "HOLD"
    assert binding["reason"] == "container_runtime_required_but_absent"


def test_podman_is_preferred_when_container_is_required() -> None:
    report = _report()
    podman = next(item for item in report["capabilities"] if item["runtime_id"] == "podman")
    podman.update({"state": "AVAILABLE", "version": "podman 5", "executable": "/usr/bin/podman"})

    binding = build_execution_binding(report, required_model="qwen2.5:0.5b", require_container=True)

    assert binding["decision"] == "PASS"
    assert binding["container"]["runtime_id"] == "podman"


def test_binding_hash_changes_on_model_digest_drift() -> None:
    left_report = _report()
    right_report = deepcopy(left_report)
    right_model = right_report["capabilities"][0]["models"][0]
    right_model["digest"] = "sha256:changed"

    left = build_execution_binding(left_report, required_model="qwen2.5:0.5b")
    right = build_execution_binding(right_report, required_model="qwen2.5:0.5b")

    assert left["binding_hash"] != right["binding_hash"]


def test_discovery_hold_cannot_be_promoted_to_binding_pass() -> None:
    report = _report()
    report["selector"] = {"decision": "HOLD", "runtime_id": None, "reason": "no_ready_llm_runtime"}

    binding = build_execution_binding(report, required_model="qwen2.5:0.5b")

    assert binding["decision"] == "HOLD"
    assert binding["reason"] == "runtime_discovery_not_ready"
