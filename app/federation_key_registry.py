from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from . import storage
from .federation_ed25519 import (
    ED25519_PUBLIC_KEY_SCHEME,
    Ed25519RelationIntegrityGate,
)
from .federation_relation import FederationRelation, RelationDecision, RelationRequest

ED25519_ALGORITHM = "Ed25519"
_PUBLIC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID_RE = re.compile(r"^ed25519:[0-9a-f]{64}$")


class AuthorityKeyStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def authority_key_id(public_key_hex: str) -> str:
    if not isinstance(public_key_hex, str) or _PUBLIC_KEY_RE.fullmatch(public_key_hex) is None:
        raise ValueError("public_key_hex must be 32-byte lowercase Ed25519 raw hex")
    digest = hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()
    return f"ed25519:{digest}"


def _hash_payload(payload: dict[str, object]) -> str:
    return hashlib.sha256(storage._canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthorityKeyRecord:
    authority_id: str
    key_id: str
    public_key_hex: str
    valid_from: int
    valid_until: int
    previous_key_id: str | None = None
    algorithm: str = ED25519_ALGORITHM
    revoked_at: int | None = None
    revocation_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.authority_id, "authority_id")
        if not isinstance(self.key_id, str) or _KEY_ID_RE.fullmatch(self.key_id) is None:
            raise ValueError("key_id must be ed25519:<sha256-public-key>")
        if self.key_id != authority_key_id(self.public_key_hex):
            raise ValueError("key_id does not match public_key_hex")
        if self.algorithm != ED25519_ALGORITHM:
            raise ValueError(f"algorithm must be {ED25519_ALGORITHM}")
        if not isinstance(self.valid_from, int) or not isinstance(self.valid_until, int):
            raise ValueError("key validity bounds must be integer unix timestamps")
        if self.valid_until <= self.valid_from:
            raise ValueError("key valid_until must be greater than valid_from")
        if self.previous_key_id is not None:
            if not isinstance(self.previous_key_id, str) or _KEY_ID_RE.fullmatch(self.previous_key_id) is None:
                raise ValueError("previous_key_id must be a canonical Ed25519 key id")
            if self.previous_key_id == self.key_id:
                raise ValueError("key cannot rotate from itself")
        if self.revoked_at is not None:
            if not isinstance(self.revoked_at, int):
                raise ValueError("revoked_at must be an integer unix timestamp")
            if not self.valid_from <= self.revoked_at < self.valid_until:
                raise ValueError("revoked_at must fall inside the key validity window")
            _require_text(self.revocation_reason or "", "revocation_reason")
        elif self.revocation_reason is not None:
            raise ValueError("revocation_reason requires revoked_at")

    @property
    def status(self) -> AuthorityKeyStatus:
        return AuthorityKeyStatus.REVOKED if self.revoked_at is not None else AuthorityKeyStatus.ACTIVE

    def registration_payload(self) -> dict[str, object]:
        return {
            "schema": "matverse.federation-authority-key.v1",
            "authority_id": self.authority_id,
            "key_id": self.key_id,
            "algorithm": self.algorithm,
            "public_key_hex": self.public_key_hex,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "previous_key_id": self.previous_key_id,
        }

    def registration_sha256(self) -> str:
        return _hash_payload(self.registration_payload())

    def effective_at(self, timestamp: int) -> bool:
        if not self.valid_from <= timestamp < self.valid_until:
            return False
        return self.revoked_at is None or timestamp < self.revoked_at


@dataclass(frozen=True)
class FederationRelationKeyBinding:
    relation_id: str
    relation_sha256: str
    source_authority: str
    source_key_id: str
    target_authority: str
    target_key_id: str

    def __post_init__(self) -> None:
        _require_text(self.relation_id, "relation_id")
        _require_sha256(self.relation_sha256, "relation_sha256")
        _require_text(self.source_authority, "source_authority")
        _require_text(self.target_authority, "target_authority")
        if self.source_authority == self.target_authority:
            raise ValueError("binding requires distinct authorities")
        if not isinstance(self.source_key_id, str) or _KEY_ID_RE.fullmatch(self.source_key_id) is None:
            raise ValueError("source_key_id must be a canonical Ed25519 key id")
        if not isinstance(self.target_key_id, str) or _KEY_ID_RE.fullmatch(self.target_key_id) is None:
            raise ValueError("target_key_id must be a canonical Ed25519 key id")
        if self.source_key_id == self.target_key_id:
            raise ValueError("binding requires distinct authority keys")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema": "matverse.federation-relation-key-binding.v1",
            "relation_id": self.relation_id,
            "relation_sha256": self.relation_sha256,
            "source_authority": self.source_authority,
            "source_key_id": self.source_key_id,
            "target_authority": self.target_authority,
            "target_key_id": self.target_key_id,
        }

    def binding_sha256(self) -> str:
        return _hash_payload(self.canonical_payload())


class FederationAuthorityKeyRegistry:
    """Persistent, ledgered Ed25519 authority-key lifecycle and relation binding.

    The registry stores public material only. Key registration, rotation,
    revocation and relation-key binding are committed inside BEGIN IMMEDIATE
    transactions and append their state transition to the existing MatVerse
    hash-chained ledger before commit.
    """

    def _ensure_tables(self, conn: object) -> None:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS federation_authority_keys (
                key_id TEXT PRIMARY KEY,
                authority_id TEXT NOT NULL,
                algorithm TEXT NOT NULL,
                public_key_hex TEXT NOT NULL UNIQUE,
                valid_from INTEGER NOT NULL,
                valid_until INTEGER NOT NULL,
                previous_key_id TEXT,
                registration_sha256 TEXT NOT NULL UNIQUE,
                registered_by TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                registration_receipt_json TEXT NOT NULL,
                revoked_at INTEGER,
                revocation_reason TEXT,
                revoked_by TEXT,
                revocation_receipt_json TEXT,
                FOREIGN KEY(previous_key_id) REFERENCES federation_authority_keys(key_id)
            )"""
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_federation_key_single_successor
               ON federation_authority_keys(previous_key_id)
               WHERE previous_key_id IS NOT NULL"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_federation_keys_authority ON federation_authority_keys(authority_id,valid_from,key_id)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS federation_relation_key_bindings (
                relation_id TEXT PRIMARY KEY,
                relation_sha256 TEXT NOT NULL UNIQUE,
                source_authority TEXT NOT NULL,
                source_key_id TEXT NOT NULL,
                target_authority TEXT NOT NULL,
                target_key_id TEXT NOT NULL,
                binding_sha256 TEXT NOT NULL UNIQUE,
                bound_by TEXT NOT NULL,
                bound_at TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                FOREIGN KEY(source_key_id) REFERENCES federation_authority_keys(key_id),
                FOREIGN KEY(target_key_id) REFERENCES federation_authority_keys(key_id)
            )"""
        )

    @staticmethod
    def _row_to_key(row: object) -> AuthorityKeyRecord:
        return AuthorityKeyRecord(
            authority_id=row["authority_id"],
            key_id=row["key_id"],
            public_key_hex=row["public_key_hex"],
            valid_from=int(row["valid_from"]),
            valid_until=int(row["valid_until"]),
            previous_key_id=row["previous_key_id"],
            algorithm=row["algorithm"],
            revoked_at=None if row["revoked_at"] is None else int(row["revoked_at"]),
            revocation_reason=row["revocation_reason"],
        )

    @staticmethod
    def _row_to_binding(row: object) -> FederationRelationKeyBinding:
        return FederationRelationKeyBinding(
            relation_id=row["relation_id"],
            relation_sha256=row["relation_sha256"],
            source_authority=row["source_authority"],
            source_key_id=row["source_key_id"],
            target_authority=row["target_authority"],
            target_key_id=row["target_key_id"],
        )

    def register_key(self, record: AuthorityKeyRecord, *, actor_id: str) -> dict[str, object]:
        if record.revoked_at is not None:
            raise ValueError("new key registration cannot start revoked")
        _require_text(actor_id, "actor_id")
        conn = storage._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_tables(conn)
            existing = conn.execute(
                "SELECT * FROM federation_authority_keys WHERE key_id=?", (record.key_id,)
            ).fetchone()
            if existing is not None:
                stored = self._row_to_key(existing)
                if stored.registration_payload() != record.registration_payload():
                    raise ValueError("key_id collision or registration mismatch")
                conn.commit()
                return {
                    "record": stored,
                    "registration_sha256": stored.registration_sha256(),
                    "receipt": storage.json.loads(existing["registration_receipt_json"]),
                    "idempotent": True,
                }

            authority_rows = conn.execute(
                "SELECT key_id FROM federation_authority_keys WHERE authority_id=? ORDER BY valid_from,key_id",
                (record.authority_id,),
            ).fetchall()
            if not authority_rows:
                if record.previous_key_id is not None:
                    raise ValueError("genesis authority key cannot declare previous_key_id")
            else:
                if record.previous_key_id is None:
                    raise ValueError("non-genesis authority key requires previous_key_id")
                previous = conn.execute(
                    "SELECT * FROM federation_authority_keys WHERE key_id=?", (record.previous_key_id,)
                ).fetchone()
                if previous is None:
                    raise ValueError("previous_key_id is not registered")
                if previous["authority_id"] != record.authority_id:
                    raise ValueError("previous_key_id belongs to a different authority")
                if int(previous["valid_until"]) != record.valid_from:
                    raise ValueError("rotation must continue exactly at previous valid_until")
                successor = conn.execute(
                    "SELECT key_id FROM federation_authority_keys WHERE previous_key_id=?",
                    (record.previous_key_id,),
                ).fetchone()
                if successor is not None:
                    raise ValueError("previous_key_id already has a rotation successor")

            duplicate_public = conn.execute(
                "SELECT authority_id,key_id FROM federation_authority_keys WHERE public_key_hex=?",
                (record.public_key_hex,),
            ).fetchone()
            if duplicate_public is not None:
                raise ValueError("public key is already registered")

            registered_at = storage._now()
            registration_sha256 = record.registration_sha256()
            event = {
                "event_type": "FEDERATION_AUTHORITY_KEY_REGISTERED",
                **record.registration_payload(),
                "registration_sha256": registration_sha256,
                "registered_by": actor_id,
                "registered_at": registered_at,
            }
            receipt = storage._append_ledger_tx(conn, event, "PASS")
            conn.execute(
                """INSERT INTO federation_authority_keys(
                    key_id,authority_id,algorithm,public_key_hex,valid_from,valid_until,
                    previous_key_id,registration_sha256,registered_by,registered_at,
                    registration_receipt_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.key_id,
                    record.authority_id,
                    record.algorithm,
                    record.public_key_hex,
                    record.valid_from,
                    record.valid_until,
                    record.previous_key_id,
                    registration_sha256,
                    actor_id,
                    registered_at,
                    storage._canonical_json(receipt),
                ),
            )
            conn.commit()
            return {
                "record": record,
                "registration_sha256": registration_sha256,
                "receipt": receipt,
                "idempotent": False,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def revoke_key(
        self,
        authority_id: str,
        key_id: str,
        *,
        effective_at: int,
        reason: str,
        actor_id: str,
    ) -> dict[str, object]:
        _require_text(authority_id, "authority_id")
        _require_text(reason, "reason")
        _require_text(actor_id, "actor_id")
        if not isinstance(effective_at, int):
            raise ValueError("effective_at must be an integer unix timestamp")
        conn = storage._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_tables(conn)
            row = conn.execute(
                "SELECT * FROM federation_authority_keys WHERE key_id=?", (key_id,)
            ).fetchone()
            if row is None:
                raise LookupError("authority key not found")
            if row["authority_id"] != authority_id:
                raise PermissionError("key does not belong to authority")
            if not int(row["valid_from"]) <= effective_at < int(row["valid_until"]):
                raise ValueError("revocation effective_at must fall inside key validity window")
            if row["revoked_at"] is not None:
                if int(row["revoked_at"]) == effective_at and row["revocation_reason"] == reason:
                    conn.commit()
                    return {
                        "record": self._row_to_key(row),
                        "receipt": storage.json.loads(row["revocation_receipt_json"]),
                        "idempotent": True,
                    }
                raise ValueError("key is already revoked with different revocation state")

            event = {
                "event_type": "FEDERATION_AUTHORITY_KEY_REVOKED",
                "authority_id": authority_id,
                "key_id": key_id,
                "registration_sha256": row["registration_sha256"],
                "effective_at": effective_at,
                "reason": reason,
                "revoked_by": actor_id,
                "revoked_at_recorded": storage._now(),
            }
            receipt = storage._append_ledger_tx(conn, event, "PASS")
            conn.execute(
                """UPDATE federation_authority_keys
                   SET revoked_at=?,revocation_reason=?,revoked_by=?,revocation_receipt_json=?
                   WHERE key_id=?""",
                (
                    effective_at,
                    reason,
                    actor_id,
                    storage._canonical_json(receipt),
                    key_id,
                ),
            )
            conn.commit()
            updated = self.get_key(key_id)
            assert updated is not None
            return {"record": updated, "receipt": receipt, "idempotent": False}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_key(self, key_id: str) -> AuthorityKeyRecord | None:
        conn = storage._connect()
        try:
            self._ensure_tables(conn)
            row = conn.execute(
                "SELECT * FROM federation_authority_keys WHERE key_id=?", (key_id,)
            ).fetchone()
            return None if row is None else self._row_to_key(row)
        finally:
            conn.close()

    def list_authority_keys(self, authority_id: str) -> tuple[AuthorityKeyRecord, ...]:
        _require_text(authority_id, "authority_id")
        conn = storage._connect()
        try:
            self._ensure_tables(conn)
            rows = conn.execute(
                """SELECT * FROM federation_authority_keys
                   WHERE authority_id=? ORDER BY valid_from,key_id""",
                (authority_id,),
            ).fetchall()
            return tuple(self._row_to_key(row) for row in rows)
        finally:
            conn.close()

    def register_relation_binding(
        self,
        relation: FederationRelation,
        *,
        source_key_id: str,
        target_key_id: str,
        actor_id: str,
    ) -> dict[str, object]:
        _require_text(actor_id, "actor_id")
        if relation.witness_scheme != ED25519_PUBLIC_KEY_SCHEME:
            raise ValueError("relation key binding requires ED25519-PUBLIC-KEY-V1")
        relation_sha256 = relation.payload_sha256()
        binding = FederationRelationKeyBinding(
            relation_id=relation.relation_id,
            relation_sha256=relation_sha256,
            source_authority=relation.source_authority,
            source_key_id=source_key_id,
            target_authority=relation.target_authority,
            target_key_id=target_key_id,
        )

        conn = storage._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_tables(conn)
            existing = conn.execute(
                "SELECT * FROM federation_relation_key_bindings WHERE relation_id=?",
                (relation.relation_id,),
            ).fetchone()
            if existing is not None:
                stored = self._row_to_binding(existing)
                if stored != binding:
                    raise ValueError("relation_id is already bound to different key material")
                conn.commit()
                return {
                    "binding": stored,
                    "binding_sha256": stored.binding_sha256(),
                    "receipt": storage.json.loads(existing["receipt_json"]),
                    "idempotent": True,
                }

            source_row = conn.execute(
                "SELECT * FROM federation_authority_keys WHERE key_id=?", (source_key_id,)
            ).fetchone()
            target_row = conn.execute(
                "SELECT * FROM federation_authority_keys WHERE key_id=?", (target_key_id,)
            ).fetchone()
            if source_row is None:
                raise LookupError("source authority key not found")
            if target_row is None:
                raise LookupError("target authority key not found")
            source_key = self._row_to_key(source_row)
            target_key = self._row_to_key(target_row)
            if source_key.authority_id != relation.source_authority:
                raise PermissionError("source key authority does not match relation")
            if target_key.authority_id != relation.target_authority:
                raise PermissionError("target key authority does not match relation")
            if source_key.public_key_hex == target_key.public_key_hex:
                raise PermissionError("source and target authorities cannot share one public key")
            for role, key in (("source", source_key), ("target", target_key)):
                if relation.valid_from < key.valid_from or relation.valid_until > key.valid_until:
                    raise PermissionError(f"relation validity exceeds {role} key validity")
                if key.revoked_at is not None and relation.valid_until > key.revoked_at:
                    raise PermissionError(f"relation validity exceeds {role} key revocation")

            verification_gate = Ed25519RelationIntegrityGate(
                {
                    relation.source_authority: source_key.public_key_hex,
                    relation.target_authority: target_key.public_key_hex,
                },
                now=lambda: relation.valid_from,
            )
            verification = verification_gate.evaluate(
                relation,
                RelationRequest(
                    source_domain=relation.source_domain,
                    target_domain=relation.target_domain,
                    contract_hash=relation.contract_hash,
                    capability=relation.capabilities[0],
                ),
            )
            if not verification.admissible:
                raise PermissionError(
                    "relation witness does not verify under bound keys: "
                    + ",".join(verification.reasons)
                )

            binding_sha256 = binding.binding_sha256()
            bound_at = storage._now()
            event = {
                "event_type": "FEDERATION_RELATION_KEY_BOUND",
                **binding.canonical_payload(),
                "binding_sha256": binding_sha256,
                "bound_by": actor_id,
                "bound_at": bound_at,
            }
            receipt = storage._append_ledger_tx(conn, event, "PASS")
            conn.execute(
                """INSERT INTO federation_relation_key_bindings(
                    relation_id,relation_sha256,source_authority,source_key_id,
                    target_authority,target_key_id,binding_sha256,bound_by,bound_at,receipt_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    binding.relation_id,
                    binding.relation_sha256,
                    binding.source_authority,
                    binding.source_key_id,
                    binding.target_authority,
                    binding.target_key_id,
                    binding_sha256,
                    actor_id,
                    bound_at,
                    storage._canonical_json(receipt),
                ),
            )
            conn.commit()
            return {
                "binding": binding,
                "binding_sha256": binding_sha256,
                "receipt": receipt,
                "idempotent": False,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_relation_binding(self, relation_id: str) -> FederationRelationKeyBinding | None:
        conn = storage._connect()
        try:
            self._ensure_tables(conn)
            row = conn.execute(
                "SELECT * FROM federation_relation_key_bindings WHERE relation_id=?",
                (relation_id,),
            ).fetchone()
            return None if row is None else self._row_to_binding(row)
        finally:
            conn.close()

    @staticmethod
    def key_state_reasons(record: AuthorityKeyRecord, timestamp: int, role: str) -> tuple[str, ...]:
        reasons: list[str] = []
        if timestamp < record.valid_from:
            reasons.append(f"{role}_key_not_yet_valid")
        if timestamp >= record.valid_until:
            reasons.append(f"{role}_key_expired")
        if record.revoked_at is not None and timestamp >= record.revoked_at:
            reasons.append(f"{role}_key_revoked")
        return tuple(reasons)


class GovernedEd25519RelationIntegrityGate:
    """Resolve relation signatures through immutable key bindings and lifecycle state."""

    def __init__(
        self,
        registry: FederationAuthorityKeyRegistry,
        now: Callable[[], int] | None = None,
    ) -> None:
        self._registry = registry
        self._now = now or (lambda: int(time.time()))

    def evaluate(
        self,
        relation: FederationRelation,
        request: RelationRequest,
    ) -> RelationDecision:
        timestamp = int(self._now())
        binding_reasons: list[str] = []
        binding = self._registry.get_relation_binding(relation.relation_id)
        public_keys: dict[str, str] = {}

        if binding is None:
            binding_reasons.append("missing_key_binding")
        else:
            if binding.relation_sha256 != relation.payload_sha256():
                binding_reasons.append("binding_relation_hash_mismatch")
            if binding.source_authority != relation.source_authority:
                binding_reasons.append("binding_source_authority_mismatch")
            if binding.target_authority != relation.target_authority:
                binding_reasons.append("binding_target_authority_mismatch")

            source_key = self._registry.get_key(binding.source_key_id)
            target_key = self._registry.get_key(binding.target_key_id)
            if source_key is None:
                binding_reasons.append("bound_source_key_missing")
            else:
                if source_key.authority_id != binding.source_authority:
                    binding_reasons.append("bound_source_key_authority_mismatch")
                binding_reasons.extend(
                    self._registry.key_state_reasons(source_key, timestamp, "source")
                )
                public_keys[relation.source_authority] = source_key.public_key_hex
            if target_key is None:
                binding_reasons.append("bound_target_key_missing")
            else:
                if target_key.authority_id != binding.target_authority:
                    binding_reasons.append("bound_target_key_authority_mismatch")
                binding_reasons.extend(
                    self._registry.key_state_reasons(target_key, timestamp, "target")
                )
                public_keys[relation.target_authority] = target_key.public_key_hex
            if source_key is not None and target_key is not None:
                if source_key.public_key_hex == target_key.public_key_hex:
                    binding_reasons.append("bound_shared_authority_public_key")

        base = Ed25519RelationIntegrityGate(public_keys, now=lambda: timestamp).evaluate(
            relation, request
        )
        reasons = tuple(dict.fromkeys([*binding_reasons, *base.reasons]))
        return RelationDecision(
            relation_id=relation.relation_id,
            admissible=not reasons,
            reasons=reasons,
            relation_sha256=base.relation_sha256,
            evaluated_at=timestamp,
        )
