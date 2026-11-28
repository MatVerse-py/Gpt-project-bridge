"""MatVerse local web operator: bounded HTTP, optional Chromium, PHOENIX receipts."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

from .phoenix_receipts import append_receipt, verify_receipts

VERSION = "0.2.0"
MAX_URL = 2048
MAX_BODY = 512 * 1024
MAX_TEXT = 24_000
HTTP_TIMEOUT = 12
NAME = "matverse_web_read"
HTTP_NAME = "matverse_http_read"
VERIFY_NAME = "matverse_phoenix_verify_receipts"

_READ_ANNOTATIONS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
TOOL = {
    "name": NAME,
    "description": "Read a page with optional local Chromium. Disabled by default; requires explicit operator opt-in. Page content is untrusted.",
    "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False},
    "annotations": dict(_READ_ANNOTATIONS),
}
HTTP_TOOL = {
    "name": HTTP_NAME,
    "description": "Read static HTML, text or JSON over a bounded direct HTTP GET. Uses an explicit origin allowlist, pinned DNS address, no redirects, and appends a PHOENIX receipt.",
    "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False},
    "annotations": dict(_READ_ANNOTATIONS),
}
VERIFY_TOOL = {
    "name": VERIFY_NAME,
    "description": "Verify the local PHOENIX web-receipt hash chain without modifying its entries.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
}


def origin(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > MAX_URL or any(ord(c) <= 32 for c in url) or "\\" in url:
        raise ValueError("invalid URL")
    try:
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None or not parsed.hostname:
            raise ValueError("invalid URL authority")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("HTTPS or explicit loopback HTTP required")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL authority") from exc
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    default = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != default else "")


def configured_origins() -> set[str]:
    raw = os.environ.get("MATVERSE_WEB_ORIGINS", "")
    if len(raw) > 8192:
        raise ValueError("origin allowlist is too large")
    allowed: set[str] = set()
    for item in raw.split(","):
        if not item.strip():
            continue
        candidate = item.strip()
        parsed = urlsplit(candidate)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("allowlist entries must be origins, not URLs with paths or queries")
        allowed.add(origin(candidate))
    return allowed


def public_url(url: str) -> str:
    parsed = urlsplit(url)
    safe_path = re.sub(r"(?<=/)[A-Za-z0-9._~-]{32,}(?=/|$)", "[REDACTED]", parsed.path)
    return urlunsplit((parsed.scheme, parsed.netloc, safe_path, "", ""))


def digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _resolve_pinned(host: str, port: int) -> str:
    """Resolve once, reject non-public DNS answers, and connect to the chosen IP directly."""
    loopback_names = {"localhost", "127.0.0.1", "::1"}
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("DNS resolution failed") from exc
        addresses = sorted({item[4][0].split("%", 1)[0] for item in infos})
    if not addresses:
        raise ValueError("host has no usable address")
    is_explicit_loopback = host.lower() in loopback_names
    parsed_addresses = []
    for address in addresses:
        try:
            parsed_addresses.append(ipaddress.ip_address(address))
        except ValueError as exc:
            raise ValueError("DNS returned an invalid address") from exc
    if is_explicit_loopback:
        if not all(item.is_loopback for item in parsed_addresses):
            raise ValueError("loopback hostname resolved outside loopback")
    elif not all(item.is_global for item in parsed_addresses):
        raise ValueError("origin resolved to a non-public, reserved or private address")
    return str(parsed_addresses[0])


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: int):
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: int):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        raw = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def _receipt_observation(url: str, status: int, content_type: str, raw: bytes, text: str, resolved_ip: str | None, tool: str) -> dict:
    parsed = urlsplit(url)
    return {
        "source": "MATVERSE_LOCAL_WEB",
        "tool": tool,
        "request": {
            "method": "GET",
            "origin": origin(url),
            "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
            "path_sha256": hashlib.sha256(parsed.path.encode("utf-8")).hexdigest(),
        },
        "response": {
            "status": status,
            "content_type": content_type,
            "bytes_read": len(raw),
            "body_sha256": hashlib.sha256(raw).hexdigest(),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text_truncated": len(text) > MAX_TEXT,
        },
        "network": {"resolved_ip": resolved_ip, "dns_pinned": resolved_ip is not None},
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "clock_trust": "LOCAL_CLOCK_UNVERIFIED",
        "classification": "OBSERVACAO_NAO_VERIFICADA",
        "trust": "UNTRUSTED_PAGE_CONTENT",
        "tool_version": VERSION,
    }


def http_read(args: dict) -> dict:
    if not isinstance(args, dict) or set(args) != {"url"}:
        raise ValueError("expected exactly one URL")
    url = args["url"]
    allowed = configured_origins()
    request_origin = origin(url)
    if request_origin not in allowed:
        raise ValueError("origin not enabled by local operator")
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    address = _resolve_pinned(parsed.hostname, port)
    connection_cls = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    connection = connection_cls(parsed.hostname, port, address, HTTP_TIMEOUT)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        connection.request("GET", target, headers={
            "Host": parsed.netloc,
            "User-Agent": "MatVerseLocalWeb/0.2",
            "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise ValueError("redirects are disabled; inspect the redirect target separately")
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "text/plain", "application/json"}:
            raise ValueError("unsupported content type")
        length = response.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            raise ValueError("response too large")
        deadline = time.monotonic() + HTTP_TIMEOUT
        chunks = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("response exceeded the overall read deadline")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(64 * 1024, MAX_BODY + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BODY:
                raise ValueError("response too large")
        raw = b"".join(chunks)
        text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        if content_type == "text/html":
            parser = TextParser()
            parser.feed(text)
            text = " ".join(parser.parts)
        observation = _receipt_observation(url, response.status, content_type, raw, text, address, HTTP_NAME)
        receipt = append_receipt(observation, raw_body=raw)
        return {
            "url": public_url(url),
            "status": response.status,
            "content_type": content_type,
            "text": text[:MAX_TEXT],
            "truncated": len(text) > MAX_TEXT,
            "body_sha256": observation["response"]["body_sha256"],
            "text_sha256": observation["response"]["text_sha256"],
            "observed_at": observation["observed_at"],
            "classification": "OBSERVACAO_NAO_VERIFICADA",
            "trust": "UNTRUSTED_PAGE_CONTENT",
            "receipt": receipt,
        }
    finally:
        connection.close()


def read_page(args: dict) -> dict:
    """Optional Chromium renderer; disabled by default and not a security sandbox."""
    if os.environ.get("MATVERSE_ENABLE_CHROMIUM") != "1":
        raise ValueError("Chromium is disabled by default; use matverse_http_read or explicitly enable the experimental renderer")
    if not isinstance(args, dict) or set(args) != {"url"}:
        raise ValueError("expected exactly one URL")
    url = args["url"]
    allowed = configured_origins()
    if origin(url) not in allowed:
        raise ValueError("origin not enabled by local operator")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(accept_downloads=False, service_workers="block", permissions=[])
            def guard(route):
                try:
                    permitted = route.request.method in {"GET", "HEAD"} and origin(route.request.url) in allowed
                except ValueError:
                    permitted = False
                route.continue_() if permitted else route.abort()
            context.route("**/*", guard)
            context.route_web_socket("**/*", lambda ws: ws.close())
            page = context.new_page()
            page.set_default_navigation_timeout(15000)
            page.set_default_timeout(5000)
            response = page.goto(url, wait_until="domcontentloaded")
            if response is None or 300 <= response.status < 400 or origin(page.url) not in allowed:
                raise ValueError("navigation failed or escaped the configured origin scope")
            text = page.locator("body").inner_text()
            title = page.title()[:500]
            body = text.encode("utf-8")
            observation = _receipt_observation(url, response.status, "text/html-rendered", body, text, None, NAME)
            observation["response"]["title_sha256"] = hashlib.sha256(title.encode("utf-8")).hexdigest()
            receipt = append_receipt(observation)
            return {
                "url": public_url(page.url), "status": response.status, "title": title,
                "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT,
                "text_sha256": observation["response"]["text_sha256"],
                "observed_at": observation["observed_at"],
                "classification": "OBSERVACAO_NAO_VERIFICADA",
                "trust": "UNTRUSTED_PAGE_CONTENT", "receipt": receipt,
                "renderer_security": "EXPERIMENTAL_ORIGIN_FILTER_ONLY_NOT_EGRESS_SANDBOX",
            }
        finally:
            browser.close()


def install(bridge):
    """Add local web and PHOENIX receipt tools to the existing Bridge MCP dispatcher."""
    tool_by_name = {item["name"]: item for item in bridge.TOOLS}
    for item in (TOOL, HTTP_TOOL, VERIFY_TOOL):
        if item["name"] not in tool_by_name:
            bridge.TOOLS.append(item)
    cls = bridge.BridgeMCP
    if getattr(cls, "_matverse_local_web_installed", False):
        return
    original = cls.call

    def call(self, name, args):
        if name == NAME:
            try:
                return read_page(args)
            except Exception:
                raise ValueError("local Chromium read failed; check opt-in, browser and origin policy") from None
        if name == HTTP_NAME:
            try:
                return http_read(args)
            except Exception:
                raise ValueError("local HTTP read failed; check origin policy, DNS and PHOENIX receipt store") from None
        if name == VERIFY_NAME:
            if args not in ({}, None):
                raise ValueError("receipt verification accepts no arguments")
            return verify_receipts()
        return original(self, name, args)

    cls.call = call
    cls._matverse_local_web_installed = True


def standalone():
    """Minimal stdio smoke runtime; production uses the existing Bridge lifecycle."""
    ready = False
    initialized = False
    tools = {NAME: read_page, HTTP_NAME: http_read, VERIFY_NAME: lambda args: verify_receipts()}
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("invalid request")
            method = request.get("method")
            if "id" not in request:
                if method == "notifications/initialized" and initialized:
                    ready = True
                continue
            base = {"jsonrpc": "2.0", "id": request["id"]}
            if method == "initialize":
                initialized = True
                result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "matverse-local-web", "version": VERSION}}
            elif method == "ping":
                result = {}
            elif ready and method == "tools/list":
                result = {"tools": [TOOL, HTTP_TOOL, VERIFY_TOOL]}
            elif ready and method == "tools/call":
                params = request.get("params", {})
                if not isinstance(params, dict) or params.get("name") not in tools:
                    raise ValueError("unknown tool")
                value = tools[params["name"]](params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
            else:
                print(json.dumps({**base, "error": {"code": -32601, "message": "unavailable method"}}), flush=True)
                continue
            print(json.dumps({**base, "result": result}, ensure_ascii=False), flush=True)
        except Exception:
            request_id = request.get("id") if isinstance(request, dict) else None
            print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "invalid request or local operation failed"}}), flush=True)


if __name__ == "__main__":
    if "--standalone" in sys.argv:
        standalone()
    else:
        from . import agent_mcp
        install(agent_mcp)
        agent_mcp.main()
