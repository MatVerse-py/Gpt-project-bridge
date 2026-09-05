from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = "https://token.actions.githubusercontent.com/.well-known/jwks"
AUDIENCE = "matverse-secret-broker"
SECRET_REF = "secret_ref://openai/matverse/executor-transplant"
CAPABILITY = "openai.responses.create"
PROVIDER_URL = "https://api.openai.com/v1/responses"
ALLOWED_MODELS = frozenset({"gpt-5.6-sol", "gpt-6-astra"})
MAX_BODY_BYTES = 1_000_000
CLOCK_SKEW_SECONDS = 30
JWKS_CACHE_SECONDS = 300


class BrokerError(RuntimeError):
    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class BrokerPolicy:
    repository: str = "MatVerse-py/Gpt-project-bridge"
    repository_owner: str = "MatVerse-py"
    audience: str = AUDIENCE
    issuer: str = ISSUER
    allowed_events: tuple[str, ...] = ("workflow_dispatch",)
    allowed_refs: tuple[str, ...] = ("refs/heads/main",)
    workflow_ref_prefix: str = (
        "MatVerse-py/Gpt-project-bridge/.github/workflows/"
        "secret-plane-oidc-broker-v1.yml@"
    )
    runner_environment: str | None = "github-hosted"

    @classmethod
    def from_env(cls) -> "BrokerPolicy":
        refs = tuple(
            item.strip()
            for item in os.environ.get(
                "MATVERSE_ALLOWED_REFS", "refs/heads/main"
            ).split(",")
            if item.strip()
        )
        events = tuple(
            item.strip()
            for item in os.environ.get(
                "MATVERSE_ALLOWED_EVENTS", "workflow_dispatch"
            ).split(",")
            if item.strip()
        )
        workflow_ref_prefix = os.environ.get(
            "MATVERSE_ALLOWED_WORKFLOW_REF_PREFIX",
            (
                "MatVerse-py/Gpt-project-bridge/.github/workflows/"
                "secret-plane-oidc-broker-v1.yml@"
            ),
        ).strip()
        runner_environment = os.environ.get(
            "MATVERSE_ALLOWED_RUNNER_ENVIRONMENT", "github-hosted"
        ).strip() or None
        if not refs or not events or not workflow_ref_prefix:
            raise BrokerError(
                "HOLD_POLICY_INVALID", 503, "broker trust policy is incomplete"
            )
        return cls(
            repository=os.environ.get(
                "MATVERSE_ALLOWED_REPOSITORY", "MatVerse-py/Gpt-project-bridge"
            ).strip(),
            repository_owner=os.environ.get(
                "MATVERSE_ALLOWED_REPOSITORY_OWNER", "MatVerse-py"
            ).strip(),
            audience=os.environ.get(
                "MATVERSE_SECRET_AUDIENCE", AUDIENCE
            ).strip(),
            allowed_events=events,
            allowed_refs=refs,
            workflow_ref_prefix=workflow_ref_prefix,
            runner_environment=runner_environment,
        )


@dataclass
class GitHubOIDCVerifier:
    policy: BrokerPolicy
    transport: httpx.BaseTransport | None = None
    _jwks: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _jwks_loaded_at: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _load_jwks(self) -> Mapping[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._jwks is not None and now - self._jwks_loaded_at < JWKS_CACHE_SECONDS:
                return self._jwks
            try:
                with httpx.Client(
                    timeout=10.0,
                    follow_redirects=False,
                    transport=self.transport,
                ) as client:
                    response = client.get(JWKS_URL)
            except httpx.HTTPError as exc:
                raise BrokerError(
                    "HOLD_IDENTITY_KEYSET_UNAVAILABLE",
                    503,
                    "GitHub OIDC keyset is unavailable",
                ) from exc
            if response.status_code != 200:
                raise BrokerError(
                    "HOLD_IDENTITY_KEYSET_UNAVAILABLE",
                    503,
                    "GitHub OIDC keyset is unavailable",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise BrokerError(
                    "HOLD_IDENTITY_KEYSET_INVALID",
                    503,
                    "GitHub OIDC keyset is invalid",
                ) from exc
            if not isinstance(payload, Mapping) or not isinstance(payload.get("keys"), list):
                raise BrokerError(
                    "HOLD_IDENTITY_KEYSET_INVALID",
                    503,
                    "GitHub OIDC keyset is invalid",
                )
            self._jwks = dict(payload)
            self._jwks_loaded_at = now
            return self._jwks

    def verify(self, token: str) -> Mapping[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            raise BrokerError("HOLD_IDENTITY_INVALID", 401, "OIDC token is malformed")
        try:
            header = json.loads(_b64url_decode(parts[0]))
            claims = json.loads(_b64url_decode(parts[1]))
            signature = _b64url_decode_bytes(parts[2])
        except (ValueError, json.JSONDecodeError) as exc:
            raise BrokerError("HOLD_IDENTITY_INVALID", 401, "OIDC token is malformed") from exc
        if not isinstance(header, Mapping) or not isinstance(claims, Mapping):
            raise BrokerError("HOLD_IDENTITY_INVALID", 401, "OIDC token is malformed")
        if header.get("alg") != "RS256":
            raise BrokerError("HOLD_IDENTITY_ALG", 401, "OIDC token algorithm is not allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise BrokerError("HOLD_IDENTITY_INVALID", 401, "OIDC token key id is missing")
        jwks = self._load_jwks()
        key = next(
            (
                item for item in jwks["keys"]
                if isinstance(item, Mapping)
                and item.get("kid") == kid
                and item.get("kty") == "RSA"
            ),
            None,
        )
        if key is None:
            with self._lock:
                self._jwks = None
            jwks = self._load_jwks()
            key = next(
                (
                    item for item in jwks["keys"]
                    if isinstance(item, Mapping)
                    and item.get("kid") == kid
                    and item.get("kty") == "RSA"
                ),
                None,
            )
        if key is None:
            raise BrokerError("HOLD_IDENTITY_KEY_UNKNOWN", 401, "OIDC signing key is unknown")
        public_key = _rsa_public_key_from_jwk(key)
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        try:
            public_key.verify(
                signature,
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature as exc:
            raise BrokerError("HOLD_IDENTITY_SIGNATURE", 401, "OIDC signature is invalid") from exc
        self._validate_claims(claims)
        return claims

    def _validate_claims(self, claims: Mapping[str, Any]) -> None:
        now = int(time.time())
        if claims.get("iss") != self.policy.issuer:
            raise BrokerError("HOLD_IDENTITY_ISSUER", 403, "OIDC issuer is not trusted")
        aud = claims.get("aud")
        if isinstance(aud, str):
            audience_ok = aud == self.policy.audience
        elif isinstance(aud, Sequence) and not isinstance(aud, (str, bytes)):
            audience_ok = self.policy.audience in aud
        else:
            audience_ok = False
        if not audience_ok:
            raise BrokerError("HOLD_IDENTITY_AUDIENCE", 403, "OIDC audience is not trusted")
        exp = _int_claim(claims, "exp")
        iat = _int_claim(claims, "iat")
        nbf = _int_claim(claims, "nbf", required=False)
        if exp <= now - CLOCK_SKEW_SECONDS:
            raise BrokerError("HOLD_IDENTITY_EXPIRED", 401, "OIDC token is expired")
        if iat > now + CLOCK_SKEW_SECONDS:
            raise BrokerError("HOLD_IDENTITY_TIME", 401, "OIDC token issued-at is invalid")
        if nbf is not None and nbf > now + CLOCK_SKEW_SECONDS:
            raise BrokerError("HOLD_IDENTITY_TIME", 401, "OIDC token is not active")
        if claims.get("repository") != self.policy.repository:
            raise BrokerError("HOLD_IDENTITY_REPOSITORY", 403, "OIDC repository is not authorized")
        if claims.get("repository_owner") != self.policy.repository_owner:
            raise BrokerError("HOLD_IDENTITY_OWNER", 403, "OIDC repository owner is not authorized")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.startswith(f"repo:{self.policy.repository}:"):
            raise BrokerError("HOLD_IDENTITY_SUBJECT", 403, "OIDC subject is not authorized")
        event_name = claims.get("event_name")
        if event_name not in self.policy.allowed_events:
            raise BrokerError("HOLD_IDENTITY_EVENT", 403, "OIDC event is not authorized")
        ref = claims.get("ref")
        if ref not in self.policy.allowed_refs:
            raise BrokerError("HOLD_IDENTITY_REF", 403, "OIDC ref is not authorized")
        workflow_ref = claims.get("workflow_ref")
        if not isinstance(workflow_ref, str) or not workflow_ref.startswith(
            self.policy.workflow_ref_prefix
        ):
            raise BrokerError("HOLD_IDENTITY_WORKFLOW", 403, "OIDC workflow is not authorized")
        if (
            self.policy.runner_environment is not None
            and claims.get("runner_environment") != self.policy.runner_environment
        ):
            raise BrokerError("HOLD_IDENTITY_RUNNER", 403, "OIDC runner environment is not authorized")


@dataclass(frozen=True)
class BrokerSecretResolver:
    file_env_name: str = "MATVERSE_OPENAI_PROVIDER_SECRET_FILE"
    env_name: str = "MATVERSE_OPENAI_PROVIDER_SECRET"
    allow_env_name: str = "MATVERSE_ALLOW_ENV_SECRET"
    max_secret_bytes: int = 16_384

    def backend_mode(self) -> str:
        secret_file = os.environ.get(self.file_env_name, "").strip()
        if secret_file:
            return "mounted_secret_file"
        allow_env = os.environ.get(self.allow_env_name, "").strip().lower()
        if allow_env in {"1", "true", "yes"} and os.environ.get(self.env_name, "").strip():
            return "environment_transitional"
        return "unresolved"

    def configured(self) -> bool:
        return self.backend_mode() != "unresolved"

    def _read_secret(self) -> str:
        secret_file = os.environ.get(self.file_env_name, "").strip()
        if secret_file:
            path = os.path.abspath(secret_file)
            try:
                stat = os.stat(path)
            except OSError as exc:
                raise BrokerError(
                    "HOLD_SECRET_UNRESOLVED",
                    503,
                    "mounted provider credential is unavailable",
                ) from exc
            if not os.path.isfile(path) or stat.st_size <= 0 or stat.st_size > self.max_secret_bytes:
                raise BrokerError(
                    "HOLD_SECRET_UNRESOLVED",
                    503,
                    "mounted provider credential is unavailable",
                )
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    return handle.read(self.max_secret_bytes + 1).strip()
            except OSError as exc:
                raise BrokerError(
                    "HOLD_SECRET_UNRESOLVED",
                    503,
                    "mounted provider credential is unavailable",
                ) from exc

        allow_env = os.environ.get(self.allow_env_name, "").strip().lower()
        if allow_env in {"1", "true", "yes"}:
            return os.environ.get(self.env_name, "").strip()
        return ""

    def resolve(self, secret_ref: str) -> str:
        if secret_ref != SECRET_REF:
            raise BrokerError("HOLD_SECRET_REF", 403, "secret_ref is not authorized")
        secret = self._read_secret()
        if not secret:
            raise BrokerError(
                "HOLD_SECRET_UNRESOLVED",
                503,
                "provider credential is not provisioned in broker trust boundary",
            )
        if len(secret.encode("utf-8")) > self.max_secret_bytes or any(ch.isspace() for ch in secret):
            raise BrokerError(
                "HOLD_SECRET_MALFORMED",
                503,
                "provider credential is malformed",
            )
        return secret


@dataclass
class OpenAIProvider:
    transport: httpx.BaseTransport | None = None

    def forward(self, body: bytes, provider_secret: str) -> httpx.Response:
        try:
            with httpx.Client(
                timeout=240.0,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                return client.post(
                    PROVIDER_URL,
                    content=body,
                    headers={
                        "Authorization": f"Bearer {provider_secret}",
                        "Content-Type": "application/json",
                        "User-Agent": "MatVerse-External-Secret-Broker/1.0",
                    },
                )
        except httpx.HTTPError as exc:
            raise BrokerError(
                "HOLD_PROVIDER_UNAVAILABLE",
                502,
                "provider request failed before a response was received",
            ) from exc


def _b64url_decode(value: str) -> str:
    return _b64url_decode_bytes(value).decode("utf-8")


def _b64url_decode_bytes(value: str) -> bytes:
    padding_len = (-len(value)) % 4
    return base64.urlsafe_b64decode(value + ("=" * padding_len))


def _rsa_public_key_from_jwk(jwk: Mapping[str, Any]):
    n = jwk.get("n")
    e = jwk.get("e")
    if not isinstance(n, str) or not isinstance(e, str):
        raise BrokerError("HOLD_IDENTITY_KEYSET_INVALID", 503, "GitHub OIDC keyset is invalid")
    n_int = int.from_bytes(_b64url_decode_bytes(n), "big")
    e_int = int.from_bytes(_b64url_decode_bytes(e), "big")
    return rsa.RSAPublicNumbers(e_int, n_int).public_key()


def _int_claim(claims: Mapping[str, Any], key: str, *, required: bool = True) -> int | None:
    value = claims.get(key)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrokerError("HOLD_IDENTITY_TIME", 401, f"OIDC {key} claim is invalid")
    return int(value)


def _request_hash(body: bytes, secret_ref: str, capability: str) -> str:
    digest = hashlib.sha256()
    digest.update(secret_ref.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(capability.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(body)
    return digest.hexdigest()


def _bearer_token(header: str | None) -> str:
    if not header:
        return ""
    scheme, sep, token = header.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return ""
    return token.strip()


def _error_response(error: BrokerError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
            }
        },
        headers={
            "Cache-Control": "no-store",
            "X-MatVerse-Secret-Broker": "external-v1",
        },
    )


def create_app(
    *,
    verifier: GitHubOIDCVerifier | None = None,
    secret_resolver: BrokerSecretResolver | None = None,
    provider: OpenAIProvider | None = None,
) -> FastAPI:
    policy = BrokerPolicy.from_env()
    verifier = verifier or GitHubOIDCVerifier(policy)
    secret_resolver = secret_resolver or BrokerSecretResolver()
    provider = provider or OpenAIProvider()

    app = FastAPI(
        title="MatVerse External Secret Broker",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "ready": True,
                "broker_mode": "oidc-secret-ref-provider-proxy",
                "oidc_verification_enabled": True,
                "issuer": policy.issuer,
                "audience": policy.audience,
                "allowed_repository": policy.repository,
                "allowed_refs": list(policy.allowed_refs),
                "allowed_capability": CAPABILITY,
                "secret_ref": SECRET_REF,
                "provider_secret_configured": secret_resolver.configured(),
                "secret_backend_mode": secret_resolver.backend_mode(),
                "provider_secret_exposed": False,
                "provider_secret_persisted": False,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/v1/responses")
    async def responses(request: Request) -> Response:
        try:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    parsed_length = int(content_length)
                except ValueError as exc:
                    raise BrokerError(
                        "HOLD_REQUEST_SIZE", 400, "Content-Length is invalid"
                    ) from exc
                if parsed_length <= 0 or parsed_length > MAX_BODY_BYTES:
                    raise BrokerError(
                        "HOLD_REQUEST_SIZE", 413, "request body size is not allowed"
                    )

            secret_ref = request.headers.get("x-matverse-secret-ref", "")
            capability = request.headers.get("x-matverse-capability", "")
            presented_hash = request.headers.get("x-matverse-request-hash", "")
            if secret_ref != SECRET_REF:
                raise BrokerError("HOLD_SECRET_REF", 403, "secret_ref is not authorized")
            if capability != CAPABILITY:
                raise BrokerError("HOLD_CAPABILITY", 403, "capability is not authorized")
            token = _bearer_token(request.headers.get("authorization"))
            if not token:
                raise BrokerError("HOLD_IDENTITY_MISSING", 401, "OIDC bearer token is missing")

            body = await request.body()
            if not body or len(body) > MAX_BODY_BYTES:
                raise BrokerError(
                    "HOLD_REQUEST_SIZE", 413, "request body size is not allowed"
                )
            expected_hash = _request_hash(body, secret_ref, capability)
            if not presented_hash or not hmac.compare_digest(
                presented_hash.lower(), expected_hash
            ):
                raise BrokerError(
                    "HOLD_REQUEST_BINDING", 403, "request hash does not match request body"
                )
            try:
                payload = json.loads(body)
            except ValueError as exc:
                raise BrokerError("HOLD_REQUEST_JSON", 400, "request body must be JSON") from exc
            if not isinstance(payload, Mapping):
                raise BrokerError("HOLD_REQUEST_SHAPE", 400, "request body must be an object")
            if payload.get("store") is not False:
                raise BrokerError("HOLD_STORE_POLICY", 400, "store must be false")
            if "previous_response_id" in payload:
                raise BrokerError(
                    "HOLD_STATE_CARRYOVER",
                    400,
                    "previous_response_id is forbidden",
                )
            model = payload.get("model")
            if model not in ALLOWED_MODELS:
                raise BrokerError("HOLD_MODEL_POLICY", 403, "model is not authorized")

            verifier.verify(token)
            provider_secret = secret_resolver.resolve(secret_ref)
            response = provider.forward(body, provider_secret)

            allowed_headers: dict[str, str] = {
                "Cache-Control": "no-store",
                "X-MatVerse-Secret-Broker": "external-v1",
            }
            for name in ("x-request-id", "request-id"):
                value = response.headers.get(name)
                if value:
                    allowed_headers[name] = value
            content_type = response.headers.get("content-type", "application/json")
            allowed_headers["Content-Type"] = content_type
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=allowed_headers,
                media_type=None,
            )
        except BrokerError as error:
            return _error_response(error)

    return app


app = create_app()
