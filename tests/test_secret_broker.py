from __future__ import annotations

import json

import httpx
import pytest

from experiments.executor_transplant.secret_broker import (
    GitHubOIDCBrokerConfig,
    GitHubOIDCSecretBroker,
    SecretBrokerConfigurationError,
)


def test_oidc_broker_rejects_plaintext_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MATVERSE_SECRET_BROKER_URL", "http://broker.example/v1")
    monkeypatch.setenv(
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "https://pipelines.actions.githubusercontent.com/token",
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "t" * 48)

    with pytest.raises(
        SecretBrokerConfigurationError,
        match="absolute HTTPS",
    ):
        GitHubOIDCBrokerConfig.from_env()


def test_oidc_config_repr_hides_request_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_token = "github-oidc-request-" + ("x" * 32)
    monkeypatch.setenv("MATVERSE_SECRET_BROKER_URL", "https://broker.example")
    monkeypatch.setenv(
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "https://pipelines.actions.githubusercontent.com/token?api-version=1",
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", request_token)

    config = GitHubOIDCBrokerConfig.from_env()
    assert request_token not in repr(config)


def test_oidc_broker_forwards_secret_ref_without_provider_secret() -> None:
    events: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pipelines.actions.githubusercontent.com":
            events.append(("oidc", str(request.url)))
            assert request.headers["authorization"] == "Bearer runner-request-token"
            assert "audience=matverse-secret-broker" in str(request.url)
            return httpx.Response(200, json={"value": "oidc-jwt-" + ("a" * 40)})

        assert request.url == httpx.URL("https://broker.example/v1/responses")
        events.append(("broker", str(request.url)))
        assert request.headers["authorization"].startswith("Bearer oidc-jwt-")
        assert (
            request.headers["x-matverse-secret-ref"]
            == "secret_ref://openai/matverse/executor-transplant"
        )
        assert request.headers["x-matverse-capability"] == "openai.responses.create"
        assert len(request.headers["x-matverse-request-hash"]) == 64
        body = json.loads(request.content.decode("utf-8"))
        assert body["store"] is False
        assert "OPENAI_API_KEY" not in request.content.decode("utf-8")
        return httpx.Response(
            200,
            headers={"x-request-id": "req-broker"},
            json={
                "id": "resp-broker",
                "object": "response",
                "model": "gpt-5.6-sol",
                "output": [],
                "usage": {},
            },
        )

    broker = GitHubOIDCSecretBroker(
        GitHubOIDCBrokerConfig(
            broker_base_url="https://broker.example",
            oidc_request_url=(
                "https://pipelines.actions.githubusercontent.com/token?api-version=1"
            ),
            oidc_request_token="runner-request-token",
        ),
        transport=httpx.MockTransport(handler),
    )
    response = broker.forward(
        body=json.dumps({"model": "gpt-5.6-sol", "store": False}).encode("utf-8"),
        secret_ref="secret_ref://openai/matverse/executor-transplant",
    )

    assert response.status_code == 200
    assert [event[0] for event in events] == ["oidc", "broker"]
