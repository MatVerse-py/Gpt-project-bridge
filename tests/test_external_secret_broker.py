from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from services.external_secret_broker.app import (
    AUDIENCE,
    CAPABILITY,
    ISSUER,
    JWKS_URL,
    SECRET_REF,
    BrokerError,
    BrokerPolicy,
    BrokerSecretResolver,
    GitHubOIDCVerifier,
    OpenAIProvider,
    _request_hash,
    create_app,
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwk(private_key, kid: str = "test-key") -> dict[str, str]:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _claims(**overrides):
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "repo:MatVerse-py/Gpt-project-bridge:ref:refs/heads/main",
        "exp": now + 300,
        "iat": now - 5,
        "nbf": now - 5,
        "repository": "MatVerse-py/Gpt-project-bridge",
        "repository_owner": "MatVerse-py",
        "event_name": "workflow_dispatch",
        "ref": "refs/heads/main",
        "workflow_ref": (
            "MatVerse-py/Gpt-project-bridge/.github/workflows/"
            "secret-plane-oidc-broker-v1.yml@refs/heads/main"
        ),
        "runner_environment": "github-hosted",
    }
    base.update(overrides)
    return base


def _token(private_key, claims: dict, kid: str = "test-key") -> str:
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    header_part = _b64(json.dumps(header, separators=(",", ":")).encode())
    claims_part = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header_part}.{claims_part}".encode("ascii")
    signature = private_key.sign(
        signing_input,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{header_part}.{claims_part}.{_b64(signature)}"


def _verifier(private_key, claims: dict):
    jwk = _jwk(private_key)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        return httpx.Response(200, json={"keys": [jwk]})

    verifier = GitHubOIDCVerifier(
        BrokerPolicy(),
        transport=httpx.MockTransport(handler),
    )
    return verifier, _token(private_key, claims)


def test_valid_oidc_signature_and_claims_pass() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier, token = _verifier(private_key, _claims())
    claims = verifier.verify(token)
    assert claims["repository"] == "MatVerse-py/Gpt-project-bridge"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"aud": "wrong-audience"}, "HOLD_IDENTITY_AUDIENCE"),
        ({"repository": "evil/repo"}, "HOLD_IDENTITY_REPOSITORY"),
        ({"ref": "refs/heads/untrusted"}, "HOLD_IDENTITY_REF"),
        ({"event_name": "pull_request"}, "HOLD_IDENTITY_EVENT"),
        (
            {"workflow_ref": "evil/repo/.github/workflows/x.yml@refs/heads/main"},
            "HOLD_IDENTITY_WORKFLOW",
        ),
    ],
)
def test_oidc_claim_policy_fails_closed(overrides, code) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier, token = _verifier(private_key, _claims(**overrides))
    with pytest.raises(BrokerError) as exc:
        verifier.verify(token)
    assert exc.value.code == code


class _AcceptVerifier:
    def verify(self, token: str):
        assert token == "oidc-token"
        return _claims()


def _client(monkeypatch, tmp_path, *, provider_transport=None, with_secret=False):
    monkeypatch.delenv("MATVERSE_OPENAI_PROVIDER_SECRET", raising=False)
    monkeypatch.delenv("MATVERSE_ALLOW_ENV_SECRET", raising=False)
    monkeypatch.delenv("MATVERSE_OPENAI_PROVIDER_SECRET_FILE", raising=False)
    if with_secret:
        secret_file = tmp_path / "openai-provider-secret"
        secret_file.write_text("sk-test-provider-material", encoding="utf-8")
        monkeypatch.setenv("MATVERSE_OPENAI_PROVIDER_SECRET_FILE", str(secret_file))
    provider = OpenAIProvider(transport=provider_transport)
    app = create_app(
        verifier=_AcceptVerifier(),
        secret_resolver=BrokerSecretResolver(),
        provider=provider,
    )
    return TestClient(app)


def _headers(body: bytes, *, request_hash: str | None = None):
    return {
        "Authorization": "Bearer oidc-token",
        "X-MatVerse-Secret-Ref": SECRET_REF,
        "X-MatVerse-Capability": CAPABILITY,
        "X-MatVerse-Request-Hash": request_hash or _request_hash(
            body, SECRET_REF, CAPABILITY
        ),
        "Content-Type": "application/json",
    }


def test_missing_external_provider_secret_is_hold(monkeypatch, tmp_path) -> None:
    body = json.dumps({"model": "gpt-5.6-sol", "store": False}).encode()
    client = _client(monkeypatch, tmp_path, with_secret=False)
    response = client.post("/v1/responses", content=body, headers=_headers(body))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "HOLD_SECRET_UNRESOLVED"


def test_authorized_request_forwards_without_exposing_secret(monkeypatch, tmp_path) -> None:
    def provider_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-test-provider-material"
        assert b"sk-test-provider-material" not in request.content
        return httpx.Response(
            200,
            headers={"x-request-id": "req-provider"},
            json={
                "id": "resp-provider",
                "model": "gpt-5.6-sol",
                "output": [],
                "usage": {},
            },
        )

    body = json.dumps({"model": "gpt-5.6-sol", "store": False}).encode()
    client = _client(
        monkeypatch,
        tmp_path,
        provider_transport=httpx.MockTransport(provider_handler),
        with_secret=True,
    )
    response = client.post("/v1/responses", content=body, headers=_headers(body))
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-provider"
    assert "sk-test-provider-material" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "gpt-5.6-sol", "store": True},
        {
            "model": "gpt-5.6-sol",
            "store": False,
            "previous_response_id": "resp-old",
        },
        {"model": "not-authorized", "store": False},
    ],
)
def test_request_policy_rejects_unsafe_payload(monkeypatch, tmp_path, payload) -> None:
    body = json.dumps(payload).encode()
    client = _client(monkeypatch, tmp_path, with_secret=True)
    response = client.post("/v1/responses", content=body, headers=_headers(body))
    assert response.status_code in {400, 403}


def test_request_hash_binding_is_enforced(monkeypatch, tmp_path) -> None:
    body = json.dumps({"model": "gpt-5.6-sol", "store": False}).encode()
    client = _client(monkeypatch, tmp_path, with_secret=True)
    response = client.post(
        "/v1/responses",
        content=body,
        headers=_headers(body, request_hash="0" * 64),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "HOLD_REQUEST_BINDING"


def test_health_is_non_sensitive(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, with_secret=True)
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_secret_configured"] is True
    assert payload["provider_secret_exposed"] is False
    assert "sk-test-provider-material" not in response.text
    assert payload["secret_backend_mode"] == "mounted_secret_file"


def test_environment_secret_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MATVERSE_OPENAI_PROVIDER_SECRET_FILE", raising=False)
    monkeypatch.setenv("MATVERSE_OPENAI_PROVIDER_SECRET", "sk-env-should-not-be-used")
    monkeypatch.delenv("MATVERSE_ALLOW_ENV_SECRET", raising=False)
    resolver = BrokerSecretResolver()
    assert resolver.backend_mode() == "unresolved"
    with pytest.raises(BrokerError) as exc:
        resolver.resolve(SECRET_REF)
    assert exc.value.code == "HOLD_SECRET_UNRESOLVED"
