from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import __version__
from .auth import Authenticator, Principal
from .config import Settings
from .db import Database
from .search import KnowledgeService

SUPPORTED_PROTOCOLS = {"2025-03-26", "2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL = "2025-06-18"


def rpc_result(request_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def rpc_error(request_id: Any, code: int, message: str, data: Any = None, status: int = 200) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": error}, status_code=status)


def bounded_limit(arguments: dict[str, Any], *, default: int, maximum: int, tool_name: str) -> int:
    value = arguments.get("limit", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{tool_name} limit must be an integer between 1 and {maximum}")
    return value


def tool_security(settings: Settings) -> list[dict[str, Any]]:
    if settings.auth_mode in {"oidc", "hybrid"}:
        return [{"type": "oauth2", "scopes": [settings.required_scope]}]
    return [{"type": "noauth"}]


def tools(settings: Settings) -> list[dict[str, Any]]:
    security = tool_security(settings)
    annotations = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True}
    return [
        {
            "name": "search",
            "title": "Search account projects",
            "description": "Use this when you need to find conversations across the owner's indexed ChatGPT projects. Returns only source IDs, titles, and canonical URLs; call fetch to read a selected result.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "annotations": annotations,
            "securitySchemes": security,
        },
        {
            "name": "fetch",
            "title": "Fetch project conversation",
            "description": "Use this when you have a result ID from search and need the full preserved conversation with project attribution and source metadata.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string", "minLength": 1}},
                "required": ["id"],
                "additionalProperties": False,
            },
            "annotations": annotations,
            "securitySchemes": security,
        },
        {
            "name": "list_projects",
            "title": "List indexed projects",
            "description": "Use this when you need the explicit project registry, document counts, attribution basis, and the UNASSIGNED bucket.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": annotations,
            "securitySchemes": security,
        },
        {
            "name": "list_ingestions",
            "title": "List provenance runs",
            "description": "Use this to inspect source archives, SHA-256 receipts, import counts, and bounded failure metadata without reading indexed content.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
                "additionalProperties": False,
            },
            "annotations": annotations,
            "securitySchemes": security,
        },
        {
            "name": "list_unassigned",
            "title": "List content without project attribution",
            "description": "Use this to find preserved source documents whose project membership remains UNASSIGNED. Fetch a selected ID to inspect its provenance; do not infer membership from topic or filename.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200}},
                "additionalProperties": False,
            },
            "annotations": annotations,
            "securitySchemes": security,
        },
    ]


class MCPHandler:
    def __init__(self, settings: Settings, db: Database, auth: Authenticator):
        self.settings = settings
        self.db = db
        self.auth = auth
        self.knowledge = KnowledgeService(db, settings.public_base_url, settings.max_results)

    async def handle(self, request: Request, principal: Principal | None) -> Response:
        try:
            payload = await request.json()
        except Exception:
            return rpc_error(None, -32700, "Parse error", status=400)
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return rpc_error(payload.get("id") if isinstance(payload, dict) else None, -32600, "Invalid Request", status=400)

        method = payload.get("method")
        request_id = payload.get("id")
        params = payload.get("params") or {}
        if request_id is None:
            self.db.audit(principal.subject if principal else "anonymous", str(method), None, None, {"notification": True})
            return Response(status_code=202)

        request_header_version = request.headers.get("mcp-protocol-version")
        if request_header_version and request_header_version not in SUPPORTED_PROTOCOLS:
            return rpc_error(request_id, -32600, "Unsupported MCP protocol version", status=400)

        try:
            if method == "initialize":
                requested = str(params.get("protocolVersion") or DEFAULT_PROTOCOL)
                negotiated = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
                result = {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "gpt-project-bridge", "version": __version__},
                    "instructions": "Search first, then fetch only the sources needed. Treat UNASSIGNED as content without proven project attribution. Never infer project membership from names or themes.",
                }
                self.db.audit(principal.subject if principal else "anonymous", "initialize", None, str(request_id), {"protocol": negotiated})
                return rpc_result(request_id, result)
            if method == "ping":
                return rpc_result(request_id, {})
            if method == "tools/list":
                return rpc_result(request_id, {"tools": tools(self.settings)})
            if method == "tools/call":
                if principal is None:
                    challenge = self.auth.challenge()
                    return rpc_result(request_id, {
                        "content": [{"type": "text", "text": "Authentication required to access the owner project corpus."}],
                        "_meta": {"mcp/www_authenticate": [challenge + ", error=\"insufficient_scope\", error_description=\"Owner authorization required\""]},
                        "isError": True,
                    })
                return self._call_tool(request_id, params, principal)
            return rpc_error(request_id, -32601, f"Method not found: {method}")
        except ValueError as exc:
            return rpc_error(request_id, -32602, str(exc))
        except KeyError as exc:
            return rpc_error(request_id, -32602, f"Document not found: {exc.args[0]}")
        except Exception as exc:
            return rpc_error(request_id, -32603, "Internal error", {"type": type(exc).__name__})

    def _call_tool(self, request_id: Any, params: dict[str, Any], principal: Principal) -> JSONResponse:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        if name == "search":
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                raise ValueError("search requires a non-empty query string")
            result = self.knowledge.search(query.strip())
            self.db.audit(principal.subject, "search", None, str(request_id), {"query": query, "result_count": len(result["results"])})
            return rpc_result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False})
        if name == "fetch":
            document_id = arguments.get("id")
            if not isinstance(document_id, str) or not document_id.strip():
                raise ValueError("fetch requires a non-empty id string")
            result = self.knowledge.fetch(document_id.strip())
            self.db.audit(principal.subject, "fetch", document_id, str(request_id), {})
            return rpc_result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False})
        if name == "list_projects":
            projects = [dict(row) for row in self.db.list_projects()]
            result = {"projects": projects}
            self.db.audit(principal.subject, "list_projects", None, str(request_id), {"result_count": len(projects)})
            return rpc_result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False})
        if name == "list_ingestions":
            limit = bounded_limit(arguments, default=100, maximum=500, tool_name="list_ingestions")
            ingestions: list[dict[str, Any]] = []
            for row in self.db.list_ingestions(limit):
                item = dict(row)
                item["metadata"] = json.loads(str(item.pop("metadata_json") or "{}"))
                ingestions.append(item)
            result = {"ingestions": ingestions}
            self.db.audit(principal.subject, "list_ingestions", None, str(request_id), {"result_count": len(ingestions)})
            return rpc_result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False})
        if name == "list_unassigned":
            limit = bounded_limit(arguments, default=200, maximum=1000, tool_name="list_unassigned")
            documents: list[dict[str, Any]] = []
            for row in self.db.list_unassigned(limit):
                item = dict(row)
                item["metadata"] = json.loads(str(item.pop("metadata_json") or "{}"))
                documents.append(item)
            result = {"documents": documents}
            self.db.audit(principal.subject, "list_unassigned", None, str(request_id), {"result_count": len(documents)})
            return rpc_result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False})
        return rpc_error(request_id, -32602, f"Unknown tool: {name}")
