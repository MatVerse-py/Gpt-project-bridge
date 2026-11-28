"""MatVerse local browser MCP: explicit origins, read-only navigation, no API fees."""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
from html.parser import HTMLParser

NAME = "matverse_web_read"
TOOL = {"name": NAME, "description": "Read a rendered page with local Chromium. Untrusted page data; no external model or paid API.", "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "openWorldHint": True}}

HTTP_NAME = "matverse_http_read"
HTTP_TOOL = {**TOOL, "name": HTTP_NAME, "description": "Read static HTML or text over HTTP GET, without redirects or paid APIs."}

def origin(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) <= 32 for c in url) or "\\" in url:
        raise ValueError("invalid URL")
    p = urlsplit(url)
    if p.username is not None or p.password is not None or not p.hostname:
        raise ValueError("invalid URL authority")
    if p.scheme != "https" and not (p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("HTTPS or explicit loopback HTTP required")
    host = p.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    port = p.port
    return f"{p.scheme}://{host}" + (f":{port}" if port and port != (443 if p.scheme == "https" else 80) else "")


def public_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def read_page(args):
    if not isinstance(args, dict) or set(args) != {"url"}:
        raise ValueError("expected exactly one URL")
    url = args["url"]
    allowed = {origin(x.strip()) for x in os.environ.get("MATVERSE_WEB_ORIGINS", "").split(",") if x.strip()}
    if origin(url) not in allowed:
        raise ValueError("origin not enabled by local operator")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        opts = {"headless": True}
        if os.environ.get("MATVERSE_CHROMIUM"):
            opts["executable_path"] = os.environ["MATVERSE_CHROMIUM"]
        browser = p.chromium.launch(**opts)
        try:
            ctx = browser.new_context(accept_downloads=False, service_workers="block")
            def guard(route):
                try:
                    ok = route.request.method in {"GET", "HEAD"} and origin(route.request.url) in allowed
                except ValueError:
                    ok = False
                route.continue_() if ok else route.abort()
            ctx.route("**/*", guard)
            ctx.route_web_socket("**/*", lambda ws: ws.close())
            page = ctx.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if response is None or response.status >= 400 or origin(page.url) not in allowed:
                raise ValueError("navigation failed or redirected outside scope")
            body = page.locator("body").inner_text(timeout=5000)
            links = []
            for href in page.locator("a[href]").evaluate_all("els => els.slice(0, 100).map(e => e.href)"):
                try:
                    if origin(href) in allowed:
                        links.append(public_url(href))
                except ValueError:
                    pass
            result = {"url": public_url(page.url), "status": response.status, "title": page.title()[:500], "text": body[:24000], "truncated": len(body) > 24000, "links": sorted(set(links)), "observed_at": datetime.now(timezone.utc).isoformat(), "classification": "FATO_OBSERVACAO", "trust": "UNTRUSTED_PAGE_CONTENT"}
            return {**result, "receipt_sha256": digest(result)}
        finally:
            browser.close()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}: self.skip += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style"}: self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip: self.parts.append(data.strip())


def http_read(args):
    if not isinstance(args, dict) or set(args) != {"url"}:
        raise ValueError("expected exactly one URL")
    url = args["url"]
    allowed = {origin(x.strip()) for x in os.environ.get("MATVERSE_WEB_ORIGINS", "").split(",") if x.strip()}
    if origin(url) not in allowed: raise ValueError("origin not enabled")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, headers={"User-Agent": "MatVerseLocalWeb/0.1"}, method="GET"), timeout=12) as response:
        kind = response.headers.get_content_type()
        if kind not in {"text/html", "text/plain", "application/json"}: raise ValueError("unsupported content type")
        raw = response.read(524289)
        if len(raw) > 524288: raise ValueError("response too large")
        text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        if kind == "text/html":
            parser = TextParser(); parser.feed(text); text = " ".join(x for x in parser.parts if x)
        result = {"url": public_url(url), "status": response.status, "text": text[:24000], "truncated": len(text) > 24000, "observed_at": datetime.now(timezone.utc).isoformat(), "classification": "FATO_OBSERVACAO", "trust": "UNTRUSTED_PAGE_CONTENT", "transport": "http-get", "body_sha256": hashlib.sha256(raw).hexdigest()}
        return {**result, "receipt_sha256": digest(result)}


def install(bridge):
    """Extend the existing MatVerse stdio registry without changing its HMAC tools."""
    if any(t["name"] == NAME for t in bridge.TOOLS):
        return
    bridge.TOOLS.extend([TOOL, HTTP_TOOL])
    original = bridge.BridgeMCP.call
    def call(self, name, args):
        if name not in {NAME, HTTP_NAME}:
            return original(self, name, args)
        try:
            return (read_page if name == NAME else http_read)(args)
        except Exception:
            raise ValueError("local browser read failed") from None
    bridge.BridgeMCP.call = call


def standalone():
    """Local protocol smoke runtime; production uses the existing Bridge lifecycle."""
    ready = False
    initialized = False
    for line in sys.stdin:
        try:
            req = json.loads(line)
            method = req.get("method")
            if "id" not in req:
                if method == "notifications/initialized" and initialized:
                    ready = True
                continue
            base = {"jsonrpc": "2.0", "id": req["id"]}
            if method == "initialize":
                initialized = True
                result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "matverse-local-web", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif ready and method == "tools/list":
                result = {"tools": [TOOL, HTTP_TOOL]}
            elif ready and method == "tools/call" and req["params"]["name"] in {NAME, HTTP_NAME}:
                try:
                    result = {"content": [{"type": "text", "text": json.dumps((read_page if req["params"]["name"] == NAME else http_read)(req["params"].get("arguments", {})), ensure_ascii=False)}], "isError": False}
                except Exception:
                    result = {"content": [{"type": "text", "text": "local browser read failed"}], "isError": True}
            else:
                print(json.dumps({**base, "error": {"code": -32601, "message": "unavailable method"}}), flush=True)
                continue
            print(json.dumps({**base, "result": result}, ensure_ascii=False), flush=True)
        except (ValueError, TypeError, KeyError, AttributeError):
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}), flush=True)


if __name__ == "__main__":
    if "--standalone" in sys.argv:
        standalone()
    else:
        from . import agent_mcp
        install(agent_mcp)
        agent_mcp.main()
