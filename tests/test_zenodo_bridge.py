from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from app.zenodo_bridge import (
    EncryptedZenodoTokenStore,
    ZenodoAPI,
    ZenodoAuthError,
    ZenodoConfig,
    ZenodoOAuthClient,
    ZenodoOAuthState,
    ZenodoScopeError,
)


def config(scopes: tuple[str, ...] = ("deposit:write",)) -> ZenodoConfig:
    return ZenodoConfig(
        client_id="client-123",
        client_secret="secret-456",
        redirect_uri="http://localhost:8788/zenodo/oauth/callback",
        base_url="https://zenodo.org",
        scopes=scopes,
    )


def store(tmp_path: Path) -> EncryptedZenodoTokenStore:
    return EncryptedZenodoTokenStore(
        tmp_path / "zenodo.sqlite3", Fernet.generate_key().decode("ascii")
    )


def test_oauth_authorization_url_is_exact_and_scoped() -> None:
    oauth = ZenodoOAuthClient(config())
    parsed = urlparse(oauth.authorization_url("signed-state"))
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "zenodo.org"
    assert parsed.path == "/oauth/authorize"
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["client-123"]
    assert params["redirect_uri"] == ["http://localhost:8788/zenodo/oauth/callback"]
    assert params["scope"] == ["deposit:write"]
    assert params["state"] == ["signed-state"]


def test_oauth_state_is_signed_one_time_and_replay_safe(tmp_path: Path) -> None:
    token_store = store(tmp_path)
    manager = ZenodoOAuthState(token_store, "x" * 48)
    state = manager.issue()
    payload = manager.validate_once(state)
    assert payload["nonce"]
    with pytest.raises(ZenodoAuthError, match="already consumed"):
        manager.validate_once(state)

    token_store_2 = store(tmp_path / "tamper")
    manager_2 = ZenodoOAuthState(token_store_2, "y" * 48)
    original = manager_2.issue()
    tampered = original[:-1] + ("A" if original[-1] != "A" else "B")
    with pytest.raises(ZenodoAuthError):
        manager_2.validate_once(tampered)


def test_token_store_encrypts_access_token_and_status_never_exposes_it(tmp_path: Path) -> None:
    token_store = store(tmp_path)
    token_store.save_token_response(
        {
            "access_token": "super-secret-token-value",
            "token_type": "Bearer",
            "scope": "deposit:write",
            "expires_in": 3600,
        }
    )
    raw_db = (tmp_path / "zenodo.sqlite3").read_bytes()
    assert b"super-secret-token-value" not in raw_db

    status = token_store.status()
    assert status["connected"] is True
    assert status["scopes"] == ["deposit:write"]
    assert "access_token" not in json.dumps(status)


def test_exchange_code_uses_zenodo_token_endpoint_and_preserves_requested_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://zenodo.org/oauth/token")
        body = request.content.decode("utf-8")
        assert "grant_type=authorization_code" in body
        assert "client_id=client-123" in body
        assert "client_secret=secret-456" in body
        assert "code=abc123" in body
        return httpx.Response(
            200,
            json={"access_token": "token", "token_type": "Bearer", "expires_in": 3600},
        )

    oauth = ZenodoOAuthClient(config(), transport=httpx.MockTransport(handler))
    payload = oauth.exchange_code("abc123")
    assert payload["access_token"] == "token"
    assert payload["scope"] == "deposit:write"


def test_api_uses_bearer_header_and_lists_user_depositions(tmp_path: Path) -> None:
    token_store = store(tmp_path)
    token_store.save_token_response(
        {"access_token": "token-123", "scope": "deposit:write"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token-123"
        assert request.url.path == "/api/deposit/depositions"
        return httpx.Response(200, json=[{"id": 42, "submitted": False}])

    api = ZenodoAPI(config(), token_store, transport=httpx.MockTransport(handler))
    rows = api.list_depositions(status="draft")
    assert rows[0]["id"] == 42


def test_publish_is_double_gated_by_scope_and_exact_confirmation(tmp_path: Path) -> None:
    token_store = store(tmp_path)
    token_store.save_token_response(
        {"access_token": "token-123", "scope": "deposit:write"}
    )
    api = ZenodoAPI(config(), token_store)

    with pytest.raises(Exception, match="Human confirmation"):
        api.publish_draft(42, "yes")

    with pytest.raises(ZenodoScopeError, match="deposit:actions"):
        api.publish_draft(42, "PUBLISH ZENODO 42")


def test_publish_calls_only_publish_action_when_scope_is_present(tmp_path: Path) -> None:
    token_store = store(tmp_path)
    token_store.save_token_response(
        {"access_token": "token-123", "scope": "deposit:write deposit:actions"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/deposit/depositions/42/actions/publish"
        assert request.headers["authorization"] == "Bearer token-123"
        return httpx.Response(202, json={"id": 42, "submitted": True})

    api = ZenodoAPI(
        config(("deposit:write", "deposit:actions")),
        token_store,
        transport=httpx.MockTransport(handler),
    )
    result = api.publish_draft(42, "PUBLISH ZENODO 42")
    assert result["submitted"] is True
