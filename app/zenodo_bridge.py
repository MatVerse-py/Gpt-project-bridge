from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken


class ZenodoBridgeError(RuntimeError):
    pass


class ZenodoAuthError(ZenodoBridgeError):
    pass


class ZenodoScopeError(ZenodoBridgeError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _scopes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(sorted({item for item in value.replace(",", " ").split() if item}))
    if isinstance(value, (list, tuple, set)):
        return tuple(sorted({str(item).strip() for item in value if str(item).strip()}))
    return ()


@dataclass(frozen=True)
class ZenodoConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    base_url: str = "https://zenodo.org"
    scopes: tuple[str, ...] = ("deposit:write",)

    @classmethod
    def from_env(cls) -> "ZenodoConfig":
        client_id = os.getenv("ZENODO_CLIENT_ID", "").strip()
        client_secret = os.getenv("ZENODO_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("ZENODO_REDIRECT_URI", "").strip()
        if not client_id or not client_secret or not redirect_uri:
            raise ZenodoAuthError(
                "ZENODO_CLIENT_ID, ZENODO_CLIENT_SECRET and ZENODO_REDIRECT_URI are required"
            )
        scope_value = os.getenv("ZENODO_SCOPES", "deposit:write")
        base_url = os.getenv("ZENODO_BASE_URL", "https://zenodo.org").rstrip("/")
        if not base_url.startswith("https://") and not base_url.startswith("http://localhost"):
            raise ZenodoAuthError("ZENODO_BASE_URL must use HTTPS except localhost")
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            base_url=base_url,
            scopes=_scopes(scope_value) or ("deposit:write",),
        )

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.base_url}/oauth/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.base_url}/oauth/token"

    @property
    def api_base(self) -> str:
        return f"{self.base_url}/api"


class EncryptedZenodoTokenStore:
    """Encrypted local session store.

    The SQLite database contains only Fernet ciphertext and non-secret status
    metadata. The encryption key MUST come from a runtime secret and MUST NOT be
    committed. This is a deployable bridge store, not a substitute for the
    MatVerse Secret Plane; production deployments should place the key in the
    platform secret manager / Secret Plane.
    """

    def __init__(self, db_path: str | os.PathLike[str], encryption_key: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except Exception as exc:
            raise ZenodoAuthError(
                "ZENODO_TOKEN_ENCRYPTION_KEY must be a valid Fernet key"
            ) from exc
        self._init_db()

    @classmethod
    def from_env(cls) -> "EncryptedZenodoTokenStore":
        key = os.getenv("ZENODO_TOKEN_ENCRYPTION_KEY", "").strip()
        if not key:
            raise ZenodoAuthError("ZENODO_TOKEN_ENCRYPTION_KEY is required")
        path = os.getenv("ZENODO_TOKEN_DB", ".data/zenodo-oauth.sqlite3")
        return cls(path, key)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS zenodo_token(
                    subject TEXT PRIMARY KEY,
                    ciphertext BLOB NOT NULL,
                    scopes TEXT NOT NULL,
                    stored_at INTEGER NOT NULL,
                    expires_at INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS zenodo_oauth_state(
                    nonce TEXT PRIMARY KEY,
                    issued_at INTEGER NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def remember_state(self, nonce: str, issued_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO zenodo_oauth_state(nonce, issued_at, consumed) VALUES(?, ?, 0)",
                (nonce, issued_at),
            )

    def consume_state(self, nonce: str) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT consumed FROM zenodo_oauth_state WHERE nonce = ?", (nonce,)
            ).fetchone()
            if row is None or int(row["consumed"]) != 0:
                conn.rollback()
                return False
            conn.execute(
                "UPDATE zenodo_oauth_state SET consumed = 1 WHERE nonce = ?", (nonce,)
            )
            conn.commit()
            return True

    def save_token_response(self, payload: dict[str, Any], subject: str = "default") -> None:
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise ZenodoAuthError("Zenodo token response did not contain access_token")
        now = int(time.time())
        expires_in = payload.get("expires_in")
        expires_at = now + int(expires_in) if expires_in is not None else None
        scopes = _scopes(payload.get("scope"))
        ciphertext = self._fernet.encrypt(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO zenodo_token(subject, ciphertext, scopes, stored_at, expires_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(subject) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    scopes = excluded.scopes,
                    stored_at = excluded.stored_at,
                    expires_at = excluded.expires_at
                """,
                (subject, ciphertext, " ".join(scopes), now, expires_at),
            )

    def load_token_response(self, subject: str = "default") -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ciphertext, expires_at FROM zenodo_token WHERE subject = ?",
                (subject,),
            ).fetchone()
        if row is None:
            raise ZenodoAuthError("Zenodo is not connected")
        if row["expires_at"] is not None and int(row["expires_at"]) <= int(time.time()):
            raise ZenodoAuthError("Stored Zenodo access token is expired; reconnect required")
        try:
            raw = self._fernet.decrypt(bytes(row["ciphertext"]))
        except InvalidToken as exc:
            raise ZenodoAuthError("Cannot decrypt stored Zenodo token") from exc
        payload = json.loads(raw.decode("utf-8"))
        if not payload.get("access_token"):
            raise ZenodoAuthError("Stored Zenodo token is invalid")
        return payload

    def status(self, subject: str = "default") -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT scopes, stored_at, expires_at FROM zenodo_token WHERE subject = ?",
                (subject,),
            ).fetchone()
        if row is None:
            return {"connected": False, "scopes": [], "expires_at": None}
        expires_at = row["expires_at"]
        connected = expires_at is None or int(expires_at) > int(time.time())
        return {
            "connected": connected,
            "scopes": list(_scopes(row["scopes"])),
            "stored_at": int(row["stored_at"]),
            "expires_at": int(expires_at) if expires_at is not None else None,
        }

    def has_scope(self, scope: str, subject: str = "default") -> bool:
        return scope in set(self.status(subject).get("scopes", []))


class ZenodoOAuthState:
    def __init__(
        self,
        store: EncryptedZenodoTokenStore,
        signing_secret: str,
        max_age_seconds: int = 600,
    ) -> None:
        if len(signing_secret.encode("utf-8")) < 32:
            raise ZenodoAuthError("ZENODO_OAUTH_STATE_SECRET must be at least 32 bytes")
        self.store = store
        self.secret = signing_secret.encode("utf-8")
        self.max_age_seconds = max_age_seconds

    @classmethod
    def from_env(cls, store: EncryptedZenodoTokenStore) -> "ZenodoOAuthState":
        secret = os.getenv("ZENODO_OAUTH_STATE_SECRET", "")
        return cls(store, secret)

    def issue(self) -> str:
        now = int(time.time())
        nonce = secrets.token_urlsafe(24)
        payload = json.dumps(
            {"nonce": nonce, "iat": now}, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        encoded = _b64url(payload)
        signature = _b64url(
            hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        self.store.remember_state(nonce, now)
        return f"{encoded}.{signature}"

    def validate_once(self, state: str) -> dict[str, Any]:
        try:
            encoded, signature = state.split(".", 1)
            expected = _b64url(
                hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ZenodoAuthError("Invalid OAuth state signature")
            payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
            nonce = str(payload["nonce"])
            issued_at = int(payload["iat"])
        except ZenodoAuthError:
            raise
        except Exception as exc:
            raise ZenodoAuthError("Malformed OAuth state") from exc
        age = int(time.time()) - issued_at
        if age < 0 or age > self.max_age_seconds:
            raise ZenodoAuthError("OAuth state expired")
        if not self.store.consume_state(nonce):
            raise ZenodoAuthError("OAuth state is unknown or already consumed")
        return payload


class ZenodoOAuthClient:
    def __init__(
        self,
        config: ZenodoConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": " ".join(self.config.scopes),
                "state": state,
            }
        )
        return f"{self.config.authorize_endpoint}?{query}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        if not code:
            raise ZenodoAuthError("OAuth authorization code is required")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
        }
        with httpx.Client(
            timeout=30.0, follow_redirects=False, transport=self.transport
        ) as client:
            response = client.post(self.config.token_endpoint, data=data)
        if response.status_code >= 400:
            raise ZenodoAuthError(
                f"Zenodo OAuth token exchange failed with HTTP {response.status_code}"
            )
        payload = response.json()
        if not payload.get("access_token"):
            raise ZenodoAuthError("Zenodo OAuth response did not include an access token")
        return payload


class ZenodoAPI:
    def __init__(
        self,
        config: ZenodoConfig,
        store: EncryptedZenodoTokenStore,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        token = self.store.load_token_response().get("access_token")
        return {"Authorization": f"Bearer {token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.config.api_base}{path}"
        headers = self._headers()
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        with httpx.Client(
            timeout=30.0, follow_redirects=False, transport=self.transport
        ) as client:
            response = client.request(
                method, url, headers=headers, params=params, json=json_body
            )
        if response.status_code >= 400:
            raise ZenodoBridgeError(
                f"Zenodo API request failed: {method} {path} -> HTTP {response.status_code}"
            )
        if response.status_code == 204 or not response.content:
            return {"ok": True, "status_code": response.status_code}
        return response.json()

    def search_records(self, q: str = "", page: int = 1, size: int = 20) -> Any:
        return self._request(
            "GET", "/records", params={"q": q, "page": page, "size": min(max(size, 1), 100)}
        )

    def get_record(self, record_id: str) -> Any:
        return self._request("GET", f"/records/{record_id}")

    def list_depositions(
        self,
        q: str = "",
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Any:
        params: dict[str, Any] = {
            "q": q,
            "page": page,
            "size": min(max(size, 1), 100),
        }
        if status:
            if status not in {"draft", "published"}:
                raise ValueError("status must be draft or published")
            params["status"] = status
        return self._request("GET", "/deposit/depositions", params=params)

    def get_deposition(self, deposition_id: int) -> Any:
        return self._request("GET", f"/deposit/depositions/{int(deposition_id)}")

    def create_draft(self, metadata: dict[str, Any]) -> Any:
        if not self.store.has_scope("deposit:write"):
            raise ZenodoScopeError("deposit:write scope is required")
        return self._request(
            "POST", "/deposit/depositions", json_body={"metadata": metadata}
        )

    def publish_draft(self, deposition_id: int, confirmation: str) -> Any:
        if confirmation.strip() != f"PUBLISH ZENODO {int(deposition_id)}":
            raise ZenodoBridgeError(
                f"Human confirmation must be exactly: PUBLISH ZENODO {int(deposition_id)}"
            )
        if not self.store.has_scope("deposit:actions"):
            raise ZenodoScopeError(
                "deposit:actions scope is required; reconnect Zenodo with expanded scope"
            )
        return self._request(
            "POST", f"/deposit/depositions/{int(deposition_id)}/actions/publish"
        )


def runtime_from_env(
    *, transport: httpx.BaseTransport | None = None
) -> tuple[ZenodoConfig, EncryptedZenodoTokenStore, ZenodoOAuthState, ZenodoOAuthClient, ZenodoAPI]:
    config = ZenodoConfig.from_env()
    store = EncryptedZenodoTokenStore.from_env()
    state = ZenodoOAuthState.from_env(store)
    oauth = ZenodoOAuthClient(config, transport=transport)
    api = ZenodoAPI(config, store, transport=transport)
    return config, store, state, oauth, api
