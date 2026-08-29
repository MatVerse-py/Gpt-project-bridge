from __future__ import annotations

from typing import Any

from .core import stable_hash

PROTOCOL_VERSION = "matverse.runtime-binding.v1"


def _runtime(report: dict[str, Any], runtime_id: str) -> dict[str, Any] | None:
    for item in report.get("capabilities", []):
        if isinstance(item, dict) and item.get("runtime_id") == runtime_id:
            return item
    return None


def build_execution_binding(
    report: dict[str, Any],
    *,
    required_model: str | None = None,
    require_container: bool = False,
) -> dict[str, Any]:
    selector = report.get("selector")
    if not isinstance(selector, dict) or selector.get("decision") != "PASS":
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decision": "HOLD",
            "reason": "runtime_discovery_not_ready",
            "discovery_report_hash": report.get("report_hash"),
        }

    runtime_id = selector.get("runtime_id")
    if not isinstance(runtime_id, str):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decision": "HOLD",
            "reason": "selected_runtime_identity_missing",
            "discovery_report_hash": report.get("report_hash"),
        }

    runtime = _runtime(report, runtime_id)
    if runtime is None or runtime.get("state") != "AVAILABLE":
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decision": "HOLD",
            "reason": "selected_runtime_not_available",
            "runtime_id": runtime_id,
            "discovery_report_hash": report.get("report_hash"),
        }

    selected_model: dict[str, Any] | None = None
    if required_model is not None:
        models = runtime.get("models")
        if not isinstance(models, list):
            models = []
        for model in models:
            if isinstance(model, dict) and model.get("name") == required_model:
                selected_model = {
                    "name": required_model,
                    "digest": model.get("digest"),
                    "size": model.get("size"),
                }
                break
        if selected_model is None:
            observed = sorted(
                str(model.get("name"))
                for model in models
                if isinstance(model, dict) and isinstance(model.get("name"), str)
            )
            return {
                "protocol_version": PROTOCOL_VERSION,
                "decision": "HOLD",
                "reason": "required_model_not_observed",
                "runtime_id": runtime_id,
                "required_model": required_model,
                "observed_models": observed,
                "discovery_report_hash": report.get("report_hash"),
            }

    container: dict[str, Any] | None = None
    if require_container:
        for candidate in ("podman", "docker"):
            item = _runtime(report, candidate)
            if item is not None and item.get("state") == "AVAILABLE":
                container = {
                    "runtime_id": candidate,
                    "version": item.get("version"),
                    "executable": item.get("executable"),
                }
                break
        if container is None:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "decision": "HOLD",
                "reason": "container_runtime_required_but_absent",
                "runtime_id": runtime_id,
                "discovery_report_hash": report.get("report_hash"),
            }

    body = {
        "protocol_version": PROTOCOL_VERSION,
        "decision": "PASS",
        "discovery_report_hash": report.get("report_hash"),
        "runtime": {
            "runtime_id": runtime_id,
            "version": runtime.get("version"),
            "executable": runtime.get("executable"),
            "endpoint": runtime.get("endpoint"),
            "upstream_repo": runtime.get("upstream_repo"),
        },
        "model": selected_model,
        "container": container,
        "requirements": {
            "required_model": required_model,
            "require_container": require_container,
        },
    }
    return {**body, "binding_hash": stable_hash(body)}
