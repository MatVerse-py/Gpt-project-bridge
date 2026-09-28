from __future__ import annotations

import time

import pytest

from experiments.executor_transplant.blind_secret_proxy import (
    BlindProxyConfig,
    CapabilityLease,
    ProxyConfigurationError,
)


def test_capability_lease_is_scoped_by_ttl_and_usage() -> None:
    token = "x" * 48
    lease = CapabilityLease(token=token, ttl_seconds=60, max_usage=2)

    assert lease.authorize_and_consume("wrong")[0] is False
    assert lease.usage == 0

    assert lease.authorize_and_consume(token) == (True, "authorized")
    assert lease.authorize_and_consume(token) == (True, "authorized")
    assert lease.authorize_and_consume(token) == (False, "capability_exhausted")
    assert lease.usage == 2


def test_capability_lease_expires_fail_closed() -> None:
    token = "y" * 48
    lease = CapabilityLease(token=token, ttl_seconds=1, max_usage=3)
    lease.issued_monotonic = time.monotonic() - 2
    assert lease.authorize_and_consume(token) == (False, "capability_expired")
    assert lease.usage == 0


def test_proxy_config_requires_provider_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("MATVERSE_PROXY_TOKEN", "z" * 48)
    with pytest.raises(ProxyConfigurationError, match="provider secret is unavailable"):
        BlindProxyConfig.from_env()


def test_proxy_config_repr_does_not_expose_secret_or_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_secret = "sk-provider-secret-material"
    capability = "capability-" + ("k" * 48)
    monkeypatch.setenv("OPENAI_API_KEY", provider_secret)
    monkeypatch.setenv("MATVERSE_PROXY_TOKEN", capability)
    monkeypatch.setenv(
        "MATVERSE_SECRET_REF",
        "secret_ref://openai/matverse/executor-transplant",
    )

    config = BlindProxyConfig.from_env()
    rendered = repr(config)
    assert provider_secret not in rendered
    assert capability not in rendered
    assert "secret_ref://openai/matverse/executor-transplant" in rendered


def test_proxy_rejects_plaintext_remote_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MATVERSE_PROXY_TOKEN", "q" * 48)
    monkeypatch.setenv("MATVERSE_OPENAI_UPSTREAM", "http://example.com/v1")

    with pytest.raises(ProxyConfigurationError, match="absolute HTTPS"):
        BlindProxyConfig.from_env()
