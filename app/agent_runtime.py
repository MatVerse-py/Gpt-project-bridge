"""Real provider adapters; authority and observable state stay in the Bridge."""
from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, replace
from typing import Any

import httpx

from .core import Decision, evaluate_hdb, omega_gate, stable_hash
from .model_bridge import assert_transferable_state
from .secret_plane import EnvironmentSecretVault, KeyAuthority, SecretDescriptor, SecretPlane, SecretPolicy, StorageClass

PROTOCOL = "matverse.agent-bridge.v1"
MCP_VERSION = "2025-11-25"  # Explicit legacy compatibility, negotiated with Base44.


class AgentConfigurationError(RuntimeError):
    pass


class AgentProviderError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    provider: str
    transport: str
    model_env: str
    credential_env: str | None
    roles: tuple[str, ...]
    default_model: str = ""


AGENTS = {
    a.agent_id: a for a in (
        AgentSpec("gpt", "openai", "openai_responses", "OPENAI_MODEL", "OPENAI_API_KEY", ("research", "draft", "review")),
        AgentSpec("claude", "anthropic", "anthropic_messages", "ANTHROPIC_MODEL", "ANTHROPIC_API_KEY", ("research", "code", "draft", "review")),
        AgentSpec("kilo", "kilo", "kilo_gateway", "KILO_MODEL", "KILO_API_KEY", ("code", "review", "redteam")),
        AgentSpec("manus", "manus", "manus_tasks_v2", "MANUS_AGENT_PROFILE", "MANUS_API_KEY", ("workflow", "research", "curation"), "lite"),
        AgentSpec("base44", "base44", "base44_mcp", "", "BASE44_ACCESS_TOKEN", ("application", "workflow"), "platform:base44"),
        AgentSpec("claude-code", "anthropic", "external_handoff", "CLAUDE_CODE_MODEL", None, ("code", "review")),
        AgentSpec("claude-web", "anthropic", "external_handoff", "CLAUDE_WEB_MODEL", None, ("research", "draft", "review")),
        AgentSpec("kilo-code", "kilo", "external_handoff", "KILO_CODE_MODEL", None, ("code", "review", "redteam")),
    )
}


def get_spec(agent_id: str) -> AgentSpec:
    try:
        return AGENTS[agent_id]
    except KeyError:
        raise AgentConfigurationError("unknown agent") from None


@dataclass(frozen=True)
class AgentSettings:
    spec: AgentSpec
    model: str
    timeout: float
    max_tokens: int
    anonymous: bool = False
    app_id: str = ""

    @classmethod
    def from_env(cls, agent_id: str) -> "AgentSettings":
        spec = get_spec(agent_id)
        model = os.environ.get(spec.model_env, spec.default_model).strip() if spec.model_env else spec.default_model
        if not model or len(model) > 256 or any(ord(x) < 32 for x in model):
            raise AgentConfigurationError("agent model is not configured or is invalid")
        if agent_id == "manus" and model not in {"lite", "standard", "max"}:
            raise AgentConfigurationError("Manus profile must be lite, standard or max")
        try:
            timeout = float(os.environ.get("MATVERSE_AGENT_TIMEOUT_SECONDS", "60"))
            max_tokens = int(os.environ.get("MATVERSE_AGENT_MAX_OUTPUT_TOKENS", "1024"))
        except ValueError:
            raise AgentConfigurationError("invalid agent resource limits") from None
        if not math.isfinite(timeout) or not 1 <= timeout <= 120 or not 1 <= max_tokens <= 8192:
            raise AgentConfigurationError("agent resource limits are out of bounds")
        anonymous = agent_id == "kilo" and os.environ.get("KILO_ALLOW_ANONYMOUS", "").lower() == "true"
        if anonymous and not model.endswith(":free"):
            raise AgentConfigurationError("anonymous Kilo access requires an explicit :free model")
        app_id = os.environ.get("BASE44_APP_ID", "").strip() if agent_id == "base44" else ""
        if agent_id == "base44" and not re.fullmatch(r"[a-fA-F0-9]{24}", app_id):
            raise AgentConfigurationError("BASE44_APP_ID must identify the selected app")
        return cls(spec, model, timeout, max_tokens, anonymous, app_id)

    def status(self) -> dict[str, Any]:
        credential_present = bool(os.environ.get(self.spec.credential_env or "", "").strip())
        configured = self.spec.transport == "external_handoff" or self.anonymous or credential_present
        return {
            "agent_id": self.spec.agent_id, "provider": self.spec.provider, "model": self.model,
            "transport": self.spec.transport, "roles": list(self.spec.roles),
            "credential_present": credential_present, "configured": configured,
            "connection_verified": False, "anonymous": self.anonymous,
            "status": "CONFIGURED_UNVERIFIED" if configured else "BLOCKED_CREDENTIAL",
            "underlying_model_disclosed": self.spec.agent_id not in {"manus", "base44"},
        }


def agent_catalog() -> list[dict[str, Any]]:
    rows = []
    for agent_id, spec in AGENTS.items():
        try:
            rows.append(AgentSettings.from_env(agent_id).status())
        except AgentConfigurationError:
            rows.append({"agent_id": agent_id, "provider": spec.provider, "transport": spec.transport,
                         "roles": list(spec.roles), "configured": False, "connection_verified": False,
                         "status": "BLOCKED_CONFIGURATION"})
    return rows


def validate_payload(payload: dict[str, Any]) -> str:
    assert_transferable_state(payload)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if not payload or len(text.encode("utf-8")) > 128_000:
        raise ValueError("observable payload must be nonempty and at most 128000 bytes")
    return text


class AgentBroker:
    """One requesting actor, one capability and one-use credential lease."""
    def __init__(self, settings: AgentSettings, *, actor: str, transport: httpx.BaseTransport | None = None):
        self.settings, self.actor, self.transport = settings, actor, transport
        self.plane = SecretPlane(vault=EnvironmentSecretVault(), lease_signing_key=KeyAuthority.generate())
        spec = settings.spec
        if spec.credential_env and not settings.anonymous and spec.agent_id != "gpt":
            self.plane.register_secret(
                SecretDescriptor(secret_id=f"provider.{spec.agent_id}.credential", kind="provider_credential",
                    owner="matverse", purpose="bounded agent invocation", provider=spec.provider,
                    storage_class=StorageClass.ENVIRONMENT, version=1, created_at=int(time.time())),
                policy=SecretPolicy(allowed_actors=(actor,), allowed_capabilities=(f"agent.{spec.agent_id}",),
                    allowed_scopes=(f"agent:invoke:{spec.agent_id}",), max_ttl_seconds=30, max_uses=1),
                locator=spec.credential_env,
            )

    def _with_credential(self, consumer):
        s = self.settings
        if s.anonymous:
            return consumer(None)
        lease = self.plane.issue_lease(secret_id=f"provider.{s.spec.agent_id}.credential", actor=self.actor,
            capability=f"agent.{s.spec.agent_id}", scope=f"agent:invoke:{s.spec.agent_id}", ttl_seconds=30, max_uses=1)
        def disclose(view):
            key = bytes(view).decode("utf-8")
            if not key or any(ch.isspace() for ch in key):
                raise AgentConfigurationError("malformed provider credential")
            return consumer(key)
        return self.plane.execute_with_secret(lease, disclose)

    def invoke(self, payload: dict[str, Any], human: dict[str, Any] | None = None,
               *, allow_base44_write: bool = False) -> dict[str, Any]:
        text = validate_payload(payload)
        decision, reason = omega_gate(hdb=evaluate_hdb(human), action="PROVIDER_EXPOSURE",
                                     ontology_ok=True, signature_valid=True, transition_valid=True)
        if decision is not Decision.PASS:
            return {"decision": decision.value, "reason": reason, "executed": False}
        s = self.settings
        if s.spec.transport == "external_handoff":
            return {"decision": "PASS", "status": "WAITING_EXTERNAL", "executed": False,
                    "prompt": text, "requested_model": s.model}
        if s.spec.agent_id == "gpt":
            from .openai_runtime import OpenAIConfigurationError, OpenAIProviderError
            from .openai_secret_plane import OpenAIPublicRuntimeSettings, OpenAISecretPlaneBroker
            try:
                settings = replace(OpenAIPublicRuntimeSettings.from_env(), timeout_seconds=s.timeout,
                                   max_output_tokens=s.max_tokens)
                result = OpenAISecretPlaneBroker(settings=settings, lease_signing_key=KeyAuthority.generate(),
                    transport=self.transport).governed_invoke(actor=self.actor, input_text=text, human=human)
            except OpenAIConfigurationError:
                raise AgentConfigurationError("OpenAI configuration is invalid") from None
            except OpenAIProviderError as exc:
                raise AgentProviderError("openai_provider_error", exc.status_code) from None
            if result["decision"] != "PASS":
                return result
            status = result.get("provider_status", "unknown")
            return {"status": "COMPLETE" if status == "completed" else "PARTIAL" if status == "incomplete" else "UNKNOWN",
                    "provider_status": status, "output_text": result["output_text"], "model": result["model"],
                    "requested_model": s.model, "response_id": result["response_id"], "usage": result.get("usage", {})}
        if s.spec.agent_id == "base44":
            state = payload.get("state", {})
            if not isinstance(state, dict):
                raise ValueError("Base44 requires a tool and arguments under state")
            tool, args = self._base44_arguments(state, allow_base44_write)
            return self._with_credential(lambda key: self._base44(key, tool, args))
        if s.spec.agent_id == "manus" and len(text) > 12_000:
            raise ValueError("Manus task input exceeds the local 12000-character budget")
        return self._with_credential(lambda key: self._invoke_http(key, text))

    def _request(self, client: httpx.Client, method: str, url: str, **kwargs) -> tuple[dict[str, Any], httpx.Response]:
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            raise AgentProviderError("provider_timeout_uncertain") from None
        except httpx.HTTPError:
            raise AgentProviderError("provider_network_error_uncertain") from None
        if response.status_code >= 300:
            raise AgentProviderError("provider_http_error", response.status_code)
        if len(response.content) > 2_000_000:
            raise AgentProviderError("provider_response_too_large")
        try:
            if response.headers.get("content-type", "").startswith("text/event-stream"):
                candidates = [json.loads(line[5:].strip()) for line in response.text.splitlines()
                              if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}]
                body = next((x for x in candidates if isinstance(x, dict) and "id" in x), None)
            else:
                body = response.json()
        except (ValueError, TypeError):
            raise AgentProviderError("provider_invalid_json") from None
        if not isinstance(body, dict):
            raise AgentProviderError("provider_invalid_shape")
        return body, response

    def _invoke_http(self, key: str | None, text: str) -> dict[str, Any]:
        s = self.settings
        headers = {"Content-Type": "application/json"}
        if s.spec.agent_id == "claude":
            url = "https://api.anthropic.com/v1/messages"
            headers.update({"Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01"})
            workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
            if workspace:
                headers["anthropic-workspace-id"] = workspace
            body = {"model": s.model, "max_tokens": s.max_tokens, "messages": [{"role": "user", "content": text}]}
        elif s.spec.agent_id == "kilo":
            url = "https://api.kilo.ai/api/gateway/chat/completions"
            if key is not None:
                headers["Authorization"] = f"Bearer {key}"
            body = {"model": s.model, "max_tokens": s.max_tokens, "stream": False,
                    "messages": [{"role": "user", "content": text}]}
        elif s.spec.agent_id == "manus":
            url = "https://api.manus.ai/v2/task.create"
            headers["x-manus-api-key"] = key
            body = {"message": {"content": [{"type": "text", "text": text, "visibility": "visible"}],
                                "connectors": []},
                    "agent_profile": s.model, "interactive_mode": False, "share_visibility": "private"}
        else:
            raise AgentConfigurationError("no HTTP adapter for agent")
        with httpx.Client(timeout=s.timeout, follow_redirects=False, transport=self.transport) as client:
            data, response = self._request(client, "POST", url, headers=headers, json=body)
        request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        if s.spec.agent_id == "manus":
            if data.get("ok") is not True or not isinstance(data.get("task_id"), str) or not data["task_id"]:
                raise AgentProviderError("manus_task_not_accepted")
            return {"status": "RUNNING", "task_id": data["task_id"], "model": f"profile:{s.model}",
                    "requested_model": s.model, "provider_request_id": data.get("request_id")}
        try:
            if s.spec.agent_id == "claude":
                output = "\n".join(x["text"] for x in data["content"] if isinstance(x, dict)
                                   and x.get("type") == "text" and isinstance(x.get("text"), str))
                stop = data.get("stop_reason")
                complete = stop == "end_turn"
            else:
                choice = data["choices"][0]
                output = choice["message"]["content"]
                stop = choice.get("finish_reason")
                complete = stop == "stop"
            if not isinstance(output, str) or not output.strip():
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise AgentProviderError("provider_missing_public_output") from None
        return {"status": "COMPLETE" if complete else "PARTIAL", "output_text": output,
                "model": data.get("model", s.model), "requested_model": s.model,
                "response_id": data.get("id"), "provider_request_id": request_id,
                "stop_reason": stop, "usage": data.get("usage", {})}

    def poll_manus(self, task_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
            raise ValueError("invalid Manus task id")
        def poll(key):
            headers = {"x-manus-api-key": key}
            with httpx.Client(timeout=self.settings.timeout, follow_redirects=False, transport=self.transport) as client:
                data, _ = self._request(client, "GET", "https://api.manus.ai/v2/task.detail",
                                        headers=headers, params={"task_id": task_id})
                if data.get("ok") is not True or not isinstance(data.get("task"), dict):
                    raise AgentProviderError("manus_invalid_task")
                task = data["task"]
                if task.get("id") != task_id:
                    raise AgentProviderError("manus_task_identity_mismatch")
                status = task.get("status")
                if status != "stopped" or task.get("has_running_background_jobs") is not False:
                    return {"status": "FAILED" if status == "error" else "WAITING" if status == "waiting" else "RUNNING",
                            "task_id": task_id, "main_agent_status": status,
                            "background_state_known": "has_running_background_jobs" in task}
                cursor, texts = None, []
                for _ in range(3):
                    params = {"task_id": task_id, "order": "desc", "limit": 200, "verbose": "false"}
                    if cursor: params["cursor"] = cursor
                    page, _ = self._request(client, "GET", "https://api.manus.ai/v2/task.listMessages",
                                            headers=headers, params=params)
                    if page.get("ok") is not True or not isinstance(page.get("messages"), list):
                        raise AgentProviderError("manus_invalid_messages")
                    if "task_id" in page and page["task_id"] != task_id:
                        raise AgentProviderError("manus_task_identity_mismatch")
                    for event in page["messages"]:
                        if not isinstance(event, dict):
                            raise AgentProviderError("manus_invalid_messages")
                        if event.get("type") == "assistant_message":
                            msg = event.get("assistant_message", {})
                            if not isinstance(msg, dict):
                                raise AgentProviderError("manus_invalid_messages")
                            if msg.get("delivery_kind") in {"result", "final"} and isinstance(msg.get("content"), str):
                                texts.append(msg["content"])
                    if texts: break
                    if page.get("has_more") is not True: break
                    cursor = page.get("next_cursor")
                    if not isinstance(cursor, str) or not cursor: raise AgentProviderError("manus_missing_cursor")
                if not texts:
                    return {"status": "UNKNOWN", "task_id": task_id, "reason": "final public output not available"}
                return {"status": "COMPLETE", "task_id": task_id, "output_text": texts[0],
                        "model": "profile:" + self.settings.model, "requested_model": self.settings.model}
        return self._with_credential(poll)

    def _base44_arguments(self, state: dict[str, Any], allow_write: bool) -> tuple[str, dict[str, Any]]:
        read_tools = {"list_user_apps", "list_directory", "read_file", "grep", "list_connectors", "get_app_status", "get_app_preview_url"}
        write_tools = {"write_file", "edit_file", "create_checkpoint"}
        tool = state.get("tool")
        if tool not in read_tools | write_tools:
            raise ValueError("Base44 tool is not allowed")
        if tool in write_tools and not (allow_write and os.environ.get("BASE44_ALLOW_WRITE", "").lower() == "true"):
            raise PermissionError("Base44 write capability and explicit deployment enablement are required")
        args = state.get("arguments", {})
        if not isinstance(args, dict): raise ValueError("Base44 arguments must be an object")
        args = dict(args)
        if tool != "list_user_apps":
            if args.get("appId", self.settings.app_id) != self.settings.app_id:
                raise PermissionError("Base44 app does not match the configured app")
            args["appId"] = self.settings.app_id
        for k in ("path", "file_path", "filePath"):
            if k in args:
                path = args[k]
                if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/") or any(
                        part.startswith(".env") or part == ".agents" for part in path.split("/")):
                    raise PermissionError("protected Base44 path")
        return tool, args

    def _base44(self, key: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        url = "https://app.base44.com/mcp"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        with httpx.Client(timeout=self.settings.timeout, follow_redirects=False, transport=self.transport) as client:
            init, response = self._request(client, "POST", url, headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": MCP_VERSION, "capabilities": {},
                                 "clientInfo": {"name": "matverse-bridge", "version": "1.0.0"}}})
            version = init.get("result", {}).get("protocolVersion")
            if init.get("id") != 1 or version not in {"2025-11-25", "2025-06-18", "2025-03-26"}:
                raise AgentProviderError("base44_mcp_negotiation_failed")
            headers["MCP-Protocol-Version"] = version
            session = response.headers.get("Mcp-Session-Id")
            if session: headers["Mcp-Session-Id"] = session
            notify = client.post(url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            if notify.status_code >= 300: raise AgentProviderError("base44_mcp_initialization_failed", notify.status_code)
            body, _ = self._request(client, "POST", url, headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        if body.get("id") != 2 or "error" in body or not isinstance(body.get("result"), dict):
            raise AgentProviderError("base44_mcp_call_failed")
        result = body["result"]
        if result.get("isError"):
            raise AgentProviderError("base44_tool_error")
        # Never pass through MCP notifications, raw credentials or hidden state.
        return {"status": "COMPLETE", "output_text": json.dumps(result, ensure_ascii=False),
                "model": self.settings.model, "requested_model": self.settings.model, "tool": tool}
