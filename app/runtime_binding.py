from __future__ import annotations

from typing import Any

from .core import stable_hash
from .runtime_discovery import PROTOCOL_VERSION as DISCOVERY_PROTOCOL_VERSION

PROTOCOL_VERSION = "matverse.runtime-binding.v1"

_BINDING_FIELDS = frozenset(
    {
        "protocol_version",
        "decision",
        "discovery_report_hash",
        "runtime",
        "model",
        "container",
        "requirements",
        "binding_hash",
    }
)
_RUNTIME_FIELDS = frozenset({"runtime_id", "version", "executable", "endpoint", "upstream_repo"})
_MODEL_FIELDS = frozenset({"name", "digest", "size"})
_CONTAINER_FIELDS = frozenset({"runtime_id", "version", "executable"})
_REQUIREMENT_FIELDS = frozenset({"required_model", "require_container"})


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


def validate_execution_binding(binding: dict[str, Any]) -> tuple[bool, str]:
    """Validate a standalone execution binding before it can reach an executor.

    A binding is observational identity, not authorization. This validator proves
    only canonical shape, integrity and sufficient identity inside a trusted
    execution domain; HDB/Ω remains the authority for the workload itself and
    origin authentication requires a separate host/issuer attestation.
    """

    if set(binding) != _BINDING_FIELDS:
        return False, "unexpected_binding_fields"
    if binding.get("protocol_version") != PROTOCOL_VERSION:
        return False, "unsupported_binding_protocol"
    if binding.get("decision") != "PASS":
        return False, "binding_not_pass"

    supplied_hash = binding.get("binding_hash")
    if not isinstance(supplied_hash, str) or not supplied_hash:
        return False, "binding_hash_missing"
    body = {key: value for key, value in binding.items() if key != "binding_hash"}
    if stable_hash(body) != supplied_hash:
        return False, "binding_hash_mismatch"

    discovery_hash = binding.get("discovery_report_hash")
    if not isinstance(discovery_hash, str) or len(discovery_hash) != 64:
        return False, "discovery_report_hash_invalid"

    runtime = binding.get("runtime")
    if not isinstance(runtime, dict):
        return False, "runtime_identity_missing"
    if set(runtime) != _RUNTIME_FIELDS:
        return False, "unexpected_runtime_identity_fields"
    runtime_id = runtime.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id.strip():
        return False, "runtime_identity_missing"

    requirements = binding.get("requirements")
    if not isinstance(requirements, dict):
        return False, "binding_requirements_missing"
    if set(requirements) != _REQUIREMENT_FIELDS:
        return False, "unexpected_binding_requirement_fields"

    required_model = requirements.get("required_model")
    model = binding.get("model")
    if required_model is not None:
        if not isinstance(required_model, str) or not required_model.strip():
            return False, "required_model_invalid"
        if not isinstance(model, dict):
            return False, "required_model_identity_mismatch"
        if set(model) != _MODEL_FIELDS:
            return False, "unexpected_model_identity_fields"
        if model.get("name") != required_model:
            return False, "required_model_identity_mismatch"
        digest = model.get("digest")
        if not isinstance(digest, str) or not digest.strip():
            return False, "required_model_immutable_identity_missing"
    elif model is not None:
        return False, "unexpected_model_identity"

    require_container = requirements.get("require_container")
    if not isinstance(require_container, bool):
        return False, "container_requirement_invalid"
    container = binding.get("container")
    if require_container:
        if not isinstance(container, dict):
            return False, "container_identity_missing"
        if set(container) != _CONTAINER_FIELDS:
            return False, "unexpected_container_identity_fields"
        if container.get("runtime_id") not in {"podman", "docker"}:
            return False, "container_identity_missing"
    elif container is not None:
        return False, "unexpected_container_identity"

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
    binding = {**body, "binding_hash": stable_hash(body)}
    valid, reason = validate_execution_binding(binding)
    if not valid:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decision": "HOLD",
            "reason": f"constructed_binding_invalid:{reason}",
            "discovery_report_hash": report.get("report_hash"),
        }
    return binding
