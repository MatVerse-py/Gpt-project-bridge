from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol


class CredentialState(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True)
class CredentialRef:
    """Persistable reference to a secret; never contains the secret value."""

    provider: str
    account_id: str
    secret_ref: str
    expires_at: str | None = None
    revoked: bool = False

    def state(self, *, now: datetime | None = None) -> CredentialState:
        if self.revoked:
            return CredentialState.REVOKED
        if self.expires_at:
            instant = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            current = now or datetime.now(timezone.utc)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            if current >= instant:
                return CredentialState.EXPIRED
        return CredentialState.ACTIVE


class SecretProvider(Protocol):
    def resolve(self, secret_ref: str) -> str: ...


class CredentialUnavailable(PermissionError):
    pass


class CredentialBroker:
    """Resolves short-lived secret material only at the transport boundary."""

    def __init__(self, provider: SecretProvider) -> None:
        self._provider = provider

    def access_token(self, credential: CredentialRef) -> str:
        state = credential.state()
        if state is not CredentialState.ACTIVE:
            raise CredentialUnavailable(f"credential is {state.value}")
        token = self._provider.resolve(credential.secret_ref)
        if not isinstance(token, str) or not token.strip():
            raise CredentialUnavailable("credential secret is unavailable")
        return token.strip()


class EnvironmentSecretProvider:
    """Minimal deployment provider. Production vaults implement SecretProvider."""

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        if environ is None:
            import os

            environ = os.environ
        self._environ = environ

    def resolve(self, secret_ref: str) -> str:
        if not secret_ref.startswith("env:"):
            raise CredentialUnavailable("unsupported secret reference")
        name = secret_ref[4:]
        if not name or not name.replace("_", "").isalnum():
            raise CredentialUnavailable("invalid environment secret reference")
        value = self._environ.get(name)
        if not value:
            raise CredentialUnavailable("secret not configured")
        return value
