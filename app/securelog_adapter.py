"""Opt-in encrypted security telemetry for MatVerse HTTP entrypoints.

Does not authorize requests, modify constitutional policy, or create key material.
Activation is explicit. A misconfigured enabled audit aborts application startup.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

VALID_MODES = frozenset({"off", "enforce"})


def _absolute_path(name: str) -> Path:
    raw = os.environ.get(name, "")
    if not raw:
        raise RuntimeError(f"{name} is required when MATVERSE_SECURELOG_MODE=enforce")
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError(f"{name} must be an absolute path")
    return path


def configure_securelog(app: Any, *, component: str) -> bool:
    """Install encrypted HTTP lifecycle audit if explicitly enabled.

    Disabled mode is dependency-free. Enabled mode requires an initialized
    private journal and keyring, and never initializes/replaces them.
    """
    mode = os.environ.get("MATVERSE_SECURELOG_MODE", "off").strip().lower()
    if mode not in VALID_MODES:
        raise RuntimeError("MATVERSE_SECURELOG_MODE must be off or enforce")
    if mode == "off":
        return False

    key_path = _absolute_path("MATVERSE_SECURELOG_KEYRING_PATH")
    log_path = _absolute_path("MATVERSE_SECURELOG_LOG_PATH")
    raw_limit = os.environ.get("MATVERSE_SECURELOG_MAX_BYTES", "16777216")
    try:
        byte_limit = int(raw_limit)
    except ValueError as exc:
        raise RuntimeError("MATVERSE_SECURELOG_MAX_BYTES must be an integer") from exc
    if byte_limit < 1024 or byte_limit > 1_073_741_824:
        raise RuntimeError("MATVERSE_SECURELOG_MAX_BYTES outside 1024..1073741824")

    try:
        from matverse_securelog import SecureLog
        from matverse_securelog.fastapi_adapter import install_fastapi_audit
    except ImportError as exc:
        raise RuntimeError("Install the reviewed matverse-fernet-logs runtime before enabling audit") from exc

    journal = SecureLog(log_path, key_path, max_bytes=byte_limit)
    journal.verify()  # fail closed on tampering, missing files, bad keys
    install_fastapi_audit(app, journal, component=component)
    app.state.securelog_enabled = True
    app.state.securelog_journal = journal
    return True
