from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import quote, unquote, urlparse

import httpx

from .auth import Principal
from .config import Settings
from .db import Database
from .provider_adapters import NATIVE_ADAPTERS, NativeProviderUnavailable, native_fetch, native_search, validate_native_options
from .search import KnowledgeService

ACCESS_PROTOCOL = "matverse.owner-access-fabric.v1"
LOCAL_PARTITION_ID = "projectvault"
REFERENCE_SEPARATOR = "::"
PARTITION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
READ_CAPABILITIES = frozenset({"search", "fetch"})
AUTHORIZATION_MODES = frozenset({"bridge_authorized", "principal_scopes"})
REMOTE_ADAPTERS = frozenset({"mcp_http"}) | NATIVE_ADAPTERS


class PartitionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PartitionSpec:
    partition_id: str
    title: str
    adapter: str
    capabilities: frozenset[str]
    authorization_mode: str = "bridge_authorized"
    required_scopes: frozenset[str] = frozenset()
    sensitivity: str = "user_private"
    enabled: bool = True
    endpoint: str | None = None
    credential_env: str | None = None
    search_tool: str = "search"
    fetch_tool: str = "fetch"
    options: Mapping[str, Any] = field(default_factory=dict)

    def public_dict(self, *, credential_configured: bool | None = None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "partition_id": self.partition_id,
            "title": self.title,
            "adapter": self.adapter,
            "capabilities": sorted(self.capabilities),
            "authorization_mode": self.authorization_mode,
            "required_scopes": sorted(self.required_scopes),
            "sensitivity": self.sensitivity,
            "enabled": self.enabled,
        }
        if credential_configured is not None:
            item["provider_credential_configured"] = credential_configured
        return item


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_partition_id(value: str) -> str:
    partition_id = value.strip().lower()
    if not PARTITION_ID_RE.fullmatch(partition_id):
        raise ValueError(f"Invalid partition_id: {value!r}")
    return partition_id


def _string_set(value: Any, field_name: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field_name} must be an array of non-empty strings")
    return frozenset(item.strip() for item in value)


def _endpoint_is_safe(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _scope_granted(granted: frozenset[str], required: str) -> bool:
    if required in granted or "*" in granted or "owner:*" in granted:
        return True
    if "." in required and required.split(".", 1)[0] + ".*" in granted:
        return True
    if ":" in required and required.split(":", 1)[0] + ":*" in granted:
        return True
    return False


def _encode_reference(partition_id: str, item_id: str) -> str:
    return f"{partition_id}{REFERENCE_SEPARATOR}{quote(item_id, safe='')}"


def _decode_reference(reference: str) -> tuple[str, str]:
    if REFERENCE_SEPARATOR not in reference:
        raise ValueError("Federated reference must use '<partition_id>::<encoded_id>'")
    partition_id, encoded = reference.split(REFERENCE_SEPARATOR, 1)
    partition_id = _valid_partition_id(partition_id)
    item_id = unquote(encoded)
    if not item_id:
        raise ValueError("Federated reference has an empty item id")
    return partition_id, item_id


def _local_spec(settings: Settings) -> PartitionSpec:
    return PartitionSpec(
        partition_id=LOCAL_PARTITION_ID,
        title="Project Vault",
        adapter="projectvault",
        capabilities=READ_CAPABILITIES,
        authorization_mode="bridge_authorized",
        required_scopes=frozenset({settings.required_scope}),
        sensitivity="user_private",
        enabled=True,
    )


def _credential_env(raw: Mapping[str, Any], partition_id: str, *, required: bool) -> str | None:
    value = raw.get("credential_env")
    if value is None:
        if required:
            raise ValueError(f"Partition {partition_id} requires credential_env")
        return None
    env_name = str(value).strip()
    if not env_name or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_name):
        raise ValueError(f"Partition {partition_id} has invalid credential_env")
    return env_name


def load_partition_specs(settings: Settings) -> tuple[PartitionSpec, ...]:
    specs: list[PartitionSpec] = [_local_spec(settings)]
    path = settings.partition_registry_path
    if path is None:
        return tuple(specs)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Partition registry must be a JSON object")
    if payload.get("protocol") not in {None, ACCESS_PROTOCOL}:
        raise ValueError(f"Unsupported partition registry protocol: {payload.get('protocol')}")
    raw_partitions = payload.get("partitions", [])
    if not isinstance(raw_partitions, list):
        raise ValueError("Partition registry 'partitions' must be an array")
    if len(raw_partitions) > settings.max_federated_partitions - 1:
        raise ValueError("Partition registry exceeds PROJECTVAULT_MAX_FEDERATED_PARTITIONS")

    seen = {LOCAL_PARTITION_ID}
    for raw in raw_partitions:
        if not isinstance(raw, dict):
            raise ValueError("Each partition must be an object")
        partition_id = _valid_partition_id(str(raw.get("partition_id") or ""))
        if partition_id in seen:
            raise ValueError(f"Duplicate partition_id: {partition_id}")
        seen.add(partition_id)

        adapter = str(raw.get("adapter") or "").strip()
        if adapter not in REMOTE_ADAPTERS:
            raise ValueError(f"Unsupported adapter for {partition_id}: {adapter!r}")

        endpoint: str | None = None
        if adapter == "mcp_http":
            endpoint = str(raw.get("endpoint") or "").strip()
            if not endpoint or not _endpoint_is_safe(endpoint):
                raise ValueError(f"Partition {partition_id} endpoint must use HTTPS or loopback HTTP")
        elif raw.get("endpoint") is not None:
            raise ValueError(f"Partition {partition_id} native adapter does not accept custom endpoint")

        capabilities = _string_set(raw.get("capabilities", ["search", "fetch"]), "capabilities")
        if not capabilities or not capabilities.issubset(READ_CAPABILITIES):
            raise ValueError(
                f"Partition {partition_id} capabilities must be a non-empty subset of {sorted(READ_CAPABILITIES)}"
            )
        authorization_mode = str(raw.get("authorization_mode") or "bridge_authorized").strip()
        if authorization_mode not in AUTHORIZATION_MODES:
            raise ValueError(
                f"Partition {partition_id} authorization_mode must be one of {sorted(AUTHORIZATION_MODES)}"
            )
        required_scopes = _string_set(raw.get("required_scopes"), "required_scopes")
        if authorization_mode == "principal_scopes" and not required_scopes:
            raise ValueError(f"Partition {partition_id} with principal_scopes requires required_scopes")

        options = raw.get("options", {})
        if not isinstance(options, dict):
            raise ValueError(f"Partition {partition_id} options must be an object")
        if adapter in NATIVE_ADAPTERS:
            validate_native_options(adapter, options)

        credential_env = _credential_env(raw, partition_id, required=adapter in NATIVE_ADAPTERS)

        specs.append(
            PartitionSpec(
                partition_id=partition_id,
                title=str(raw.get("title") or partition_id),
                adapter=adapter,
                capabilities=capabilities,
                authorization_mode=authorization_mode,
                required_scopes=required_scopes,
                sensitivity=str(raw.get("sensitivity") or "user_private"),
                enabled=bool(raw.get("enabled", True)),
                endpoint=endpoint,
                credential_env=credential_env,
                search_tool=str(raw.get("search_tool") or "search"),
                fetch_tool=str(raw.get("fetch_tool") or "fetch"),
                options=dict(options),
            )
        )
    return tuple(specs)


class AccessFabric:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.knowledge = KnowledgeService(db, settings.public_base_url, settings.max_results)
        self.specs = {spec.partition_id: spec for spec in load_partition_specs(settings)}

    def _provider_credential_configured(self, spec: PartitionSpec) -> bool:
        if spec.adapter == "projectvault" or spec.credential_env is None:
            return True
        return bool(os.getenv(spec.credential_env))

    def _provider_token(self, spec: PartitionSpec) -> str:
        if not spec.credential_env:
            raise PartitionUnavailable("provider_credential_not_configured")
        token = os.getenv(spec.credential_env)
        if not token:
            raise PartitionUnavailable("provider_credential_not_configured")
        return token

    def _authorization(self, spec: PartitionSpec, principal: Principal) -> tuple[str, str | None]:
        if not spec.enabled:
            return "DISABLED", "partition_disabled"
        if spec.authorization_mode == "principal_scopes":
            missing = sorted(scope for scope in spec.required_scopes if not _scope_granted(principal.scopes, scope))
            if missing:
                return "DENIED", "missing_principal_scopes:" + ",".join(missing)
        elif not _scope_granted(principal.scopes, self.settings.required_scope):
            return "DENIED", f"missing_bridge_scope:{self.settings.required_scope}"
        if not self._provider_credential_configured(spec):
            return "UNAVAILABLE", "provider_credential_not_configured"
        return "GRANTED", None

    def list_partitions(self, principal: Principal) -> dict[str, Any]:
        partitions: list[dict[str, Any]] = []
        for spec in self.specs.values():
            status, reason = self._authorization(spec, principal)
            item = spec.public_dict(
                credential_configured=self._provider_credential_configured(spec) if spec.adapter != "projectvault" else None
            )
            item["access_status"] = status
            if reason:
                item["access_reason"] = reason
            partitions.append(item)
        return {"protocol": ACCESS_PROTOCOL, "partitions": partitions}

    def explain_access(self, principal: Principal, partition_id: str | None = None) -> dict[str, Any]:
        if partition_id is None:
            selected = list(self.specs.values())
        else:
            normalized = _valid_partition_id(partition_id)
            if normalized not in self.specs:
                raise KeyError(normalized)
            selected = [self.specs[normalized]]
        entries = []
        for spec in selected:
            status, reason = self._authorization(spec, principal)
            if spec.adapter == "projectvault":
                boundary = "Local indexed Project Vault boundary"
            elif spec.adapter == "mcp_http":
                boundary = "Remote MCP provider authorization remains independently enforced"
            else:
                boundary = f"Native {spec.adapter} provider authorization remains independently enforced"
            entries.append({
                "partition_id": spec.partition_id,
                "access_status": status,
                "reason": reason,
                "rule": (
                    "Explicit Bridge registration + authenticated owner access"
                    if spec.authorization_mode == "bridge_authorized"
                    else "Authenticated owner access + partition-specific principal scopes"
                ),
                "provider_boundary": boundary,
            })
        return {
            "protocol": ACCESS_PROTOCOL,
            "principle": (
                "User authorization is necessary but access is limited to explicitly registered "
                "partitions, read capabilities, provider permissions, and the caller's granted scopes."
            ),
            "partitions": entries,
        }

    def _selected_specs(self, requested: Iterable[str] | None) -> list[PartitionSpec]:
        if requested is None:
            return list(self.specs.values())
        raw = [str(item).strip().lower() for item in requested if str(item).strip()]
        if not raw or "*" in raw:
            return list(self.specs.values())
        selected: list[PartitionSpec] = []
        seen: set[str] = set()
        for partition_id in raw:
            partition_id = _valid_partition_id(partition_id)
            if partition_id not in self.specs:
                raise KeyError(partition_id)
            if partition_id not in seen:
                selected.append(self.specs[partition_id])
                seen.add(partition_id)
        return selected

    def _remote_headers(self, spec: PartitionSpec) -> dict[str, str]:
        headers = {"accept": "application/json", "content-type": "application/json"}
        if spec.credential_env:
            headers["authorization"] = f"Bearer {self._provider_token(spec)}"
        return headers

    def _remote_tool_call(self, spec: PartitionSpec, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not spec.endpoint:
            raise PartitionUnavailable("partition_endpoint_missing")
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": dict(arguments)},
        }
        try:
            with httpx.Client(timeout=float(self.settings.partition_timeout_seconds), follow_redirects=False) as client:
                response = client.post(spec.endpoint, headers=self._remote_headers(spec), json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            raise PartitionUnavailable(f"provider_request_failed:{type(exc).__name__}") from exc
        if not isinstance(body, dict):
            raise PartitionUnavailable("provider_invalid_jsonrpc")
        if body.get("error"):
            error = body["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise PartitionUnavailable(f"provider_rpc_error:{message}")
        result = body.get("result")
        if not isinstance(result, dict):
            raise PartitionUnavailable("provider_missing_result")
        if result.get("isError"):
            raise PartitionUnavailable("provider_tool_error")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    try:
                        decoded = json.loads(item["text"])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(decoded, dict):
                        return decoded
        return result

    def _search_partition(self, spec: PartitionSpec, query: str, limit: int) -> list[dict[str, Any]]:
        if spec.adapter == "projectvault":
            payload = self.knowledge.search(query, limit=limit)
        elif spec.adapter == "mcp_http":
            payload = self._remote_tool_call(spec, spec.search_tool, {"query": query})
        elif spec.adapter in NATIVE_ADAPTERS:
            try:
                payload = native_search(
                    spec.adapter,
                    token=self._provider_token(spec),
                    query=query,
                    limit=limit,
                    options=spec.options,
                    timeout=float(self.settings.partition_timeout_seconds),
                )
            except NativeProviderUnavailable as exc:
                raise PartitionUnavailable(str(exc)) from exc
        else:
            raise PartitionUnavailable("unsupported_adapter")
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise PartitionUnavailable("provider_search_result_missing_results")
        results: list[dict[str, Any]] = []
        for item in raw_results[:limit]:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or source_id).strip()
            if not source_id:
                continue
            normalized = {
                "id": _encode_reference(spec.partition_id, source_id),
                "partition_id": spec.partition_id,
                "source_id": source_id,
                "title": title,
            }
            if item.get("url"):
                normalized["url"] = str(item["url"])
            if isinstance(item.get("metadata"), dict):
                normalized["metadata"] = item["metadata"]
            results.append(normalized)
        return results

    def _fetch_partition(self, spec: PartitionSpec, item_id: str) -> dict[str, Any]:
        if spec.adapter == "projectvault":
            payload = self.knowledge.fetch(item_id)
        elif spec.adapter == "mcp_http":
            payload = self._remote_tool_call(spec, spec.fetch_tool, {"id": item_id})
        elif spec.adapter in NATIVE_ADAPTERS:
            try:
                payload = native_fetch(
                    spec.adapter,
                    token=self._provider_token(spec),
                    item_id=item_id,
                    options=spec.options,
                    timeout=float(self.settings.partition_timeout_seconds),
                    max_bytes=self.settings.max_indexable_file_bytes,
                )
            except NativeProviderUnavailable as exc:
                raise PartitionUnavailable(str(exc)) from exc
        else:
            raise PartitionUnavailable("unsupported_adapter")
        if not isinstance(payload, dict):
            raise PartitionUnavailable("provider_fetch_result_invalid")
        result = dict(payload)
        result["id"] = _encode_reference(spec.partition_id, item_id)
        result["partition_id"] = spec.partition_id
        result["source_id"] = item_id
        return result

    def _receipt(
        self,
        *,
        principal: Principal,
        action: str,
        purpose: str | None,
        query: str | None,
        requested: list[str],
        accessed: list[str],
        skipped: list[dict[str, str]],
        result_count: int,
        resource_id: str | None = None,
    ) -> dict[str, Any]:
        receipt = {
            "protocol": ACCESS_PROTOCOL,
            "receipt_id": str(uuid.uuid4()),
            "timestamp": utc_now(),
            "principal": principal.subject,
            "action": action,
            "purpose_hash": sha256_text(purpose.strip()) if purpose and purpose.strip() else None,
            "query_hash": sha256_text(query.strip()) if query and query.strip() else None,
            "requested_partitions": requested,
            "accessed_partitions": accessed,
            "skipped_partitions": skipped,
            "result_count": result_count,
        }
        self.db.audit(principal.subject, f"access_fabric.{action}", resource_id, receipt["receipt_id"], receipt)
        return receipt

    def search(
        self,
        principal: Principal,
        query: str,
        *,
        partitions: Iterable[str] | None = None,
        limit: int | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("search_federated requires a non-empty query string")
        effective_limit = max(1, min(100, int(limit or self.settings.max_results)))
        specs = self._selected_specs(partitions)
        requested = [spec.partition_id for spec in specs]
        results: list[dict[str, Any]] = []
        accessed: list[str] = []
        skipped: list[dict[str, str]] = []
        for spec in specs:
            if len(results) >= effective_limit:
                break
            status, reason = self._authorization(spec, principal)
            if status != "GRANTED":
                skipped.append({"partition_id": spec.partition_id, "status": status, "reason": reason or status.lower()})
                continue
            if "search" not in spec.capabilities:
                skipped.append({"partition_id": spec.partition_id, "status": "UNSUPPORTED", "reason": "search_capability_missing"})
                continue
            try:
                remaining = effective_limit - len(results)
                partition_results = self._search_partition(spec, query, remaining)
            except PartitionUnavailable as exc:
                skipped.append({"partition_id": spec.partition_id, "status": "UNAVAILABLE", "reason": str(exc)})
                continue
            accessed.append(spec.partition_id)
            results.extend(partition_results[:remaining])
        receipt = self._receipt(
            principal=principal,
            action="search",
            purpose=purpose,
            query=query,
            requested=requested,
            accessed=accessed,
            skipped=skipped,
            result_count=len(results),
        )
        return {"protocol": ACCESS_PROTOCOL, "results": results, "access_receipt": receipt}

    def fetch(self, principal: Principal, reference: str, *, purpose: str | None = None) -> dict[str, Any]:
        partition_id, item_id = _decode_reference(reference.strip())
        if partition_id not in self.specs:
            raise KeyError(partition_id)
        spec = self.specs[partition_id]
        status, reason = self._authorization(spec, principal)
        if status != "GRANTED":
            raise PermissionError(reason or status)
        if "fetch" not in spec.capabilities:
            raise PermissionError("fetch_capability_missing")
        try:
            result = self._fetch_partition(spec, item_id)
        except PartitionUnavailable as exc:
            raise PermissionError(str(exc)) from exc
        receipt = self._receipt(
            principal=principal,
            action="fetch",
            purpose=purpose,
            query=None,
            requested=[partition_id],
            accessed=[partition_id],
            skipped=[],
            result_count=1,
            resource_id=reference,
        )
        result["access_receipt"] = receipt
        return result
