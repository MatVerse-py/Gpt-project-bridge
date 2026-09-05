from __future__ import annotations

from typing import Any

from .core import stable_hash
from .runtime_discovery import PROTOCOL_VERSION as DISCOVERY_PROTOCOL_VERSION

PROTOCOL_VERSION = "matverse.runtime-binding.v1"


def _runtime(report: dict[str, Any], runtime_id: str) -> dict[str, Any] | None:
    for item in report.get("capabilities", []):
        if isinstance(item, dict) and item.get("runtime_id") == runtime_id:
            return item
    return None


def _validate_discovery_report(report: dict[str, Any]) -> tuple[bool, str]:
    if report.get("protocol_version") != DISCOVERY_PROTOCOL_VERSION:
        return False, "unsupported_discovery_protocol"

    supplied_hash = report.get("report_hash")
    if not isinstance(supplied_hash, str) or not supplied_hash:
        return False, "discovery_report_hash_missing"

    body = {key: value for key, value in report.items() if key != "report_hash"}
    if stable_hash(body) != supplied_hash:
        return False, "discovery_report_hash_mismatch"

    return True, "ok"


def build_execution_binding(
    report: dict[str, Any],
    *,
    required_model: str | None = None,
    require_container: bool = False,
) -> dict[str, Any]:
    discovery_valid, discovery_reason = _validate_discovery_report(report)
    if not discovery_valid:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decision": "HOLD",
            "reason": discovery_reason,
            "discovery_report_hash": report.get("report_hash"),
        }

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
                digest = model.get("digest")
                if not isinstance(digest, str) or not digest.strip():
                    return {
                        "protocol_version": PROTOCOL_VERSION,
                        "decision": "HOLD",
                        "reason": "required_model_immutable_identity_missing",
                        "runtime_id": runtime_id,
                        "required_model": required_model,
                        "discovery_report_hash": report.get("report_hash"),
                    }
                selected_model = {
                    "name": required_model,
                    "digest": digest.strip(),
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
