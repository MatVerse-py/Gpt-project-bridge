from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TypeVar

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .core import stable_hash
from .evidence import canonical_json

SCHEMA_VERSION = "matverse.secret-plane.v1"
LEASE_SCHEMA_VERSION = "matverse.secret-lease.v1"


class SecretPlaneError(RuntimeError):
    pass


class SecretAccessDenied(SecretPlaneError):
    pass


class SecretNotAvailable(SecretPlaneError):
    pass


class SecretExposureError(SecretPlaneError):
    pass


class SecretState(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    DESTROYED = "DESTROYED"


class StorageClass(str, Enum):
    ENVIRONMENT = "ENVIRONMENT"
    PLATFORM_SECRET = "PLATFORM_SECRET"
    OS_KEYCHAIN = "OS_KEYCHAIN"
    HSM = "HSM"
    TPM = "TPM"
    EXTERNAL_VAULT = "EXTERNAL_VAULT"
    TEST_MEMORY = "TEST_MEMORY"


@dataclass(frozen=True)
class SecretDescriptor:
    secret_id: str
    kind: str
    owner: str
    purpose: str
    provider: str | None
    storage_class: StorageClass
    version: int
    created_at: int
    expires_at: int | None = None
    rotation_due_at: int | None = None
    state: SecretState = SecretState.ACTIVE

    def __post_init__(self) -> None:
        if not self.secret_id or not self.kind or not self.owner or not self.purpose:
            raise ValueError("secret_id, kind, owner and purpose are required")
        if self.version < 1:
            raise ValueError("secret version must be >= 1")
        if self.created_at < 0:
            raise ValueError("created_at must be >= 0")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be greater than created_at")
        if self.rotation_due_at is not None and self.rotation_due_at <= self.created_at:
            raise ValueError("rotation_due_at must be greater than created_at")

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["storage_class"] = self.storage_class.value
        payload["state"] = self.state.value
        return payload


@dataclass(frozen=True)
class SecretPolicy:
    allowed_actors: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    allowed_scopes: tuple[str, ...]
    max_ttl_seconds: int = 600
    max_uses: int = 1

    def __post_init__(self) -> None:
        if not self.allowed_actors or not self.allowed_capabilities or not self.allowed_scopes:
            raise ValueError("secret policy allowlists must be non-empty")
        if self.max_ttl_seconds < 1:
            raise ValueError("max_ttl_seconds must be >= 1")
        if self.max_uses < 1:
            raise ValueError("max_uses must be >= 1")

    def public_dict(self) -> dict[str, Any]:
        return {
            "allowed_actors": list(self.allowed_actors),
            "allowed_capabilities": list(self.allowed_capabilities),
            "allowed_scopes": list(self.allowed_scopes),
            "max_ttl_seconds": self.max_ttl_seconds,
            "max_uses": self.max_uses,
        }


@dataclass(frozen=True)
class CapabilityLease:
    lease_id: str
    secret_id: str
    secret_version: int
    actor: str
    capability: str
    scope: str
    issued_at: int
    expires_at: int
    max_uses: int
    nonce: str
    signature: str

    def signed_payload(self) -> dict[str, Any]:
        return {
            "schema": LEASE_SCHEMA_VERSION,
            "lease_id": self.lease_id,
            "secret_id": self.secret_id,
            "secret_version": self.secret_version,
            "actor": self.actor,
            "capability": self.capability,
            "scope": self.scope,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "max_uses": self.max_uses,
            "nonce": self.nonce,
        }

    def audit_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "secret_id": self.secret_id,
            "secret_version": self.secret_version,
            "actor": self.actor,
            "capability": self.capability,
            "scope": self.scope,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "max_uses": self.max_uses,
        }


@dataclass(frozen=True)
class ExposureFinding:
    rule: str
    start: int
    end: int
    length: int


class SecretVaultAdapter(Protocol):
    storage_class: StorageClass

    def read(self, locator: str) -> bytearray:
        ...

    def destroy(self, locator: str) -> None:
        ...


class EnvironmentSecretVault:
    """Read-through process-environment adapter. It is not a persistent vault."""

    storage_class = StorageClass.ENVIRONMENT

    def read(self, locator: str) -> bytearray:
        value = os.environ.get(locator)
        if value is None or value == "":
            raise SecretNotAvailable("environment-backed secret is not available")
        return bytearray(value.encode("utf-8"))

    def destroy(self, locator: str) -> None:
        raise SecretPlaneError("environment-backed material cannot be cryptographically destroyed by this process")


class InMemorySecretVault:
    """Test-only vault. Never use this as a production secret store."""

    storage_class = StorageClass.TEST_MEMORY

    def __init__(self, values: Mapping[str, bytes | bytearray | str] | None = None) -> None:
        self._values: dict[str, bytearray] = {}
        for key, value in (values or {}).items():
            self.put(key, value)

    def put(self, locator: str, value: bytes | bytearray | str) -> None:
        if not locator:
            raise ValueError("locator is required")
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if not raw:
            raise ValueError("secret material must be non-empty")
        previous = self._values.get(locator)
        if previous is not None:
            for idx in range(len(previous)):
                previous[idx] = 0
        self._values[locator] = bytearray(raw)

    def read(self, locator: str) -> bytearray:
        value = self._values.get(locator)
        if value is None:
            raise SecretNotAvailable("secret locator is not available")
        return bytearray(value)

    def destroy(self, locator: str) -> None:
        value = self._values.pop(locator, None)
        if value is None:
            raise SecretNotAvailable("secret locator is not available")
        for idx in range(len(value)):
            value[idx] = 0


class LeaseStateStore(Protocol):
    def register(self, lease: CapabilityLease) -> None:
        ...

    def consume(self, lease: CapabilityLease) -> bool:
        ...

    def revoke_secret(self, secret_id: str) -> None:
        ...


class InMemoryLeaseStateStore:
    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def register(self, lease: CapabilityLease) -> None:
        with self._lock:
            if lease.lease_id in self._state:
                raise ValueError("duplicate lease_id")
            self._state[lease.lease_id] = {"secret_id": lease.secret_id, "uses": 0, "revoked": False}

    def consume(self, lease: CapabilityLease) -> bool:
        with self._lock:
            state = self._state.get(lease.lease_id)
            if state is None or state["revoked"] or state["uses"] >= lease.max_uses:
                return False
            state["uses"] += 1
            return True

    def revoke_secret(self, secret_id: str) -> None:
        with self._lock:
            for state in self._state.values():
                if state["secret_id"] == secret_id:
                    state["revoked"] = True


class SQLiteLeaseStateStore:
    """Durable use/revocation accounting. Stores no secret values or locators."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS secret_lease_state(
              lease_id TEXT PRIMARY KEY,
              secret_id TEXT NOT NULL,
              uses INTEGER NOT NULL,
              revoked INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()
        self._lock = threading.Lock()

    def register(self, lease: CapabilityLease) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO secret_lease_state(lease_id, secret_id, uses, revoked) VALUES(?, ?, 0, 0)",
                (lease.lease_id, lease.secret_id),
            )
            self._conn.commit()

    def consume(self, lease: CapabilityLease) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT uses, revoked FROM secret_lease_state WHERE lease_id = ?",
                (lease.lease_id,),
            ).fetchone()
            if row is None or int(row[1]) != 0 or int(row[0]) >= lease.max_uses:
                self._conn.rollback()
                return False
            self._conn.execute(
                "UPDATE secret_lease_state SET uses = uses + 1 WHERE lease_id = ?",
                (lease.lease_id,),
            )
            self._conn.commit()
            return True

    def revoke_secret(self, secret_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE secret_lease_state SET revoked = 1 WHERE secret_id = ?", (secret_id,))
            self._conn.commit()


class KeyAuthority:
    """CSPRNG + domain-separated HKDF. Persistence is intentionally out of scope."""

    @staticmethod
    def generate(size: int = 32) -> bytes:
        if size < 32:
            raise ValueError("secret keys must be at least 256 bits")
        return secrets.token_bytes(size)

    @staticmethod
    def derive(root_key: bytes | bytearray | memoryview, *, context: str, length: int = 32, salt: bytes | None = None) -> bytes:
        if not context:
            raise ValueError("HKDF context is required")
        if length < 16:
            raise ValueError("derived key length must be >= 16 bytes")
        material = bytes(root_key)
        if len(material) < 32:
            raise ValueError("root key must be at least 256 bits")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=length,
            salt=salt,
            info=(SCHEMA_VERSION + "/" + context).encode("utf-8"),
        ).derive(material)


class DisclosureGate:
    @staticmethod
    def _match(value: str, allowlist: tuple[str, ...]) -> bool:
        return "*" in allowlist or value in allowlist

    def evaluate(self, policy: SecretPolicy, *, actor: str, capability: str, scope: str) -> tuple[bool, str]:
        if not self._match(actor, policy.allowed_actors):
            return False, "actor_not_allowed"
        if not self._match(capability, policy.allowed_capabilities):
            return False, "capability_not_allowed"
        if not self._match(scope, policy.allowed_scopes):
            return False, "scope_not_allowed"
        return True, "allowed"


class SecretExposureDetector:
    """Heuristic DLP detector. Findings never return matched secret text."""

    _RULES = (
        ("private_key_pem", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
        ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)),
        ("openai_like_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
        (
            "credential_assignment",
            re.compile(
                r"(?i)\b(?:api[_-]?key|access[_-]?token|secret(?:[_-]?key)?|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{12,}"
            ),
        ),
    )

    @classmethod
    def scan(cls, text: str) -> tuple[ExposureFinding, ...]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        findings: list[ExposureFinding] = []
        for rule, pattern in cls._RULES:
            for match in pattern.finditer(text):
                findings.append(ExposureFinding(rule, match.start(), match.end(), match.end() - match.start()))
        findings.sort(key=lambda item: (item.start, item.end, item.rule))
        return tuple(findings)


T = TypeVar("T")


def _contains_secret(value: Any, secret: bytes) -> bool:
    if not secret:
        return False
    if isinstance(value, str):
        try:
            return secret.decode("utf-8") in value
        except UnicodeDecodeError:
            return False
    if isinstance(value, (bytes, bytearray, memoryview)):
        return secret in bytes(value)
    if isinstance(value, Mapping):
        return any(_contains_secret(key, secret) or _contains_secret(item, secret) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_secret(item, secret) for item in value)
    return False


class SecretPlane:
    """Governed secret-use plane.

    Secret values live behind a vault adapter. Descriptors, leases and audit events
    contain no secret value, encrypted value or secret hash.
    """

    def __init__(
        self,
        *,
        vault: SecretVaultAdapter,
        lease_signing_key: bytes | bytearray | memoryview,
        lease_state: LeaseStateStore | None = None,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
        disclosure_gate: DisclosureGate | None = None,
    ) -> None:
        key = bytes(lease_signing_key)
        if len(key) < 32:
            raise ValueError("lease signing key must be at least 256 bits")
        self._vault = vault
        self._lease_signing_key = key
        self._lease_state = lease_state or InMemoryLeaseStateStore()
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._gate = disclosure_gate or DisclosureGate()
        self._descriptors: dict[str, SecretDescriptor] = {}
        self._policies: dict[str, SecretPolicy] = {}
        self._bindings: dict[str, str] = {}
        self._audit_events: list[dict[str, Any]] = []

    def _now(self) -> int:
        return int(self._clock())

    def _audit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        core = {
            "schema": SCHEMA_VERSION,
            "event_type": event_type,
            "timestamp": self._now(),
            "payload": dict(payload),
        }
        self._audit_events.append({**core, "event_hash": stable_hash(core)})

    def audit_events(self) -> tuple[dict[str, Any], ...]:
        return tuple({**item, "payload": dict(item["payload"])} for item in self._audit_events)

    def register_secret(self, descriptor: SecretDescriptor, *, policy: SecretPolicy, locator: str) -> None:
        if descriptor.secret_id in self._descriptors:
            raise ValueError("secret_id already registered")
        if descriptor.storage_class is not self._vault.storage_class:
            raise ValueError("descriptor storage_class does not match vault adapter")
        if not locator:
            raise ValueError("opaque vault locator is required")
        self._descriptors[descriptor.secret_id] = descriptor
        self._policies[descriptor.secret_id] = policy
        self._bindings[descriptor.secret_id] = locator
        self._audit(
            "SECRET_REGISTERED",
            {"descriptor": descriptor.public_dict(), "policy": policy.public_dict()},
        )

    def descriptor(self, secret_id: str) -> SecretDescriptor:
        try:
            return self._descriptors[secret_id]
        except KeyError as exc:
            raise SecretNotAvailable("unknown secret_id") from exc

    def descriptors(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._descriptors[key].public_dict() for key in sorted(self._descriptors))

    def _sign_lease_payload(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self._lease_signing_key,
            canonical_json(dict(payload)).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def issue_lease(
        self,
        *,
        secret_id: str,
        actor: str,
        capability: str,
        scope: str,
        ttl_seconds: int | None = None,
        max_uses: int | None = None,
    ) -> CapabilityLease:
        descriptor = self.descriptor(secret_id)
        policy = self._policies[secret_id]
        now = self._now()
        if descriptor.state is not SecretState.ACTIVE:
            raise SecretAccessDenied("secret is not active")
        if descriptor.expires_at is not None and now >= descriptor.expires_at:
            raise SecretAccessDenied("secret descriptor is expired")
        allowed, reason = self._gate.evaluate(policy, actor=actor, capability=capability, scope=scope)
        if not allowed:
            self._audit("LEASE_DENIED", {"secret_id": secret_id, "actor": actor, "capability": capability, "scope": scope, "reason": reason})
            raise SecretAccessDenied(reason)
        ttl = policy.max_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        uses = policy.max_uses if max_uses is None else int(max_uses)
        if ttl < 1 or ttl > policy.max_ttl_seconds:
            raise SecretAccessDenied("requested ttl exceeds policy")
        if uses < 1 or uses > policy.max_uses:
            raise SecretAccessDenied("requested max_uses exceeds policy")
        nonce = self._nonce_factory()
        if not isinstance(nonce, str) or len(nonce) < 16:
            raise ValueError("nonce_factory must return at least 16 characters")
        base = {
            "schema": LEASE_SCHEMA_VERSION,
            "secret_id": secret_id,
            "secret_version": descriptor.version,
            "actor": actor,
            "capability": capability,
            "scope": scope,
            "issued_at": now,
            "expires_at": now + ttl,
            "max_uses": uses,
            "nonce": nonce,
        }
        lease_id = stable_hash(base)
        unsigned = {**base, "lease_id": lease_id}
        signature = self._sign_lease_payload(unsigned)
        lease = CapabilityLease(
            lease_id=lease_id,
            secret_id=secret_id,
            secret_version=descriptor.version,
            actor=actor,
            capability=capability,
            scope=scope,
            issued_at=now,
            expires_at=now + ttl,
            max_uses=uses,
            nonce=nonce,
            signature=signature,
        )
        self._lease_state.register(lease)
        self._audit("LEASE_ISSUED", lease.audit_dict())
        return lease

    def verify_lease(self, lease: CapabilityLease) -> SecretDescriptor:
        expected = self._sign_lease_payload(lease.signed_payload())
        if not hmac.compare_digest(expected, lease.signature):
            raise SecretAccessDenied("invalid lease signature")
        descriptor = self.descriptor(lease.secret_id)
        if descriptor.state is not SecretState.ACTIVE:
            raise SecretAccessDenied("secret is not active")
        if descriptor.version != lease.secret_version:
            raise SecretAccessDenied("lease is bound to a superseded secret version")
        now = self._now()
        if now >= lease.expires_at:
            raise SecretAccessDenied("lease is expired")
        if descriptor.expires_at is not None and now >= descriptor.expires_at:
            raise SecretAccessDenied("secret descriptor is expired")
        policy = self._policies[lease.secret_id]
        allowed, reason = self._gate.evaluate(
            policy,
            actor=lease.actor,
            capability=lease.capability,
            scope=lease.scope,
        )
        if not allowed:
            raise SecretAccessDenied(reason)
        return descriptor

    def execute_with_secret(self, lease: CapabilityLease, consumer: Callable[[memoryview], T]) -> T:
        self.verify_lease(lease)
        if not self._lease_state.consume(lease):
            self._audit("LEASE_USE_DENIED", {**lease.audit_dict(), "reason": "usage_exhausted_or_revoked"})
            raise SecretAccessDenied("lease usage exhausted or revoked")
        locator = self._bindings.get(lease.secret_id)
        if locator is None:
            raise SecretNotAvailable("secret binding is unavailable")
        material = self._vault.read(locator)
        if not material:
            raise SecretNotAvailable("vault returned empty secret material")
        raw = bytes(material)
        try:
            result = consumer(memoryview(material))
            if _contains_secret(result, raw):
                self._audit("SECRET_RETURN_BLOCKED", {**lease.audit_dict(), "reason": "consumer_returned_secret_material"})
                raise SecretExposureError("consumer attempted to return secret material")
            self._audit("LEASE_USED", {**lease.audit_dict(), "decision": "PASS"})
            return result
        except SecretPlaneError:
            raise
        except Exception as exc:
            self._audit("LEASE_USE_ERROR", {**lease.audit_dict(), "error_type": type(exc).__name__})
            raise
        finally:
            for idx in range(len(material)):
                material[idx] = 0

    def rebind_secret(self, secret_id: str, *, new_locator: str, new_version: int) -> SecretDescriptor:
        descriptor = self.descriptor(secret_id)
        if descriptor.state is not SecretState.ACTIVE:
            raise SecretAccessDenied("only active secrets can be rebound")
        if new_version <= descriptor.version:
            raise ValueError("new_version must be greater than current version")
        if not new_locator:
            raise ValueError("new_locator is required")
        updated = replace(descriptor, version=new_version)
        self._descriptors[secret_id] = updated
        self._bindings[secret_id] = new_locator
        self._lease_state.revoke_secret(secret_id)
        self._audit("SECRET_REBOUND", {"secret_id": secret_id, "old_version": descriptor.version, "new_version": new_version})
        return updated

    def revoke_secret(self, secret_id: str) -> SecretDescriptor:
        descriptor = self.descriptor(secret_id)
        if descriptor.state is SecretState.DESTROYED:
            raise SecretAccessDenied("destroyed secret cannot be revoked")
        updated = replace(descriptor, state=SecretState.REVOKED)
        self._descriptors[secret_id] = updated
        self._bindings.pop(secret_id, None)
        self._lease_state.revoke_secret(secret_id)
        self._audit("SECRET_REVOKED", {"secret_id": secret_id, "version": descriptor.version})
        return updated

    def destroy_secret(self, secret_id: str) -> SecretDescriptor:
        descriptor = self.descriptor(secret_id)
        if descriptor.state is SecretState.DESTROYED:
            return descriptor
        locator = self._bindings.get(secret_id)
        if locator is None:
            raise SecretNotAvailable("secret binding is unavailable")
        self._vault.destroy(locator)
        updated = replace(descriptor, state=SecretState.DESTROYED)
        self._descriptors[secret_id] = updated
        self._bindings.pop(secret_id, None)
        self._lease_state.revoke_secret(secret_id)
        self._audit("SECRET_DESTROYED", {"secret_id": secret_id, "version": descriptor.version})
        return updated
