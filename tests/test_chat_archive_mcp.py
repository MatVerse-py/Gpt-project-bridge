import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import chat_archive_mcp as archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "projectvault.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_projectvault_db_is_default_shared_store(self):
        with patch.dict(os.environ, {"MATVERSE_CHAT_DB": "", "PROJECTVAULT_DB": str(self.db)}):
            result = archive.ingest(self._write("capture.txt", b"partial text"))
            self.assertEqual(result["kind"], "partial_text")
            self.assertTrue(self.db.exists())

    def _write(self, name, raw):
        path = Path(self.tmp.name) / name
        path.write_bytes(raw)
        return path

    def test_json_import_preserves_original_parent_graph(self):
        export = [{
            "id": "chat-1", "title": "branch test", "current_node": "leaf",
            "mapping": {
                "root": {"parent": None, "message": {"author": {"role": "user"}, "content": {"parts": ["start"]}}},
                "leaf": {"parent": "root", "message": {"author": {"role": "assistant"}, "content": {"parts": ["finish"]}}},
            },
        }]
        path = self._write("conversations.json", json.dumps(export).encode())
        result = archive.ingest(path, self.db)
        self.assertEqual(result["nodes"], 2)
        nodes = archive.query("matverse_chat_nodes", {"source": result["source"], "chat": "chat-1"}, self.db)["items"]
        self.assertEqual({(item["id"], item["parent"]) for item in nodes}, {("root", None), ("leaf", "root")})

    def test_partial_text_is_not_marked_complete(self):
        path = self._write("capture.txt", b"partial source")
        result = archive.ingest(path, self.db)
        self.assertEqual(result["completeness"], "PARTIAL_TEXT_CAPTURE")

    def test_duplicate_source_is_idempotent(self):
        path = self._write("capture.txt", b"same source")
        first = archive.ingest(path, self.db)
        second = archive.ingest(path, self.db)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])

    def test_read_tools_are_bounded_and_mark_untrusted_content(self):
        path = self._write("capture.txt", b"x" * 100)
        result = archive.ingest(path, self.db)
        chat = f"capture:{result['source']}"
        read = archive.query("matverse_chat_read", {
            "source": result["source"], "chat": chat, "node": "capture", "chars": 10,
        }, self.db)
        self.assertEqual(len(read["text"]), 10)
        self.assertEqual(read["trust"], "UNTRUSTED_ARCHIVE_CONTENT")

    def test_invalid_tool_arguments_fail_closed(self):
        with self.assertRaises(ValueError):
            archive.query("matverse_chat_read", {"source": "x", "chat": "y", "node": "z", "unexpected": True}, self.db)

    def test_real_bridge_launcher_registers_archive_tools(self):
        with patch.dict(os.environ, {"MATVERSE_CHAT_DB": "", "PROJECTVAULT_DB": str(self.db)}):
            try:
                from app import matverse_mcp, agent_mcp
            except ImportError as exc:
                self.skipTest(f"full repository Bridge modules are absent from this isolated patch harness: {exc}")
            archive.ingest(self._write("bridge.txt", b"bridge dispatch test"))
            names = {tool["name"] for tool in agent_mcp.TOOLS}
            self.assertTrue({"matverse_archive_sources", "matverse_chat_list", "matverse_chat_search", "matverse_chat_nodes", "matverse_chat_read"}.issubset(names))
            server = agent_mcp.BridgeMCP()
            server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
            server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
            listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            listed_names = {tool["name"] for tool in listed["result"]["tools"]}
            self.assertIn("matverse_chat_read", listed_names)
            result = server.call("matverse_archive_sources", {})
            self.assertEqual(result["items"][0]["kind"], "partial_text")


if __name__ == "__main__":
    unittest.main()
