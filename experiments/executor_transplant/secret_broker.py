from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

DEFAULT_AUDIENCE = "matverse-secret-broker"
DEFAULT_CAPABILITY = "openai.responses.create"
DEFAULT_TIMEOUT_SECONDS = 15.0


class SecretBrokerConfigurationError(RuntimeError):
    """Raised when the OIDC-backed secret broker is not safely configured."""


class SecretBrokerError(RuntimeError):
    """Sanitized broker failure; never embeds bearer tokens or provider secrets."""


@dataclass(frozen=True)
class GitHubOIDCBrokerConfig:
    broker_base_url: str
    oidc_request_url: str
    oidc_request_token: str = field(repr=False)
    audience: str = DEFAULT_AUDIENCE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "GitHubOIDCBrokerConfig":
        broker = os.environ.get("MATVERSE_SECRET_BROKER_URL", "").strip().rstrip("/")
        request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
        request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
        audience = os.environ.get("MATVERSE_SECRET_AUDIENCE", DEFAULT_AUDIENCE).strip()

        if not broker:
            raise SecretBrokerConfigurationError("OIDC secret broker URL is unavailable")
        _validate_https_url(broker, allow_query=False, label="secret broker URL")
        if not request_url:
            raise SecretBrokerConfigurationError("GitHub OIDC request URL is unavailable")
        _validate_https_url(request_url, allow_query=True, label="GitHub OIDC request URL")
        if len(request_token) < 20 or any(ch.isspace() for ch in request_token):
            raise SecretBrokerConfigurationError("GitHub OIDC request token is unavailable or malformed")
        if not audience or len(audience) > 200 or any(ch.isspace() for ch in audience):
            raise SecretBrokerConfigurationError("OIDC audience is unavailable or malformed")

        timeout_raw = os.environ.get(
            "MATVERSE_SECRET_BROKER_TIMEOUT_SECONDS",
            str(DEFAULT_TIMEOUT_SECONDS),
        ).strip()
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise SecretBrokerConfigurationError("OIDC broker timeout must be numeric") from exc
        if not 1.0 <= timeout_seconds <= 60.0:
            raise SecretBrokerConfigurationError("OIDC broker timeout must be between 1 and 60 seconds")

        return cls(
            broker_base_url=broker,
            oidc_request_url=request_url,
            oidc_request_token=request_token,
            audience=audience,
            timeout_seconds=timeout_seconds,
        )


def _validate_https_url(
    value: str,
    *,
    allow_query: bool,
    label: str,
) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SecretBrokerConfigurationError(f"{label} must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise SecretBrokerConfigurationError(f"{label} must not embed credentials or fragments")
    if parsed.query and not allow_query:
        raise SecretBrokerConfigurationError(f"{label} must not contain query data")


def _with_audience(request_url: str, audience: str) -> str:
    parsed = urlparse(request_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["audience"] = audience
    return urlunparse(parsed._replace(query=urlencode(query)))


def _request_hash(body: bytes, secret_ref: str, capability: str) -> str:
    digest = hashlib.sha256()
    digest.update(secret_ref.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(capability.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(body)
    return digest.hexdigest()


class GitHubOIDCSecretBroker:
    """OIDC-authenticated blind provider broker.

    The broker is expected to validate the GitHub OIDC token claims and resolve
    ``secret_ref`` internally. The provider credential never needs to enter the
    GitHub runner, MatVerse executor, or local blind proxy process.
    """

    def __init__(
        self,
        config: GitHubOIDCBrokerConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
            transport=self._transport,
        )

    def acquire_oidc_token(self) -> str:
        url = _with_audience(self.config.oidc_request_url, self.config.audience)
        try:
            with self._client() as client:
                response = client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.config.oidc_request_token}",
                        "Accept": "application/json",
                        "User-Agent": "MatVerse-Secret-Broker-Client/1.0",
                    },
                )
        except httpx.HTTPError as exc:
            raise SecretBrokerError("GitHub OIDC token request failed") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise SecretBrokerError(
                f"GitHub OIDC token request returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SecretBrokerError("GitHub OIDC token response was not JSON") from exc
        token = payload.get("value") if isinstance(payload, Mapping) else None
        if not isinstance(token, str) or len(token) < 20 or any(ch.isspace() for ch in token):
            raise SecretBrokerError("GitHub OIDC token response was malformed")
        return token

    def forward(
        self,
        *,
        body: bytes,
        secret_ref: str,
        capability: str = DEFAULT_CAPABILITY,
    ) -> httpx.Response:
        if not secret_ref.startswith("secret_ref://"):
            raise SecretBrokerConfigurationError("secret_ref must use secret_ref://")
        if capability != DEFAULT_CAPABILITY:
            raise SecretBrokerConfigurationError("unsupported provider capability")

        oidc_token = self.acquire_oidc_token()
        request_hash = _request_hash(body, secret_ref, capability)
        try:
            with self._client() as client:
                return client.post(
                    f"{self.config.broker_base_url}/v1/responses",
                    content=body,
                    headers={
                        "Authorization": f"Bearer {oidc_token}",
                        "Content-Type": "application/json",
                        "User-Agent": "MatVerse-Secret-Broker-Client/1.0",
                        "X-MatVerse-Secret-Ref": secret_ref,
                        "X-MatVerse-Capability": capability,
                        "X-MatVerse-Request-Hash": request_hash,
                    },
                )
        except httpx.HTTPError as exc:
            raise SecretBrokerError("OIDC secret broker request failed") from exc
