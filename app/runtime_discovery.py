from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from ipaddress import ip_address
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .core import stable_hash

PROTOCOL_VERSION = "matverse.runtime-discovery.v1"


class RuntimeState(str, Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RuntimeCapability:
    runtime_id: str
    runtime_class: str
    state: RuntimeState
    version: str | None
    executable: str | None
    endpoint: str | None
    capabilities: tuple[str, ...]
    models: tuple[dict[str, Any], ...]
    upstream_repo: str | None
    reason: str


@dataclass(frozen=True)
class DiscoveryConfig:
    ollama_url: str = "http://127.0.0.1:11434"
    llama_cpp_url: str = "http://127.0.0.1:8080"
    timeout_seconds: float = 0.5
    allow_remote_endpoints: bool = False


UPSTREAMS: dict[str, dict[str, Any]] = {
    "ollama": {
        "runtime_class": "LLM_RUNTIME",
        "repo": "https://github.com/ollama/ollama",
        "binaries": ("ollama",),
        "capabilities": ("generate", "chat", "embeddings", "model_inventory"),
    },
    "llama_cpp": {
        "runtime_class": "LLM_RUNTIME",
        "repo": "https://github.com/ggml-org/llama.cpp",
        "binaries": ("llama-server", "llama-cli"),
        "capabilities": ("generate", "chat", "openai_compatible_server"),
    },
    "docker": {
        "runtime_class": "CONTAINER_RUNTIME",
        "repo": "https://github.com/docker/cli",
        "binaries": ("docker",),
        "capabilities": ("container_build", "container_run"),
    },
    "podman": {
        "runtime_class": "CONTAINER_RUNTIME",
        "repo": "https://github.com/containers/podman",
        "binaries": ("podman",),
        "capabilities": ("container_build", "container_run"),
    },
    "git": {
        "runtime_class": "SCM_RUNTIME",
        "repo": "https://github.com/git/git",
        "binaries": ("git",),
        "capabilities": ("clone", "checkout", "version_control"),
    },
}

BinaryProbe = Callable[[tuple[str, ...]], tuple[str | None, str | None, str]]
JsonGet = Callable[[str, float], dict[str, Any]]
TcpProbe = Callable[[str, int, float], bool]


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _probe_binary(candidates: tuple[str, ...]) -> tuple[str | None, str | None, str]:
    for candidate in candidates:
        path = shutil.which(candidate)
        if path is None:
            continue
        try:
            completed = subprocess.run(
                [path, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return path, None, f"binary_present_version_probe_failed:{type(exc).__name__}"
        output = (completed.stdout or completed.stderr).strip().splitlines()
        version = output[0][:256] if output else None
        return path, version, "binary_present"
    return None, None, "binary_not_found"


def _json_get(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "matverse-runtime-discovery/1"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint is policy-restricted below
            payload = response.read(4 * 1024 * 1024)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(type(exc).__name__) from exc
    parsed = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


def _probe_tcp(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _safe_json_get(url: str, config: DiscoveryConfig, getter: JsonGet) -> tuple[dict[str, Any] | None, str]:
    if not config.allow_remote_endpoints and not _is_loopback_url(url):
        return None, "remote_probe_denied"
    try:
        return getter(url, config.timeout_seconds), "ok"
    except (ConnectionError, ValueError, json.JSONDecodeError) as exc:
        return None, f"probe_failed:{type(exc).__name__}"


def _discover_ollama(config: DiscoveryConfig, binary_probe: BinaryProbe, getter: JsonGet) -> RuntimeCapability:
    meta = UPSTREAMS["ollama"]
    executable, binary_version, binary_reason = binary_probe(meta["binaries"])
    base = config.ollama_url.rstrip("/")
    version_payload, version_reason = _safe_json_get(f"{base}/api/version", config, getter)
    tags_payload, tags_reason = _safe_json_get(f"{base}/api/tags", config, getter)

    models: list[dict[str, Any]] = []
    if tags_payload is not None:
        raw_models = tags_payload.get("models", [])
        if isinstance(raw_models, list):
            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("model")
                if not isinstance(name, str) or not name:
                    continue
                models.append(
                    {
                        "name": name,
                        "digest": item.get("digest") if isinstance(item.get("digest"), str) else None,
                        "size": item.get("size") if isinstance(item.get("size"), int) else None,
                    }
                )
    models.sort(key=lambda item: item["name"])

    api_version = version_payload.get("version") if isinstance(version_payload, dict) else None
    version = str(api_version)[:256] if api_version is not None else binary_version
    if version_payload is not None and tags_payload is not None:
        state = RuntimeState.AVAILABLE
        reason = "api_ready"
    elif executable is not None:
        state = RuntimeState.DEGRADED
        reason = f"installed_but_api_unavailable:{version_reason}:{tags_reason}"
    elif version_reason == "remote_probe_denied" or tags_reason == "remote_probe_denied":
        state = RuntimeState.UNKNOWN
        reason = "remote_probe_denied"
    else:
        state = RuntimeState.ABSENT
        reason = binary_reason

    return RuntimeCapability(
        runtime_id="ollama",
        runtime_class=meta["runtime_class"],
        state=state,
        version=version,
        executable=executable,
        endpoint=base,
        capabilities=meta["capabilities"],
        models=tuple(models),
        upstream_repo=meta["repo"],
        reason=reason,
    )


def _discover_llama_cpp(config: DiscoveryConfig, binary_probe: BinaryProbe, getter: JsonGet) -> RuntimeCapability:
    meta = UPSTREAMS["llama_cpp"]
    executable, version, binary_reason = binary_probe(meta["binaries"])
    base = config.llama_cpp_url.rstrip("/")
    models_payload, probe_reason = _safe_json_get(f"{base}/v1/models", config, getter)
    models: list[dict[str, Any]] = []
    if models_payload is not None and isinstance(models_payload.get("data"), list):
        for item in models_payload["data"]:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                models.append({"name": item["id"], "digest": None, "size": None})
    models.sort(key=lambda item: item["name"])

    if models_payload is not None:
        state = RuntimeState.AVAILABLE
        reason = "openai_compatible_api_ready"
    elif executable is not None:
        state = RuntimeState.AVAILABLE
        reason = "binary_ready_server_not_running"
    elif probe_reason == "remote_probe_denied":
        state = RuntimeState.UNKNOWN
        reason = "remote_probe_denied"
    else:
        state = RuntimeState.ABSENT
        reason = binary_reason

    return RuntimeCapability(
        runtime_id="llama_cpp",
        runtime_class=meta["runtime_class"],
        state=state,
        version=version,
        executable=executable,
        endpoint=base,
        capabilities=meta["capabilities"],
        models=tuple(models),
        upstream_repo=meta["repo"],
        reason=reason,
    )


def _discover_binary_runtime(runtime_id: str, binary_probe: BinaryProbe) -> RuntimeCapability:
    meta = UPSTREAMS[runtime_id]
    executable, version, reason = binary_probe(meta["binaries"])
    state = RuntimeState.AVAILABLE if executable is not None else RuntimeState.ABSENT
    return RuntimeCapability(
        runtime_id=runtime_id,
        runtime_class=meta["runtime_class"],
        state=state,
        version=version,
        executable=executable,
        endpoint=None,
        capabilities=meta["capabilities"],
        models=(),
        upstream_repo=meta["repo"],
        reason=reason,
    )


def _python_runtime() -> RuntimeCapability:
    return RuntimeCapability(
        runtime_id="python",
        runtime_class="LANGUAGE_RUNTIME",
        state=RuntimeState.AVAILABLE,
        version=sys.version.split()[0],
        executable=sys.executable,
        endpoint=None,
        capabilities=("python_execution",),
        models=(),
        upstream_repo="https://github.com/python/cpython",
        reason="current_process",
    )


def _select_llm_runtime(capabilities: list[RuntimeCapability]) -> dict[str, Any]:
    by_id = {item.runtime_id: item for item in capabilities}
    for runtime_id in ("ollama", "llama_cpp"):
        item = by_id.get(runtime_id)
        if item is not None and item.state is RuntimeState.AVAILABLE:
            return {"decision": "PASS", "runtime_id": runtime_id, "reason": item.reason}
    degraded = [item.runtime_id for item in capabilities if item.runtime_class == "LLM_RUNTIME" and item.state is RuntimeState.DEGRADED]
    return {
        "decision": "HOLD",
        "runtime_id": None,
        "reason": "no_ready_llm_runtime",
        "degraded_candidates": sorted(degraded),
    }


def discover_runtime_capabilities(
    config: DiscoveryConfig | None = None,
    *,
    binary_probe: BinaryProbe = _probe_binary,
    getter: JsonGet = _json_get,
    tcp_probe: TcpProbe = _probe_tcp,
) -> dict[str, Any]:
    # tcp_probe is intentionally injectable for future service capabilities; v1
    # does not infer semantic readiness from an open port alone.
    del tcp_probe
    cfg = config or DiscoveryConfig()
    capabilities = [
        _python_runtime(),
        _discover_ollama(cfg, binary_probe, getter),
        _discover_llama_cpp(cfg, binary_probe, getter),
        _discover_binary_runtime("docker", binary_probe),
        _discover_binary_runtime("podman", binary_probe),
        _discover_binary_runtime("git", binary_probe),
    ]
    capabilities.sort(key=lambda item: item.runtime_id)
    selector = _select_llm_runtime(capabilities)
    body = {
        "protocol_version": PROTOCOL_VERSION,
        "policy": {
            "auto_install": False,
            "discovery_only": True,
            "remote_endpoints_allowed": cfg.allow_remote_endpoints,
            "trusted_upstreams_only": True,
        },
        "selector": selector,
        "capabilities": [
            {
                **asdict(item),
                "state": item.state.value,
                "capabilities": list(item.capabilities),
                "models": list(item.models),
            }
            for item in capabilities
        ],
    }
    return {**body, "report_hash": stable_hash(body)}
