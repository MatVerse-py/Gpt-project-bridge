import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.local_web_mcp import HTTP_NAME, NAME, VERIFY_NAME, _resolve_pinned, http_read, install, origin
from app.phoenix_receipts import append_receipt, verify_receipts


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/")
            self.end_headers()
            return
        if self.path == "/large":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(600 * 1024))
            self.end_headers()
            try:
                self.wfile.write(b"x" * (600 * 1024))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'<title>MatVerse</title><body><h1>PHOENIX</h1><script>SECRET_IN_SCRIPT</script><a href="/next">next</a></body>')

    def log_message(self, *args):
        pass


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_env = dict(os.environ)
        os.environ["MATVERSE_PHOENIX_RECEIPTS_DIR"] = self.temp.name
        os.environ["MATVERSE_WEB_ORIGINS"] = self.url
        os.environ.pop("MATVERSE_PHOENIX_CAPTURE_BODY", None)
        os.environ.pop("MATVERSE_ENABLE_CHROMIUM", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temp.cleanup()

    def test_http_read_uses_pinned_loopback_and_writes_phoenix_receipt(self):
        result = http_read({"url": self.url + "/page"})
        self.assertIn("PHOENIX", result["text"])
        self.assertNotIn("SECRET_IN_SCRIPT", result["text"])
        self.assertEqual(result["classification"], "OBSERVACAO_NAO_VERIFICADA")
        self.assertEqual(result["receipt"]["sequence"], 1)
        self.assertTrue(verify_receipts()["valid"])

    def test_http_redirect_denied(self):
        with self.assertRaises(ValueError):
            http_read({"url": self.url + "/redirect"})

    def test_http_response_size_bounded(self):
        with self.assertRaises(ValueError):
            http_read({"url": self.url + "/large"})

    def test_origin_allowlist_required(self):
        with self.assertRaises(ValueError):
            http_read({"url": "https://example.org/"})

    def test_dns_private_address_rejected_for_non_loopback_host(self):
        fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 443))]
        with patch("app.local_web_mcp.socket.getaddrinfo", return_value=fake):
            with self.assertRaises(ValueError):
                _resolve_pinned("attacker.example", 443)

    def test_dns_public_address_is_pinned(self):
        fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("app.local_web_mcp.socket.getaddrinfo", return_value=fake):
            self.assertEqual(_resolve_pinned("example.org", 443), "93.184.216.34")

    def test_url_validation_rejects_credentials_and_non_https_remote(self):
        for url in ("file:///etc/passwd", "https://user:pass@example.org", "http://example.org", "https://example.org\\@bad"):
            with self.assertRaises(ValueError):
                origin(url)

    def test_phoenix_ledger_detects_tampering_and_refuses_append(self):
        append_receipt({"response": {"body_sha256": "abc"}, "source": "test"})
        self.assertTrue(verify_receipts()["valid"])
        ledger = Path(self.temp.name) / "web_receipts.jsonl"
        data = json.loads(ledger.read_text(encoding="utf-8"))
        data["observation"]["source"] = "tampered"
        ledger.write_text(json.dumps(data) + "\n", encoding="utf-8")
        self.assertFalse(verify_receipts()["valid"])
        with self.assertRaises(ValueError):
            append_receipt({"source": "must-not-append"})

    def test_content_capture_is_opt_in_and_content_addressed(self):
        body = b"immutable original bytes"
        import hashlib
        os.environ["MATVERSE_PHOENIX_CAPTURE_BODY"] = "1"
        result = append_receipt({"response": {"body_sha256": hashlib.sha256(body).hexdigest()}}, raw_body=body)
        self.assertTrue(Path(result["object_ref"]).exists())
        self.assertEqual(Path(result["object_ref"]).read_bytes(), body)

    def test_bridge_install_is_idempotent_and_preserves_dispatch(self):
        class Bridge:
            def call(self, name, args):
                return "original"
        module = SimpleNamespace(TOOLS=[], BridgeMCP=Bridge)
        install(module)
        install(module)
        names = [item["name"] for item in module.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue({NAME, HTTP_NAME, VERIFY_NAME}.issubset(set(names)))
        self.assertEqual(Bridge().call("old", {}), "original")
        self.assertTrue(Bridge().call(VERIFY_NAME, {})["valid"])

    def test_existing_bridge_mcp_dispatches_local_web_without_remote_credentials(self):
        from app import agent_mcp
        install(agent_mcp)
        bridge = agent_mcp.BridgeMCP()
        initialized = bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
        bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = bridge.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertTrue({NAME, HTTP_NAME, VERIFY_NAME}.issubset(names))
        called = bridge.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": HTTP_NAME, "arguments": {"url": self.url + "/page"}}})
        self.assertFalse(called["result"]["isError"])
        result = json.loads(called["result"]["content"][0]["text"])
        self.assertIn("PHOENIX", result["text"])
        self.assertEqual(result["classification"], "OBSERVACAO_NAO_VERIFICADA")
        verified = bridge.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": VERIFY_NAME, "arguments": {}}})
        self.assertTrue(json.loads(verified["result"]["content"][0]["text"])["valid"])

    def test_query_secret_is_not_written_to_receipt(self):
        secret = "do-not-persist-this-token-123456789"
        result = http_read({"url": self.url + "/page?access_token=" + secret})
        self.assertNotIn(secret, result["url"])
        ledger = (Path(self.temp.name) / "web_receipts.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(secret, ledger)

    def test_concurrent_appends_preserve_chain(self):
        def append(index):
            return append_receipt({"source": "concurrency-test", "index": index})
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(append, range(24)))
        result = verify_receipts()
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["entries"], 24)

    def test_chromium_disabled_by_default(self):
        from app.local_web_mcp import read_page
        with self.assertRaises(ValueError):
            read_page({"url": self.url})

    def test_mcp_http_smoke(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": HTTP_NAME, "arguments": {"url": self.url + "/page"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": VERIFY_NAME, "arguments": {}}},
        ]
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, "-m", "app.local_web_mcp", "--standalone"],
            input="\n".join(json.dumps(item) for item in requests) + "\n",
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
            check=True,
        )
        responses = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(responses[1]["result"]["tools"][2]["name"], VERIFY_NAME)
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertTrue(json.loads(responses[3]["result"]["content"][0]["text"])["valid"])


if __name__ == "__main__":
    unittest.main()
