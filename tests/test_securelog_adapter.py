"""Isolated, network-free tests for opt-in SecureLog integration."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.securelog_adapter import configure_securelog


def _paths(root: Path) -> tuple[Path, Path]:
    return root / "logs" / "audit.fernetlog", root / "keys" / "keys.json"


def test_off_by_default_has_no_effect():
    with patch.dict(os.environ, {}, clear=True):
        app = FastAPI()
        assert configure_securelog(app, component="bridge_api") is False
        assert not getattr(app.state, "securelog_enabled", False)


@pytest.mark.parametrize("mode", ["yes", "auto", "true"])
def test_unknown_mode_is_rejected(mode):
    with patch.dict(os.environ, {"MATVERSE_SECURELOG_MODE": mode}, clear=True):
        with pytest.raises(RuntimeError, match="must be off or enforce"):
            configure_securelog(FastAPI(), component="bridge_api")


def test_enforce_rejects_uninitialized_storage():
    with tempfile.TemporaryDirectory() as root:
        log, key = _paths(Path(root))
        env = {"MATVERSE_SECURELOG_MODE": "enforce",
               "MATVERSE_SECURELOG_KEYRING_PATH": str(key),
               "MATVERSE_SECURELOG_LOG_PATH": str(log)}
        with patch.dict(os.environ, env, clear=True), pytest.raises(Exception):
            configure_securelog(FastAPI(), component="bridge_api")
        assert not log.exists()
        assert not key.exists()


def test_enforce_emits_only_encrypted_categorical_http_events():
    SecureLog = pytest.importorskip("matverse_securelog").SecureLog
    with tempfile.TemporaryDirectory() as root:
        log, key = _paths(Path(root))
        SecureLog.initialize(log, key)
        env = {"MATVERSE_SECURELOG_MODE": "enforce",
               "MATVERSE_SECURELOG_KEYRING_PATH": str(key),
               "MATVERSE_SECURELOG_LOG_PATH": str(log)}
        with patch.dict(os.environ, env, clear=True):
            app = FastAPI()

            @app.get("/secret-path")
            def read():
                return {"status": "ok"}

            assert configure_securelog(app, component="bridge_api") is True
            with TestClient(app) as client:
                res = client.get(
                    "/secret-path?password=never-log-me",
                    headers={"Authorization": "Bearer never-log"},
                )
            assert res.status_code == 200
            raw = log.read_bytes()
            assert b"secret-path" not in raw and b"never-log" not in raw
            records = SecureLog(log, key).recent()
            events = [record["event"] for record in records]
            assert "api.request_started" in events and "api.request_finished" in events
            assert all("Authorization" not in str(item["fields"]) for item in records)


def test_failure_to_emit_blocks_handler_execution():
    SecureLog = pytest.importorskip("matverse_securelog").SecureLog
    with tempfile.TemporaryDirectory() as root:
        log, key = _paths(Path(root))
        SecureLog.initialize(log, key)
        env = {"MATVERSE_SECURELOG_MODE": "enforce",
               "MATVERSE_SECURELOG_KEYRING_PATH": str(key),
               "MATVERSE_SECURELOG_LOG_PATH": str(log)}
        with patch.dict(os.environ, env, clear=True):
            app = FastAPI()
            called = []

            @app.get("/commit")
            def commit():
                called.append(True)
                return {"ok": True}

            configure_securelog(app, component="bridge_api")
            log.write_bytes(log.read_bytes().replace(b"g", b"z", 1))
            with TestClient(app, raise_server_exceptions=True) as client:
                with pytest.raises(Exception):
                    client.get("/commit")
            assert called == []
