from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from projectvault.access_fabric import ACCESS_PROTOCOL, AccessFabric, load_partition_specs
from projectvault.auth import Principal
from projectvault.config import Settings
from projectvault.db import Database
from projectvault.mcp import tools


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "vault.db",
        host="127.0.0.1",
        port=8787,
        public_base_url="http://127.0.0.1:8787",
        auth_mode="disabled",
        static_token=None,
        oidc_issuer=None,
        oidc_audience=None,
        oidc_jwks_url=None,
        required_scope="projects.read",
        allowed_origins=("https://chatgpt.com",),
        max_results=20,
    )


def owner(*scopes: str) -> Principal:
    return Principal("owner-test", frozenset(scopes or ("projects.read",)), {})


def seed_document(db: Database) -> None:
    db.upsert_project("patente", "PATENTE", "test")
    db.upsert_document(
        {
            "document_id": "chat:one",
            "conversation_id": "one",
            "title": "Bridge access fabric",
            "project_id": "patente",
            "project_name": "PATENTE",
            "attribution_basis": "test",
            "created_at_epoch": 1.0,
            "updated_at_epoch": 2.0,
            "source_file": "test.json",
            "source_hash": "a" * 64,
            "body": "# Bridge access fabric\n\nFederated owner information retrieval.",
            "metadata": {"source_type": "conversation", "message_count": 1},
        }
    )


def write_registry(tmp_path: Path, partitions: list[dict[str, Any]]) -> Path:
    path = tmp_path / "partitions.json"
    path.write_text(
        json.dumps({"protocol": ACCESS_PROTOCOL, "partitions": partitions}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_local_projectvault_partition_is_always_registered(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    fabric = AccessFabric(cfg, Database(cfg.database_path))
    listed = fabric.list_partitions(owner())
    assert listed["protocol"] == ACCESS_PROTOCOL
    assert listed["partitions"][0]["partition_id"] == "projectvault"
    assert listed["partitions"][0]["access_status"] == "GRANTED"


def test_federated_local_search_and_fetch_use_namespaced_references(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    db = Database(cfg.database_path)
    seed_document(db)
    fabric = AccessFabric(cfg, db)

    searched = fabric.search(owner(), "federated owner", purpose="answer the owner's question")
    assert len(searched["results"]) == 1
    ref = searched["results"][0]["id"]
    assert ref == "projectvault::chat%3Aone"
    assert searched["access_receipt"]["query_hash"]
    assert "federated owner" not in json.dumps(searched["access_receipt"])

    fetched = fabric.fetch(owner(), ref, purpose="read selected supporting source")
    assert fetched["partition_id"] == "projectvault"
    assert fetched["source_id"] == "chat:one"
    assert "Federated owner information retrieval" in fetched["text"]
    assert fetched["access_receipt"]["action"] == "fetch"


def test_principal_scope_partition_fails_closed_without_required_scope(tmp_path: Path) -> None:
    registry = write_registry(
        tmp_path,
        [
            {
                "partition_id": "mail",
                "title": "Mail",
                "adapter": "mcp_http",
                "endpoint": "https://mail.example.test/mcp",
                "authorization_mode": "principal_scopes",
                "required_scopes": ["mail.read"],
            }
        ],
    )
    cfg = replace(settings(tmp_path), partition_registry_path=registry)
    fabric = AccessFabric(cfg, Database(cfg.database_path))

    result = fabric.search(owner("projects.read"), "invoice", partitions=["mail"])
    assert result["results"] == []
    skipped = result["access_receipt"]["skipped_partitions"]
    assert skipped[0]["partition_id"] == "mail"
    assert skipped[0]["status"] == "DENIED"
    assert "mail.read" in skipped[0]["reason"]


def test_registered_remote_partition_can_be_read_without_reprompt_when_bridge_grant_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = write_registry(
        tmp_path,
        [
            {
                "partition_id": "drive",
                "title": "Drive",
                "adapter": "mcp_http",
                "endpoint": "https://drive.example.test/mcp",
                "authorization_mode": "bridge_authorized",
            }
        ],
    )
    cfg = replace(settings(tmp_path), partition_registry_path=registry)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "jsonrpc": "2.0",
                "id": "x",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "results": [
                                        {
                                            "id": "file-7",
                                            "title": "Research note",
                                            "url": "https://drive.example.test/file-7",
                                        }
                                    ]
                                }
                            ),
                        }
                    ],
                    "isError": False,
                },
            }

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any):
            self.headers: dict[str, str] | None = None

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            assert url == "https://drive.example.test/mcp"
            assert json["method"] == "tools/call"
            assert json["params"]["name"] == "search"
            assert json["params"]["arguments"] == {"query": "research"}
            self.headers = headers
            return FakeResponse()

    monkeypatch.setattr("projectvault.access_fabric.httpx.Client", FakeClient)
    fabric = AccessFabric(cfg, Database(cfg.database_path))
    result = fabric.search(owner("projects.read"), "research", partitions=["drive"])
    assert result["results"][0]["id"] == "drive::file-7"
    assert result["access_receipt"]["accessed_partitions"] == ["drive"]


def test_missing_provider_credential_is_visible_but_secret_name_is_not_exposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PRIVATE_MCP_TOKEN", raising=False)
    registry = write_registry(
        tmp_path,
        [
            {
                "partition_id": "private",
                "title": "Private provider",
                "adapter": "mcp_http",
                "endpoint": "https://private.example.test/mcp",
                "credential_env": "PRIVATE_MCP_TOKEN",
            }
        ],
    )
    cfg = replace(settings(tmp_path), partition_registry_path=registry)
    fabric = AccessFabric(cfg, Database(cfg.database_path))

    listed = fabric.list_partitions(owner())
    private = next(item for item in listed["partitions"] if item["partition_id"] == "private")
    assert private["access_status"] == "UNAVAILABLE"
    assert private["provider_credential_configured"] is False
    assert "PRIVATE_MCP_TOKEN" not in json.dumps(private)


def test_remote_registry_rejects_insecure_non_loopback_http(tmp_path: Path) -> None:
    registry = write_registry(
        tmp_path,
        [
            {
                "partition_id": "unsafe",
                "adapter": "mcp_http",
                "endpoint": "http://example.test/mcp",
            }
        ],
    )
    cfg = replace(settings(tmp_path), partition_registry_path=registry)
    with pytest.raises(ValueError, match="HTTPS or loopback HTTP"):
        load_partition_specs(cfg)


def test_unknown_partition_is_not_silently_guessed(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    fabric = AccessFabric(cfg, Database(cfg.database_path))
    with pytest.raises(KeyError):
        fabric.search(owner(), "anything", partitions=["not-registered"])


def test_explain_access_states_user_authorization_and_provider_boundary(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    fabric = AccessFabric(cfg, Database(cfg.database_path))
    explanation = fabric.explain_access(owner(), "projectvault")
    assert explanation["partitions"][0]["access_status"] == "GRANTED"
    assert "limited to explicitly registered" in explanation["principle"]


def test_mcp_exposes_federated_tools_only_when_partition_registry_is_configured(tmp_path: Path) -> None:
    base = settings(tmp_path)
    assert [item["name"] for item in tools(base)] == [
        "search",
        "fetch",
        "list_projects",
        "list_ingestions",
        "list_unassigned",
    ]

    registry = write_registry(
        tmp_path,
        [
            {
                "partition_id": "drive",
                "adapter": "mcp_http",
                "endpoint": "https://drive.example.test/mcp",
            }
        ],
    )
    configured = replace(base, partition_registry_path=registry)
    names = [item["name"] for item in tools(configured)]
    assert names[-4:] == [
        "list_partitions",
        "search_federated",
        "fetch_federated",
        "explain_access",
    ]
