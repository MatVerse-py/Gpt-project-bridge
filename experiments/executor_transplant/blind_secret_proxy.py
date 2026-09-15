from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import urlparse

import httpx

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_UPSTREAM = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 240.0
DEFAULT_TTL_SECONDS = 600
DEFAULT_MAX_USAGE = 3
MAX_REQUEST_BYTES = 1_000_000


class ProxyConfigurationError(RuntimeError):
    """Raised for fail-closed blind-secret proxy configuration errors."""


@dataclass
class CapabilityLease:
    token: str = field(repr=False)
    ttl_seconds: int
    max_usage: int
    issued_monotonic: float = field(default_factory=time.monotonic)
    _usage: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def authorize_and_consume(self, presented: str) -> tuple[bool, str]:
        if not presented or not hmac.compare_digest(presented, self.token):
            return False, "invalid_capability"
        now = time.monotonic()
        with self._lock:
            if now - self.issued_monotonic > self.ttl_seconds:
                return False, "capability_expired"
            if self._usage >= self.max_usage:
                return False, "capability_exhausted"
            self._usage += 1
        return True, "authorized"

    @property
    def usage(self) -> int:
        with self._lock:
            return self._usage


@dataclass(frozen=True)
class BlindProxyConfig:
    provider_key: str = field(repr=False)
    capability_token: str = field(repr=False)
    secret_ref: str
    upstream_base_url: str
    timeout_seconds: float
    ttl_seconds: int
    max_usage: int
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @classmethod
    def from_env(cls, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> "BlindProxyConfig":
        provider_key = os.environ.get("OPENAI_API_KEY", "").strip()
        capability_token = os.environ.get("MATVERSE_PROXY_TOKEN", "").strip()
        secret_ref = os.environ.get(
            "MATVERSE_SECRET_REF",
            "secret_ref://openai/matverse/executor-transplant",
        ).strip()
        upstream = os.environ.get(
            "MATVERSE_OPENAI_UPSTREAM",
            DEFAULT_UPSTREAM,
        ).strip().rstrip("/")

        if not provider_key:
            raise ProxyConfigurationError("provider secret is unavailable")
        if any(ch.isspace() for ch in provider_key):
            raise ProxyConfigurationError("provider secret is malformed")
        if len(capability_token) < 32 or any(ch.isspace() for ch in capability_token):
            raise ProxyConfigurationError("ephemeral capability token is unavailable or malformed")
        if not secret_ref.startswith("secret_ref://"):
            raise ProxyConfigurationError("MATVERSE_SECRET_REF must use secret_ref://")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ProxyConfigurationError("blind proxy must bind to loopback")

        parsed = urlparse(upstream)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ProxyConfigurationError("upstream must be an absolute HTTPS URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProxyConfigurationError("upstream URL must not embed credentials or query data")

        try:
            timeout_seconds = float(
                os.environ.get("MATVERSE_PROXY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            )
            ttl_seconds = int(
                os.environ.get("MATVERSE_PROXY_TTL_SECONDS", DEFAULT_TTL_SECONDS)
            )
            max_usage = int(
                os.environ.get("MATVERSE_PROXY_MAX_USAGE", DEFAULT_MAX_USAGE)
            )
        except ValueError as exc:
            raise ProxyConfigurationError("proxy numeric configuration is invalid") from exc

        if not 1.0 <= timeout_seconds <= 600.0:
            raise ProxyConfigurationError("proxy timeout must be between 1 and 600 seconds")
        if not 1 <= ttl_seconds <= 3600:
            raise ProxyConfigurationError("capability TTL must be between 1 and 3600 seconds")
        if not 1 <= max_usage <= 100:
            raise ProxyConfigurationError("capability max usage must be between 1 and 100")
        if not 1 <= port <= 65535:
            raise ProxyConfigurationError("invalid proxy port")

        return cls(
            provider_key=provider_key,
            capability_token=capability_token,
            secret_ref=secret_ref,
            upstream_base_url=upstream,
            timeout_seconds=timeout_seconds,
            ttl_seconds=ttl_seconds,
            max_usage=max_usage,
            host=host,
            port=port,
        )


def _bearer_token(header: str | None) -> str:
    if not header:
        return ""
    scheme, sep, value = header.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return ""
    return value.strip()


def _safe_error(code: str) -> bytes:
    return json.dumps(
        {"error": {"code": code, "message": "request not authorized by MatVerse secret plane"}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class BlindSecretProxy:
    """Process-isolated blind-secret handler for OpenAI Responses.

    The transplant runner receives only an ephemeral capability token and a
    loopback endpoint. The provider key stays in this handler process. This is
    process isolation, not an HSM/TEE claim.
    """

    def __init__(self, config: BlindProxyConfig) -> None:
        self.config = config
        self.lease = CapabilityLease(
            token=config.capability_token,
            ttl_seconds=config.ttl_seconds,
            max_usage=config.max_usage,
        )

    def forward(self, body: bytes) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.config.provider_key}",
            "Content-Type": "application/json",
            "User-Agent": "MatVerse-Blind-Secret-Handler/1.0",
        }
        return httpx.post(
            f"{self.config.upstream_base_url}/responses",
            content=body,
            headers=headers,
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
        )

    def handler_class(self) -> type[BaseHTTPRequestHandler]:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            server_version: ClassVar[str] = "MatVerseBlindSecret/1.0"
            sys_version: ClassVar[str] = ""

            def log_message(self, _format: str, *args: object) -> None:
                # Deliberately silent: request headers and bodies must not enter logs.
                return

            def _write(
                self,
                status: int,
                body: bytes,
                *,
                content_type: str = "application/json",
                extra_headers: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-MatVerse-Secret-Plane", "blind-proxy-v1")
                if extra_headers:
                    for key, value in extra_headers.items():
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/healthz":
                    self._write(404, _safe_error("not_found"))
                    return
                payload = {
                    "ready": True,
                    "mode": "blind_proxy",
                    "secret_ref": proxy.config.secret_ref,
                    "provider_secret_exposed_to_runner": False,
                    "max_usage": proxy.config.max_usage,
                    "usage": proxy.lease.usage,
                }
                self._write(
                    200,
                    json.dumps(payload, sort_keys=True).encode("utf-8"),
                )

            def do_POST(self) -> None:  # noqa: N802
                if self.path not in {"/responses", "/v1/responses"}:
                    self._write(404, _safe_error("not_found"))
                    return

                presented = _bearer_token(self.headers.get("Authorization"))
                authorized, reason = proxy.lease.authorize_and_consume(presented)
                if not authorized:
                    self._write(401, _safe_error(reason))
                    return

                raw_length = self.headers.get("Content-Length", "")
                try:
                    content_length = int(raw_length)
                except ValueError:
                    self._write(400, _safe_error("invalid_content_length"))
                    return
                if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                    self._write(413, _safe_error("request_size_rejected"))
                    return

                body = self.rfile.read(content_length)
                try:
                    decoded = json.loads(body)
                except ValueError:
                    self._write(400, _safe_error("invalid_json"))
                    return
                if not isinstance(decoded, dict):
                    self._write(400, _safe_error("invalid_request_shape"))
                    return
                if decoded.get("store") is not False:
                    self._write(400, _safe_error("store_must_be_false"))
                    return
                if "previous_response_id" in decoded:
                    self._write(400, _safe_error("previous_response_id_forbidden"))
                    return

                try:
                    response = proxy.forward(body)
                except httpx.HTTPError:
                    self._write(502, _safe_error("upstream_unavailable"))
                    return

                selected_headers: dict[str, str] = {}
                for source, target in (
                    ("x-request-id", "x-request-id"),
                    ("request-id", "request-id"),
                ):
                    value = response.headers.get(source)
                    if value:
                        selected_headers[target] = value
                content_type = response.headers.get(
                    "content-type",
                    "application/json",
                )
                self._write(
                    response.status_code,
                    response.content,
                    content_type=content_type,
                    extra_headers=selected_headers,
                )

            def do_PUT(self) -> None:  # noqa: N802
                self._write(405, _safe_error("method_not_allowed"))

            def do_PATCH(self) -> None:  # noqa: N802
                self._write(405, _safe_error("method_not_allowed"))

            def do_DELETE(self) -> None:  # noqa: N802
                self._write(405, _safe_error("method_not_allowed"))

        return Handler

    def serve_forever(self) -> None:
        server = ThreadingHTTPServer(
            (self.config.host, self.config.port),
            self.handler_class(),
        )
        # Startup output is intentionally non-sensitive and machine-readable.
        print(
            json.dumps(
                {
                    "ready": True,
                    "mode": "blind_proxy",
                    "listen": f"http://{self.config.host}:{self.config.port}",
                    "secret_ref": self.config.secret_ref,
                    "ttl_seconds": self.config.ttl_seconds,
                    "max_usage": self.config.max_usage,
                    "provider_secret_exposed_to_runner": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        server.serve_forever(poll_interval=0.25)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MatVerse process-isolated blind-secret proxy"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    try:
        config = BlindProxyConfig.from_env(host=args.host, port=args.port)
    except ProxyConfigurationError as exc:
        print(
            json.dumps(
                {
                    "ready": False,
                    "mode": "blind_proxy",
                    "reason": str(exc),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 2

    BlindSecretProxy(config).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
