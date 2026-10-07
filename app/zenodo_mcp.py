from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from app.zenodo_bridge import ZenodoAuthError, ZenodoBridgeError, runtime_from_env

mcp = MCPServer(
    "MatVerse Zenodo Bridge",
    instructions=(
        "Governed Zenodo access for MatVerse. Read tools inspect public records and the "
        "authorized user's depositions. Draft creation is a write. Publishing is irreversible "
        "and additionally requires the exact human confirmation phrase returned by the tool."
    ),
)


def _runtime():
    return runtime_from_env()


@mcp.tool()
def zenodo_connection_status() -> dict[str, Any]:
    """Check whether a valid encrypted Zenodo OAuth session is available."""
    try:
        _, store, _, _, _ = _runtime()
        return store.status()
    except ZenodoAuthError as exc:
        return {"connected": False, "error": str(exc)}


@mcp.tool()
def zenodo_oauth_start_url() -> dict[str, Any]:
    """Return the local OAuth start URL. Open it in a browser to connect Zenodo."""
    port = int(os.getenv("ZENODO_OAUTH_PORT", "8788"))
    public_base = os.getenv("ZENODO_OAUTH_PUBLIC_BASE", f"http://localhost:{port}").rstrip("/")
    scopes = os.getenv("ZENODO_SCOPES", "deposit:write").split()
    return {
        "url": f"{public_base}/zenodo/oauth/start",
        "requested_scopes": scopes,
        "note": "The Zenodo client secret is never returned by this tool.",
    }


@mcp.tool()
def zenodo_search_records(q: str = "", page: int = 1, size: int = 20) -> Any:
    """Search published Zenodo records."""
    _, _, _, _, api = _runtime()
    return api.search_records(q=q, page=page, size=size)


@mcp.tool()
def zenodo_get_record(record_id: str) -> Any:
    """Retrieve one published Zenodo record by record identifier."""
    _, _, _, _, api = _runtime()
    return api.get_record(record_id)


@mcp.tool()
def zenodo_list_depositions(
    q: str = "",
    status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> Any:
    """List the connected user's Zenodo deposits/drafts."""
    _, _, _, _, api = _runtime()
    return api.list_depositions(q=q, status=status, page=page, size=size)


@mcp.tool()
def zenodo_get_deposition(deposition_id: int) -> Any:
    """Retrieve one deposit/draft belonging to the connected Zenodo account."""
    _, _, _, _, api = _runtime()
    return api.get_deposition(deposition_id)


@mcp.tool()
def zenodo_create_draft(metadata: dict[str, Any]) -> Any:
    """Create an unpublished Zenodo draft. This changes the user's Zenodo account."""
    _, _, _, _, api = _runtime()
    return api.create_draft(metadata)


@mcp.tool()
def zenodo_publish_draft(deposition_id: int, confirmation: str = "") -> Any:
    """Publish a Zenodo draft irreversibly.

    The exact confirmation phrase is: PUBLISH ZENODO <deposition_id>.
    The OAuth session must also include deposit:actions.
    """
    _, _, _, _, api = _runtime()
    return api.publish_draft(deposition_id, confirmation)


if __name__ == "__main__":
    host = os.getenv("ZENODO_MCP_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"} and os.getenv(
        "ZENODO_MCP_REMOTE_AUTH_READY", ""
    ) != "1":
        raise RuntimeError(
            "Refusing unauthenticated remote MCP exposure. "
            "Keep ZENODO_MCP_HOST local or configure remote MCP authentication first."
        )

    mcp.run(
        transport="streamable-http",
        host=host,
        port=int(os.getenv("ZENODO_MCP_PORT", "8800")),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )
