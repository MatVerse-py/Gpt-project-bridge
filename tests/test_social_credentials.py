from datetime import datetime, timezone

import pytest

from app.social_credentials import (
    CredentialBroker,
    CredentialRef,
    CredentialState,
    CredentialUnavailable,
    EnvironmentSecretProvider,
)


def test_active_credential_resolves_without_persisting_secret():
    provider = EnvironmentSecretProvider({"IG_TOKEN": "top-secret"})
    ref = CredentialRef(provider="meta", account_id="acct-1", secret_ref="env:IG_TOKEN")
    assert ref.state() is CredentialState.ACTIVE
    assert CredentialBroker(provider).access_token(ref) == "top-secret"
    assert "top-secret" not in repr(ref)


def test_revoked_credential_fails_closed_before_resolution():
    class BombProvider:
        def resolve(self, secret_ref: str) -> str:
            raise AssertionError("must not resolve revoked credential")

    ref = CredentialRef(provider="meta", account_id="acct-1", secret_ref="vault:1", revoked=True)
    with pytest.raises(CredentialUnavailable, match="revoked"):
        CredentialBroker(BombProvider()).access_token(ref)


def test_expired_credential_state():
    ref = CredentialRef(
        provider="meta",
        account_id="acct-1",
        secret_ref="env:IG_TOKEN",
        expires_at="2026-01-01T00:00:00+00:00",
    )
    assert ref.state(now=datetime(2026, 9, 4, tzinfo=timezone.utc)) is CredentialState.EXPIRED


def test_environment_provider_rejects_non_env_reference():
    with pytest.raises(CredentialUnavailable, match="unsupported"):
        EnvironmentSecretProvider({}).resolve("vault:instagram")


def test_missing_environment_secret_fails_closed():
    with pytest.raises(CredentialUnavailable, match="not configured"):
        EnvironmentSecretProvider({}).resolve("env:IG_TOKEN")
