"""Local PHOENIX receipt journal for MatVerse web observations.

The journal is append-only at the application layer and tamper-evident, not immutable.
It stores metadata and hashes by default; captured response bodies are opt-in.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "matverse.phoenix.web-receipt/v1"
GENESIS = "GENESIS"
MAX_LEDGER_BYTES = 64 * 1024 * 1024


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _store_dir() -> Path:
    raw = os.environ.get("MATVERSE_PHOENIX_RECEIPTS_DIR", "data/phoenix/web-receipts")
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("PHOENIX receipt directory must be a real directory")
    return path


def _open_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, mode)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise ValueError("PHOENIX journal path is not a regular file")
    return fd


@contextmanager
def _exclusive_lock(path: Path):
    lock_path = path.with_name(path.name + ".lock")
    fd = _open_nofollow(lock_path, os.O_CREAT | os.O_RDWR)
    with os.fdopen(fd, "r+b", closefd=True) as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _journal_path() -> Path:
    return _store_dir() / "web_receipts.jsonl"


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("PHOENIX journal cannot be a symbolic link")
    fd = _open_nofollow(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > MAX_LEDGER_BYTES:
            raise ValueError("PHOENIX journal exceeds the verification size limit")
        raw = stream.read(MAX_LEDGER_BYTES + 1)
    if len(raw) > MAX_LEDGER_BYTES:
        raise ValueError("PHOENIX journal exceeds the verification size limit")
    if not raw:
        return []
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line:
            raise ValueError(f"empty journal line at {line_number}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"invalid journal entry at {line_number}")
        entries.append(value)
    return entries


def _verify_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    previous = GENESIS
    for index, entry in enumerate(entries, start=1):
        supplied = entry.get("entry_sha256")
        if entry.get("schema") != SCHEMA or entry.get("sequence") != index:
            return {"valid": False, "entries": index - 1, "head": previous, "error": f"schema/sequence mismatch at {index}"}
        if entry.get("previous_sha256") != previous:
            return {"valid": False, "entries": index - 1, "head": previous, "error": f"previous hash mismatch at {index}"}
        base = dict(entry)
        base.pop("entry_sha256", None)
        expected = sha256_bytes(canonical_bytes(base))
        if supplied != expected:
            return {"valid": False, "entries": index - 1, "head": previous, "error": f"entry hash mismatch at {index}"}
        previous = supplied
    return {"valid": True, "entries": len(entries), "head": previous, "error": None}


def verify_receipts() -> dict[str, Any]:
    """Verify sequence, previous-hash links and each entry digest."""
    path = _journal_path()
    try:
        with _exclusive_lock(path):
            entries = _read_entries(path)
        result = _verify_entries(entries)
        return {**result, "journal": str(path)}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"valid": False, "entries": 0, "head": GENESIS, "error": str(exc), "journal": str(path)}


def _write_content_object(body: bytes, body_hash: str) -> str:
    objects = _store_dir() / "objects"
    objects.mkdir(parents=True, exist_ok=True, mode=0o700)
    if objects.is_symlink() or not objects.is_dir():
        raise ValueError("PHOENIX objects path must be a real directory")
    target = objects / f"{body_hash}.bin"
    if target.is_symlink():
        raise ValueError("content-addressed PHOENIX object cannot be a symbolic link")
    try:
        fd = _open_nofollow(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if target.is_symlink():
            raise ValueError("content-addressed PHOENIX object cannot be a symbolic link")
        existing = target.read_bytes()
        if sha256_bytes(existing) != body_hash:
            raise ValueError("content-addressed PHOENIX object failed integrity check")
        return str(target)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    return str(target)


def append_receipt(observation: dict[str, Any], *, raw_body: bytes | None = None) -> dict[str, Any]:
    """Append an observation receipt; never store body bytes unless explicitly opted in."""
    if not isinstance(observation, dict):
        raise ValueError("observation must be an object")
    path = _journal_path()
    body_hash = observation.get("response", {}).get("body_sha256") if isinstance(observation.get("response"), dict) else None
    object_ref = None
    if raw_body is not None and os.environ.get("MATVERSE_PHOENIX_CAPTURE_BODY", "0") == "1":
        actual = sha256_bytes(raw_body)
        if body_hash != actual:
            raise ValueError("captured body does not match the observation hash")
        object_ref = _write_content_object(raw_body, actual)

    with _exclusive_lock(path):
        entries = _read_entries(path)
        checked = _verify_entries(entries)
        if not checked["valid"]:
            raise ValueError("PHOENIX receipt journal is corrupt; refusing append: " + str(checked["error"]))
        previous = entries[-1]["entry_sha256"] if entries else GENESIS
        entry: dict[str, Any] = {
            "schema": SCHEMA,
            "receipt_id": str(uuid.uuid4()),
            "sequence": len(entries) + 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "clock_trust": "LOCAL_CLOCK_UNVERIFIED",
            "previous_sha256": previous,
            "observation": observation,
            "object_ref": object_ref,
            "runtime": {"python": platform.python_version(), "platform": platform.system()},
        }
        entry["entry_sha256"] = sha256_bytes(canonical_bytes(entry))
        line = canonical_bytes(entry) + b"\n"
        fd = _open_nofollow(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY)
        with os.fdopen(fd, "ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    return {
        "receipt_id": entry["receipt_id"],
        "sequence": entry["sequence"],
        "entry_sha256": entry["entry_sha256"],
        "previous_sha256": entry["previous_sha256"],
        "journal": str(path),
        "object_ref": object_ref,
    }
