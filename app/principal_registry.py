from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from . import storage
from .institutional_state_client import (
    InstitutionalStateRejected,
    InstitutionalStateUnavailable,
    fetch_remote_auth_credential,
    remote_state_enabled,
)

ED25519_AUTH_SCHEME = "ED25519-PUBLIC-KEY-V1"
BOOTSTRAP_ROOT_ENV = "MATVERSE_BOOTSTRAP_ROOT_JSON"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PUBLIC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID_RE = re.compile(r"^ed25519:[0-9a-f]{64}$")


class PrincipalRegistryUnavailable(RuntimeError):
    pass


def principal_key_id(public_key_hex: str) -> str:
    if not isinstance(public_key_hex, str) or _PUBLIC_KEY_RE.fullmatch(public_key_hex) is None:
        raise ValueError("public_key_hex must be 32-byte lowercase Ed25519 raw hex")
    return "ed25519:" + sha256(bytes.fromhex(public_key_hex)).hexdigest()


def _require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must match {_IDENTIFIER_RE.pattern}")


def _canonical_capabilities(capabilities: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if not isinstance(capabilities, (tuple, list)) or not capabilities:
        raise ValueError("capabilities must be a non-empty list or tuple")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in capabilities):
        raise ValueError("capabilities must contain non-empty strings <= 128 characters")
    canonical = tuple(sorted(set(capabilities)))
    if len(canonical) != len(capabilities):
        raise ValueError("capabilities must not contain duplicates")
    return canonical


@dataclass(frozen=True)
class PrincipalIdentityRecord:
    principal_id: str
    capabilities: tuple[str, ...]
    status: str
    created_by: str
    created_at: str
    revoked_at: int | None = None
    revocation_reason: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.principal_id, "principal_id")
        object.__setattr__(self, "capabilities", _canonical_capabilities(self.capabilities))
        if self.status not in {"ACTIVE", "REVOKED"}:
            raise ValueError("principal status must be ACTIVE or REVOKED")
        if not isinstance(self.created_by, str) or not self.created_by:
            raise ValueError("created_by must be a non-empty string")
        if not isinstance(self.created_at, str) or not self.created_at:
            raise ValueError("created_at must be a non-empty string")
        if self.status == "ACTIVE" and (self.revoked_at is not None or self.revocation_reason is not None):
            raise ValueError("active principal cannot carry revocation metadata")
        if self.status == "REVOKED":
            if not isinstance(self.revoked_at, int) or self.revoked_at < 0:
                raise ValueError("revoked principal requires non-negative revoked_at")
            if not isinstance(self.revocation_reason, str) or not self.revocation_reason:
                raise ValueError("revoked principal requires revocation_reason")


@dataclass(frozen=True)
class PrincipalKeyRecord:
    principal_id: str
    key_id: str
    public_key_hex: str
    valid_from: int
    valid_until: int
    previous_key_id: str | None
    registered_by: str
    registered_at: str
    revoked_at: int | None = None
    revocation_reason: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.principal_id, "principal_id")
        if not isinstance(self.key_id, str) or _KEY_ID_RE.fullmatch(self.key_id) is None:
            raise ValueError("key_id must be ed25519:<sha256-public-key>")
        if self.key_id != principal_key_id(self.public_key_hex):
            raise ValueError("key_id does not match public_key_hex")
        if not isinstance(self.valid_from, int) or not isinstance(self.valid_until, int):
            raise ValueError("key validity bounds must be integer unix timestamps")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be greater than valid_from")
        if self.previous_key_id is not None:
            if _KEY_ID_RE.fullmatch(self.previous_key_id) is None or self.previous_key_id == self.key_id:
                raise ValueError("previous_key_id must be a different canonical Ed25519 key id")
        if not isinstance(self.registered_by, str) or not self.registered_by:
            raise ValueError("registered_by must be a non-empty string")
        if not isinstance(self.registered_at, str) or not self.registered_at:
            raise ValueError("registered_at must be a non-empty string")
        if self.revoked_at is not None:
            if not isinstance(self.revoked_at, int) or self.revoked_at < 0:
                raise ValueError("revoked_at must be a non-negative integer")
            if not isinstance(self.revocation_reason, str) or not self.revocation_reason:
                raise ValueError("revoked key requires revocation_reason")


@dataclass(frozen=True)
class ResolvedPrincipalCredential:
    principal: PrincipalIdentityRecord
    key: PrincipalKeyRecord


class PrincipalIdentityRegistry:
    def _ensure_tables(self, conn: Any) -> None:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS auth_principals (
                principal_id TEXT PRIMARY KEY,
                capabilities_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','REVOKED')),
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at INTEGER,
                revocation_reason TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS auth_principal_keys (
                key_id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                public_key_hex TEXT NOT NULL UNIQUE,
                valid_from INTEGER NOT NULL,
                valid_until INTEGER NOT NULL,
                previous_key_id TEXT,
                registered_by TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                revoked_at INTEGER,
                revocation_reason TEXT,
                FOREIGN KEY(principal_id) REFERENCES auth_principals(principal_id),
                FOREIGN KEY(previous_key_id) REFERENCES auth_principal_keys(key_id)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_principal_keys_principal ON auth_principal_keys(principal_id,valid_from,key_id)")

    @staticmethod
    def _row_to_principal(row: Any) -> PrincipalIdentityRecord:
        return PrincipalIdentityRecord(
            principal_id=row["principal_id"],
            capabilities=tuple(json.loads(row["capabilities_json"])),
            status=row["status"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
            revocation_reason=row["revocation_reason"],
        )

    @staticmethod
    def _row_to_key(row: Any) -> PrincipalKeyRecord:
        return PrincipalKeyRecord(
            principal_id=row["principal_id"],
            key_id=row["key_id"],
            public_key_hex=row["public_key_hex"],
            valid_from=int(row["valid_from"]),
            valid_until=int(row["valid_until"]),
            previous_key_id=row["previous_key_id"],
            registered_by=row["registered_by"],
            registered_at=row["registered_at"],
            revoked_at=None if row["revoked_at"] is None else int(row["revoked_at"]),
            revocation_reason=row["revocation_reason"],
        )

    def principal_count(self) -> int:
        conn = storage._connect()
        try:
            self._ensure_tables(conn)
            return int(conn.execute("SELECT COUNT(*) FROM auth_principals").fetchone()[0])
        finally:
            conn.close()

    def bootstrap_root_from_environment(self) -> dict[str, object] | None:
        raw = os.environ.get(BOOTSTRAP_ROOT_ENV, "").strip()
        conn = storage._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_tables(conn)
            count = int(conn.execute("SELECT COUNT(*) FROM auth_principals").fetchone()[0])
            if count:
                conn.commit()
                return None
            if not raw:
                raise PrincipalRegistryUnavailable(
                    f"asymmetric principal registry is empty and {BOOTSTRAP_ROOT_ENV} is not configured"
                )
            try:
                manifest = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise PrincipalRegistryUnavailable(f"{BOOTSTRAP_ROOT_ENV} must be valid JSON") from exc
            if not isinstance(manifest, dict):
                raise PrincipalRegistryUnavailable(f"{BOOTSTRAP_ROOT_ENV} must be a JSON object")
            expected = {"principal_id", "public_key_hex", "capabilities", "valid_from", "valid_until"}
            if set(manifest) != expected:
                raise PrincipalRegistryUnavailable(
                    f"{BOOTSTRAP_ROOT_ENV} must contain exactly {sorted(expected)}"
                )
            principal_id = manifest["principal_id"]
            public_key_hex = manifest["public_key_hex"]
            capabilities = _canonical_capabilities(manifest["capabilities"])
            valid_from = manifest["valid_from"]
            valid_until = manifest["valid_until"]
            _require_identifier(principal_id, "principal_id")
            key_id = principal_key_id(public_key_hex)
            if not isinstance(valid_from, int) or not isinstance(valid_until, int) or valid_until <= valid_from:
                raise PrincipalRegistryUnavailable("bootstrap validity window is invalid")
            created_at = storage._now()
            conn.execute(
                "INSERT INTO auth_principals(principal_id,capabilities_json,status,created_by,created_at) VALUES (?,?,?,?,?)",
                (principal_id, storage._canonical_json(list(capabilities)), "ACTIVE", "EXTERNAL_BOOTSTRAP_TRUST_ANCHOR", created_at),
            )
            conn.execute(
                """INSERT INTO auth_principal_keys(
                    key_id,principal_id,public_key_hex,valid_from,valid_until,previous_key_id,
                    registered_by,registered_at
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    key_id,
                    principal_id,
                    public_key_hex,
                    valid_from,
                    valid_until,
                    None,
                    "EXTERNAL_BOOTSTRAP_TRUST_ANCHOR",
                    created_at,
                ),
            )
            receipt = storage._append_ledger_tx(
                conn,
                {
                    "event_type": "AUTH_ROOT_PRINCIPAL_BOOTSTRAPPED",
                    "auth_scheme": ED25519_AUTH_SCHEME,
                    "principal_id": principal_id,
                    "key_id": key_id,
                    "public_key_sha256": key_id.split(":", 1)[1],
                    "capabilities": list(capabilities),
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "private_material_present": False,
                    "created_by": "EXTERNAL_BOOTSTRAP_TRUST_ANCHOR",
                    "created_at": created_at,
                },
                "PASS",
            )
            conn.commit()
            return {"principal_id": principal_id, "key_id": key_id, "receipt": receipt}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_principal(self, principal_id: str) -> PrincipalIdentityRecord | None:
        conn = storage._connect()
        try:
            self._ensure_tables(conn)
            row = conn.execute("SELECT * FROM auth_principals WHERE principal_id=?", (principal_id,)).fetchone()
            return None if row is None else self._row_to_principal(row)
        finally:
            conn.close()

    def get_key(self, key_id: str) -> PrincipalKeyRecord | None:
        conn = storage._connect()
        try:
            self._ensure_tables(conn)
            row = conn.execute("SELECT * FROM auth_principal_keys WHERE key_id=?", (key_id,)).fetchone()
            return None if row is None else self._row_to_key(row)
        finally:
            conn.close()

    def resolve_credential(self, principal_id: str, key_id: str) -> ResolvedPrincipalCredential | None:
        principal = self.get_principal(principal_id)
        key = self.get_key(key_id)
        if principal is None or key is None or key.principal_id != principal_id:
            return None
        return ResolvedPrincipalCredential(principal=principal, key=key)

    def list_keys(self, principal_id: str) -> tuple[PrincipalKeyRecord, ...]:
        conn = storage._connect()
        try:
            self._ensure_tables(conn)
            rows = conn.execute(
                "SELECT * FROM auth_principal_keys WHERE principal_id=? ORDER BY valid_from,key_id",
                (principal_id,),
            ).fetchall()
            return tuple(self._row_to_key(row) for row in rows)
        finally:
            conn.close()

    def register_principal(
        self,
        *,
        principal_id: str,
        capabilities: tuple[str, ...] | list[str],
        public_key_hex: str,
        valid_from: int,
        valid_until: int,
        actor_id: str,
    ) -> dict[str, object]:
        _require_identifier(principal_id, "principal_id")
        _require_identifier(actor_id, "actor_id")
        canonical_capabilities = _canonical_capabilities(capabilities)
        key_id = principal_key_id(public_key_hex)
        if not isinstance(valid_from, int) or not isinstance(valid_until, int) or valid_until <= valid_from:
            raise ValueError("principal key validity window is invalid")
        conn = storage._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_tables(conn)
            if conn.execute("SELECT 1 FROM auth_principals WHERE principal_id=?", (principal_id,)).fetchone() is not None:
                raise ValueError("principal_id already exists")
            created_at = storage._now()
            conn.execute(
                "INSERT INTO auth_principals(principal_id,capabilities_json,status,created_by,created_at) VALUES (?,?,?,?,?)",
                (principal_id, storage._canonical_json(list(canonical_capabilities)), "ACTIVE", actor_id, created_at),
            )
            conn.execute(
                """INSERT INTO auth_principal_keys(
                    key_id,principal_id,public_key_hex,valid_from,valid_until,previous_key_id,
                    registered_by,registered_at
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (key_id, principal_id, public_key_hex, valid_from, valid_until, None, actor_id, created_at),
            )
            receipt = storage._append_ledger_tx(
                conn,
                {
                    "event_type": "AUTH_PRINCIPAL_REGISTERED",
                    "auth_scheme": ED25519_AUTH_SCHEME,
                    "principal_id": principal_id,
                    "key_id": key_id,
                    "capabilities": list(canonical_capabilities),
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "created_by": actor_id,
                    "created_at": created_at,
                },
                "PASS",
            )
            conn.commit()
            return {
                "principal": self.get_principal(principal_id),
                "key": self.get_key(key_id),
                "receipt": receipt,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def rotate_key(
        self,
        *,
        principal_id: str,
        previous_key_id: str,
        public_key_hex: str,
        valid_from: int,
        valid_until: int,
        actor_id: str,
    ) -> dict[str, object]:
        _require_identifier(principal_id, "principal_id")
        _require_identifier(actor_id, "actor_id")
        new_key_id = principal_key_id(public_key_hex)
        if not isinstance(valid_from, int) or not isinstance(valid_until, int) or valid_until <= valid_from:
            raise ValueError("rotated key validity window is invalid")
        conn = storage._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_tables(conn)
            principal = conn.execute("SELECT * FROM auth_principals WHERE principal_id=?", (principal_id,)).fetchone()
            if principal is None:
                raise LookupError("principal not found")
            if principal["status"] != "ACTIVE":
                raise PermissionError("principal is revoked")
            previous = conn.execute("SELECT * FROM auth_principal_keys WHERE key_id=?", (previous_key_id,)).fetchone()
            if previous is None or previous["principal_id"] != principal_id:
                raise LookupError("previous principal key not found")
            if previous["revoked_at"] is not None:
                raise PermissionError("cannot rotate from a revoked key")
            if valid_from < int(previous["valid_from"]):
                raise ValueError("rotated key valid_from cannot predate predecessor")
            if conn.execute("SELECT 1 FROM auth_principal_keys WHERE key_id=?", (new_key_id,)).fetchone() is not None:
                raise ValueError("principal key already registered")
            registered_at = storage._now()
            conn.execute(
                """INSERT INTO auth_principal_keys(
                    key_id,principal_id,public_key_hex,valid_from,valid_until,previous_key_id,
                    registered_by,registered_at
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    new_key_id,
                    principal_id,
                    public_key_hex,
                    valid_from,
                    valid_until,
                    previous_key_id,
                    actor_id,
                    registered_at,
                ),
            )
            receipt = storage._append_ledger_tx(
                conn,
                {
                    "event_type": "AUTH_PRINCIPAL_KEY_ROTATED",
                    "auth_scheme": ED25519_AUTH_SCHEME,
                    "principal_id": principal_id,
                    "previous_key_id": previous_key_id,
                    "key_id": new_key_id,
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "registered_by": actor_id,
                    "registered_at": registered_at,
                },
                "PASS",
            )
            conn.commit()
            return {"key": self.get_key(new_key_id), "receipt": receipt}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def revoke_key(
        self,
        *,
        principal_id: str,
        key_id: str,
        effective_at: int,
        reason: str,
        actor_id: str,
    ) -> dict[str, object]:
        _require_identifier(principal_id, "principal_id")
        _require_identifier(actor_id, "actor_id")
        if not isinstance(effective_at, int) or effective_at < 0:
            raise ValueError("effective_at must be a non-negative integer unix timestamp")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise ValueError("reason must be a non-empty string <= 512 characters")
        conn = storage._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_tables(conn)
            row = conn.execute("SELECT * FROM auth_principal_keys WHERE key_id=?", (key_id,)).fetchone()
            if row is None or row["principal_id"] != principal_id:
                raise LookupError("principal key not found")
            if row["revoked_at"] is not None:
                if int(row["revoked_at"]) == effective_at and row["revocation_reason"] == reason:
                    conn.commit()
                    return {"key": self._row_to_key(row), "idempotent": True}
                raise ValueError("principal key already revoked with different metadata")
            replacement = conn.execute(
                """SELECT key_id FROM auth_principal_keys
                   WHERE principal_id=? AND key_id<>? AND valid_from<=? AND valid_until>?
                     AND (revoked_at IS NULL OR revoked_at>?)
                   LIMIT 1""",
                (principal_id, key_id, effective_at, effective_at, effective_at),
            ).fetchone()
            if replacement is None:
                raise PermissionError("cannot revoke the principal's last usable key; rotate first or revoke the principal")
            conn.execute(
                "UPDATE auth_principal_keys SET revoked_at=?,revocation_reason=? WHERE key_id=?",
                (effective_at, reason.strip(), key_id),
            )
            revoked_at_observed = storage._now()
            receipt = storage._append_ledger_tx(
                conn,
                {
                    "event_type": "AUTH_PRINCIPAL_KEY_REVOKED",
                    "principal_id": principal_id,
                    "key_id": key_id,
                    "revoked_at": effective_at,
                    "revocation_reason": reason.strip(),
                    "revoked_by": actor_id,
                    "observed_at": revoked_at_observed,
                },
                "PASS",
            )
            conn.commit()
            return {"key": self.get_key(key_id), "receipt": receipt, "idempotent": False}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def revoke_principal(
        self,
        *,
        principal_id: str,
        effective_at: int,
        reason: str,
        actor_id: str,
    ) -> dict[str, object]:
        _require_identifier(principal_id, "principal_id")
        _require_identifier(actor_id, "actor_id")
        if not isinstance(effective_at, int) or effective_at < 0:
            raise ValueError("effective_at must be a non-negative integer unix timestamp")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise ValueError("reason must be a non-empty string <= 512 characters")
        conn = storage._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_tables(conn)
            row = conn.execute("SELECT * FROM auth_principals WHERE principal_id=?", (principal_id,)).fetchone()
            if row is None:
                raise LookupError("principal not found")
            if row["status"] == "REVOKED":
                if int(row["revoked_at"]) == effective_at and row["revocation_reason"] == reason:
                    conn.commit()
                    return {"principal": self._row_to_principal(row), "idempotent": True}
                raise ValueError("principal already revoked with different metadata")
            conn.execute(
                "UPDATE auth_principals SET status='REVOKED',revoked_at=?,revocation_reason=? WHERE principal_id=?",
                (effective_at, reason.strip(), principal_id),
            )
            receipt = storage._append_ledger_tx(
                conn,
                {
                    "event_type": "AUTH_PRINCIPAL_REVOKED",
                    "principal_id": principal_id,
                    "revoked_at": effective_at,
                    "revocation_reason": reason.strip(),
                    "revoked_by": actor_id,
                    "observed_at": storage._now(),
                },
                "PASS",
            )
            conn.commit()
            return {"principal": self.get_principal(principal_id), "receipt": receipt, "idempotent": False}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def resolve_principal_credential(principal_id: str, key_id: str) -> ResolvedPrincipalCredential | None:
    if remote_state_enabled():
        try:
            payload = fetch_remote_auth_credential(principal_id, key_id)
        except InstitutionalStateRejected as exc:
            if exc.status == 404:
                return None
            raise PrincipalRegistryUnavailable(str(exc)) from exc
        except InstitutionalStateUnavailable as exc:
            raise PrincipalRegistryUnavailable(str(exc)) from exc
        if payload is None:
            return None
        try:
            principal_raw = payload["principal"]
            key_raw = payload["key"]
            return ResolvedPrincipalCredential(
                principal=PrincipalIdentityRecord(
                    principal_id=principal_raw["principal_id"],
                    capabilities=tuple(principal_raw["capabilities"]),
                    status=principal_raw["status"],
                    created_by=principal_raw["created_by"],
                    created_at=principal_raw["created_at"],
                    revoked_at=principal_raw.get("revoked_at"),
                    revocation_reason=principal_raw.get("revocation_reason"),
                ),
                key=PrincipalKeyRecord(
                    principal_id=key_raw["principal_id"],
                    key_id=key_raw["key_id"],
                    public_key_hex=key_raw["public_key_hex"],
                    valid_from=key_raw["valid_from"],
                    valid_until=key_raw["valid_until"],
                    previous_key_id=key_raw.get("previous_key_id"),
                    registered_by=key_raw["registered_by"],
                    registered_at=key_raw["registered_at"],
                    revoked_at=key_raw.get("revoked_at"),
                    revocation_reason=key_raw.get("revocation_reason"),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PrincipalRegistryUnavailable("remote credential response is invalid") from exc

    registry = PrincipalIdentityRegistry()
    registry.bootstrap_root_from_environment()
    return registry.resolve_credential(principal_id, key_id)
