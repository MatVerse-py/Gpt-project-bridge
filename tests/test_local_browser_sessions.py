import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app import agent_mcp
from app.local_browser_sessions import (
    BrowserSessions,
    _CLOSE,
    _FOLLOW,
    _NAVIGATE,
    _OPEN,
    _SNAPSHOT,
    install,
    shutdown,
)


class FakeResponse:
    status = 200


class FakeRequest:
    def __init__(self, url, method="GET"):
        self.url = url
        self.method = method


class FakeRoute:
    def __init__(self, url, method="GET"):
        self.request = FakeRequest(url, method)
        self.action = None
    def continue_(self):
        self.action = "continue"
    def abort(self):
        self.action = "abort"


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
    def inner_text(self, timeout=None):
        return "MatVerse PHOENIX local browser fixture"
    def evaluate_all(self, script):
        if "map(e => e.href)" in script:
            return [self.page.url.rstrip("/") + "/next"]
        return [{"index": 0, "text": "Next", "href": self.page.url.rstrip("/") + "/next"}]


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.closed = False
    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        return FakeResponse()
    def title(self):
        return "MatVerse Browser Test"
    def locator(self, selector):
        return FakeLocator(self, selector)
    def is_closed(self):
        return self.closed
    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.routes = []
        self.page = None
        self.closed = False
    def route(self, pattern, handler):
        self.routes.append(handler)
    def route_web_socket(self, pattern, handler):
        pass
    def new_page(self):
        self.page = FakePage()
        return self.page
    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.contexts = []
        self.closed = False
    def new_context(self, **kwargs):
        context = FakeContext()
        self.contexts.append(context)
        return context
    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self):
        self.browser = FakeBrowser()
        self.persistent_context = FakeContext()
    def launch(self, **kwargs):
        return self.browser
    def launch_persistent_context(self, **kwargs):
        return self.persistent_context


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()
        self.stopped = False
    def stop(self):
        self.stopped = True


class FakePlaywrightManager:
    def __init__(self, instance):
        self.instance = instance
    def start(self):
        return self.instance


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_env = dict(os.environ)
        os.environ["MATVERSE_PHOENIX_RECEIPTS_DIR"] = self.temp.name
        os.environ["MATVERSE_WEB_ORIGINS"] = "https://example.test"
        os.environ["MATVERSE_BROWSER_PERSISTENT"] = "0"
        os.environ["MATVERSE_BROWSER_HEADLESS"] = "1"
        shutdown()
        self.fake = FakePlaywright()
        self.fake_sync = types.ModuleType("playwright.sync_api")
        self.fake_sync.sync_playwright = lambda: FakePlaywrightManager(self.fake)
        self.fake_pkg = types.ModuleType("playwright")
        self.fake_pkg.sync_api = self.fake_sync
        self.modules_patch = patch.dict(sys.modules, {"playwright": self.fake_pkg, "playwright.sync_api": self.fake_sync})
        self.modules_patch.start()

    def tearDown(self):
        shutdown()
        self.modules_patch.stop()
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temp.cleanup()

    def test_session_lifecycle_and_link_navigation(self):
        runtime = BrowserSessions()
        opened = runtime.open({"url": "https://example.test/start"})
        self.assertIn("MatVerse PHOENIX", opened["text"])
        self.assertEqual(opened["links"][0]["index"], 0)
        sid = opened["session_id"]
        snap = runtime.snapshot({"session_id": sid})
        self.assertEqual(snap["url"], "https://example.test/start")
        followed = runtime.follow_link({"session_id": sid, "link_index": 0})
        self.assertEqual(followed["url"], "https://example.test/start/next")
        navigated = runtime.navigate({"session_id": sid, "url": "https://example.test/final"})
        self.assertEqual(navigated["url"], "https://example.test/final")
        self.assertTrue(runtime.close({"session_id": sid})["closed"])
        with self.assertRaises(ValueError):
            runtime.snapshot({"session_id": sid})
        runtime.shutdown()

    def test_bridge_dispatches_browser_tools(self):
        import app.matverse_mcp  # noqa: F401 — exercise the production unified launcher
        bridge = agent_mcp.BridgeMCP()
        bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = bridge.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {item["name"] for item in listed["result"]["tools"]}
        self.assertTrue({_OPEN, _SNAPSHOT, _FOLLOW, _NAVIGATE, _CLOSE}.issubset(names))
        result = bridge.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": _OPEN, "arguments": {"url": "https://example.test/"}}})
        self.assertFalse(result["result"]["isError"])
        data = json.loads(result["result"]["content"][0]["text"])
        closed = bridge.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": _CLOSE, "arguments": {"session_id": data["session_id"]}}})
        self.assertFalse(closed["result"]["isError"])

    def test_route_policy_allows_get_and_blocks_post_and_external_origin(self):
        runtime = BrowserSessions()
        opened = runtime.open({"url": "https://example.test/"})
        context = self.fake.chromium.browser.contexts[-1]
        guard = context.routes[0]
        allowed = FakeRoute("https://example.test/page", "GET")
        denied_method = FakeRoute("https://example.test/submit", "POST")
        denied_origin = FakeRoute("https://outside.test/", "GET")
        guard(allowed); guard(denied_method); guard(denied_origin)
        self.assertEqual(allowed.action, "continue")
        self.assertEqual(denied_method.action, "abort")
        self.assertEqual(denied_origin.action, "abort")
        runtime.close({"session_id": opened["session_id"]})
        runtime.shutdown()

    def test_unlisted_origin_rejected_before_browser_start(self):
        runtime = BrowserSessions()
        with self.assertRaises(ValueError):
            runtime.open({"url": "https://outside.test/"})
        self.assertIsNone(runtime._playwright)
        runtime.shutdown()

    def test_persistent_profile_requires_explicit_opt_in(self):
        os.environ["MATVERSE_BROWSER_PERSISTENT"] = "1"
        os.environ["MATVERSE_BROWSER_PROFILE_DIR"] = str(Path(self.temp.name) / "profile")
        runtime = BrowserSessions()
        opened = runtime.open({"url": "https://example.test/"})
        self.assertTrue(self.fake.chromium.persistent_context.routes)
        self.assertTrue(runtime._sessions[opened["session_id"]]["persistent"])
        runtime.close({"session_id": opened["session_id"]})
        self.assertFalse(self.fake.chromium.persistent_context.closed)
        runtime.shutdown()
        self.assertTrue(self.fake.chromium.persistent_context.closed)

    def test_session_identifier_is_not_a_path(self):
        runtime = BrowserSessions()
        with self.assertRaises(ValueError):
            runtime.snapshot({"session_id": "../../etc/passwd"})
        runtime.shutdown()


if __name__ == "__main__":
    unittest.main()
