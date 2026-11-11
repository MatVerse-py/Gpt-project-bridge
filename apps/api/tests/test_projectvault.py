from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import projectvault.app as app_module
from projectvault.app import create_app
from projectvault.config import Settings
from projectvault.db import Database
from projectvault.files import ArchiveLimits, ingest_archive, ingest_directory
from projectvault.ingest import ingest_export
from projectvault.search import KnowledgeService


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


def sample_export(path: Path) -> None:
    payload = [
        {
            "id": "conv-1",
            "title": "Arquitetura observada",
            "create_time": 1000,
            "update_time": 2000,
            "project_id": "project-alpha",
            "project_name": "Alpha",
            "mapping": {
                "a": {"message": {"id": "m1", "author": {"role": "user"}, "create_time": 1100, "content": {"parts": ["Motor Kalman verificável"]}}}
            },
        },
        {
            "id": "conv-2",
            "title": "Sem metadado",
            "create_time": 3000,
            "update_time": 4000,
            "mapping": {
                "b": {"message": {"id": "m2", "author": {"role": "assistant"}, "create_time": 3100, "content": {"parts": ["Conteúdo não atribuído"]}}}
            },
        },
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps(payload, ensure_ascii=False))


def sample_manus_backup(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("tasks/task-1.json", json.dumps({"title": "Terceira Ordem", "body": "Acoplamento humano-máquina verificável"}, ensure_ascii=False))
        archive.writestr("notes/readme.md", "# MatVerse\n\nBridge com proveniência.")
        archive.writestr("assets/diagram.bin", b"\x00\x01\x02")


def test_ingest_search_fetch_and_assignment(tmp_path: Path) -> None:
    export = tmp_path / "export.zip"
    sample_export(export)
    db = Database(tmp_path / "vault.db")
    run = ingest_export(db, export)
    assert run["imported_documents"] == 2
    assert run["assigned_documents"] == 1
    assert run["unassigned_documents"] == 1
    service = KnowledgeService(db, "http://127.0.0.1:8787")
    results = service.search("Kalman verificável")
    assert results["results"][0]["id"] == "chat:conv-1"
    fetched = service.fetch("chat:conv-1")
    assert fetched["metadata"]["project_id"] == "project-alpha"
    assert fetched["metadata"]["attribution_basis"] == "explicit_export_metadata"
    assert db.fetch("chat:conv-2")["project_id"] == "unassigned"


def test_owner_map_fills_missing_metadata(tmp_path: Path) -> None:
    export = tmp_path / "export.zip"
    sample_export(export)
    owner_map = tmp_path / "owner-map.json"
    owner_map.write_text(json.dumps({"conversations": {"conv-2": {"project_id": "project-beta", "project_name": "Beta"}}}), encoding="utf-8")
    db = Database(tmp_path / "vault.db")
    ingest_export(db, export, owner_map)
    row = db.fetch("chat:conv-2")
    assert row["project_id"] == "project-beta"
    assert row["attribution_basis"] == "owner_mapping"


def test_mcp_contract(tmp_path: Path) -> None:
    export = tmp_path / "export.zip"
    sample_export(export)
    cfg = settings(tmp_path)
    ingest_export(Database(cfg.database_path), export)
    client = TestClient(create_app(cfg))

    init = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
    assert init.status_code == 200
    assert init.json()["result"]["capabilities"]["tools"] == {"listChanged": False}

    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert names == ["search", "fetch", "list_projects", "list_ingestions", "list_unassigned"]

    searched = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search", "arguments": {"query": "Kalman"}}})
    envelope = json.loads(searched.json()["result"]["content"][0]["text"])
    assert envelope["results"][0]["id"] == "chat:conv-1"

    fetched = client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "fetch", "arguments": {"id": "chat:conv-1"}}})
    item = json.loads(fetched.json()["result"]["content"][0]["text"])
    assert "Motor Kalman verificável" in item["text"]

    ingestions = client.post("/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "list_ingestions", "arguments": {"limit": 1}}})
    ingestion_result = json.loads(ingestions.json()["result"]["content"][0]["text"])
    assert ingestion_result["ingestions"][0]["source_name"] == "export.zip"

    unassigned = client.post("/mcp", json={"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "list_unassigned", "arguments": {"limit": 1}}})
    unassigned_result = json.loads(unassigned.json()["result"]["content"][0]["text"])
    assert unassigned_result["documents"][0]["document_id"] == "chat:conv-2"


def test_origin_guard(tmp_path: Path) -> None:
    client = TestClient(create_app(settings(tmp_path)))
    denied = client.get("/health", headers={"Origin": "https://evil.example"})
    assert denied.status_code == 403


def test_ingest_directory_with_explicit_owner_assignment(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "architecture.md").write_text("Arquitetura canônica do sistema", encoding="utf-8")
    db = Database(tmp_path / "vault.db")
    run = ingest_directory(db, source, "canonical", "Canônico")
    assert run["imported_documents"] == 1
    results = KnowledgeService(db, "http://127.0.0.1:8787").search("Arquitetura canônica")
    fetched = KnowledgeService(db, "http://127.0.0.1:8787").fetch(results["results"][0]["id"])
    assert fetched["metadata"]["project_id"] == "canonical"
    assert fetched["metadata"]["attribution_basis"] == "owner_supplied_directory"


def test_static_auth_blocks_missing_token(tmp_path: Path) -> None:
    cfg = Settings(
        database_path=tmp_path / "vault.db",
        host="0.0.0.0",
        port=8787,
        public_base_url="http://127.0.0.1:8787",
        auth_mode="static",
        static_token="correct-token",
        oidc_issuer=None,
        oidc_audience=None,
        oidc_jwks_url=None,
        required_scope="projects.read",
        allowed_origins=("https://chatgpt.com",),
        max_results=20,
    )
    client = TestClient(create_app(cfg))
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
    allowed = client.post("/mcp", headers={"Authorization": "Bearer correct-token"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert allowed.status_code == 200


def test_oidc_mode_exposes_tools_then_returns_auth_challenge(tmp_path: Path) -> None:
    cfg = Settings(
        database_path=tmp_path / "vault.db",
        host="0.0.0.0",
        port=8787,
        public_base_url="https://vault.example.com",
        auth_mode="oidc",
        static_token=None,
        oidc_issuer="https://idp.example.com/",
        oidc_audience="https://vault.example.com",
        oidc_jwks_url="https://idp.example.com/.well-known/jwks.json",
        required_scope="projects.read",
        allowed_origins=("https://chatgpt.com",),
        max_results=20,
    )
    client = TestClient(create_app(cfg))
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert listed.status_code == 200
    schemes = listed.json()["result"]["tools"][0]["securitySchemes"]
    assert schemes == [{"type": "oauth2", "scopes": ["projects.read"]}]
    called = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search", "arguments": {"query": "x"}}})
    result = called.json()["result"]
    assert result["isError"] is True
    assert "mcp/www_authenticate" in result["_meta"]
    metadata = client.get("/.well-known/oauth-protected-resource").json()
    assert metadata["authorization_servers"] == ["https://idp.example.com/"]


def test_dashboard_api_and_browser_ingest(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    client = TestClient(create_app(cfg))
    export = tmp_path / "export.zip"
    sample_export(export)

    before = client.get("/api/stats")
    assert before.status_code == 200
    assert before.json()["stats"]["conversations"] == 0

    with export.open("rb") as handle:
        ingested = client.post(
            "/api/ingest/export",
            files={"export": ("export.zip", handle, "application/zip")},
        )
    assert ingested.status_code == 200, ingested.text
    assert ingested.json()["run"]["imported_documents"] == 2

    stats = client.get("/api/stats").json()["stats"]
    assert stats["projects"] == 1
    assert stats["conversations"] == 2
    assert stats["messages"] == 2
    assert stats["files"] == 0
    assert stats["unassigned"] == 1

    runs = client.get("/api/ingestions").json()["ingestions"]
    assert runs[0]["source_name"] == "export.zip"
    unassigned = client.get("/api/unassigned").json()["documents"]
    assert unassigned[0]["document_id"] == "chat:conv-2"


def test_browser_project_file_ingest_and_stats(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    client = TestClient(create_app(cfg))
    archive = tmp_path / "files.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docs/architecture.md", "Canonical bridge architecture")

    with archive.open("rb") as handle:
        response = client.post(
            "/api/ingest/project-files",
            data={"project_id": "bridge", "project_name": "GPT Project Bridge"},
            files={"archive": ("files.zip", handle, "application/zip")},
        )
    assert response.status_code == 200, response.text
    stats = response.json()["stats"]
    assert stats["files"] == 1
    assert stats["projects"] == 1


def test_ingest_manus_backup_preserves_unassigned_provenance(tmp_path: Path) -> None:
    backup = tmp_path / "tasks-data-matverse.manustask"
    sample_manus_backup(backup)
    db = Database(tmp_path / "vault.db")
    staging = tmp_path / "large-upload-staging"
    run = ingest_archive(
        db,
        backup,
        source_type="manus_task_backup",
        limits=ArchiveLimits(
            max_files=10,
            max_expanded_bytes=1024 * 1024,
            max_member_bytes=1024 * 1024,
            max_indexable_file_bytes=1024 * 1024,
            min_free_bytes=1,
        ),
        staging_dir=staging,
    )
    assert run["imported_documents"] == 2
    assert run["assigned_documents"] == 0
    assert run["unassigned_documents"] == 2
    assert run["metadata"]["source_type"] == "manus_task_backup"
    assert run["metadata"]["member_count"] == 3
    assert len(run["metadata"]["member_manifest_sha256"]) == 64
    assert staging.is_dir()
    result = KnowledgeService(db, "http://127.0.0.1:8787").search("acoplamento humano máquina")
    item = KnowledgeService(db, "http://127.0.0.1:8787").fetch(result["results"][0]["id"])
    assert item["metadata"]["source_type"] == "manus_task_backup"
    assert item["metadata"]["project_id"] == "unassigned"


def test_default_upload_capacity_covers_current_multi_gigabyte_manus_exports(tmp_path: Path) -> None:
    assert settings(tmp_path).max_upload_bytes >= 4_810_000_000


def test_archive_upload_preflight_rejects_oversized_declared_body(tmp_path: Path) -> None:
    client = TestClient(create_app(replace(settings(tmp_path), max_upload_bytes=1)))
    response = client.post(
        "/api/ingest/manus-backup",
        content=b"x",
        headers={"content-length": str(2 * 1024 * 1024 + 2)},
    )
    assert response.status_code == 413


def test_archive_upload_preflight_reserves_staging_space(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(settings(tmp_path)))
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    response = client.post(
        "/api/ingest/manus-backup",
        content=b"x",
        headers={"content-length": "1"},
    )
    assert response.status_code == 507


def test_manus_backup_api_requires_completed_container_and_optional_full_assignment(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    client = TestClient(create_app(cfg))
    backup = tmp_path / "tasks-data-matverse.manustask"
    sample_manus_backup(backup)

    with backup.open("rb") as handle:
        response = client.post(
            "/api/ingest/manus-backup",
            data={"project_id": "matverse", "project_name": "MatVerse"},
            files={"backup": (backup.name, handle, "application/zip")},
        )
    assert response.status_code == 200, response.text
    run = response.json()["run"]
    assert run["imported_documents"] == 2
    assert run["assigned_documents"] == 2
    assert run["metadata"]["source_type"] == "manus_task_backup"
    assert response.json()["stats"]["files"] == 2

    invalid = client.post(
        "/api/ingest/manus-backup",
        files={"backup": ("not-manus.zip", b"not-a-backup", "application/zip")},
    )
    assert invalid.status_code == 422


def test_archive_rejects_unsafe_member_path_before_ingest(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.manustask"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../outside.md", "must not escape")
    db = Database(tmp_path / "vault.db")
    try:
        ingest_archive(
            db,
            archive,
            source_type="manus_task_backup",
            limits=ArchiveLimits(10, 1024 * 1024, 1024 * 1024, 1024 * 1024, 1),
        )
    except ValueError as exc:
        assert str(exc) == "unsafe_archive_path"
    else:
        raise AssertionError("unsafe archive path must fail closed")


def test_hybrid_auth_accepts_internal_static_token(tmp_path: Path) -> None:
    cfg = Settings(
        database_path=tmp_path / "vault.db",
        host="0.0.0.0",
        port=8787,
        public_base_url="https://vault.example.com",
        auth_mode="hybrid",
        static_token="internal-web-token",
        oidc_issuer="https://idp.example.com/",
        oidc_audience="https://vault.example.com",
        oidc_jwks_url="https://idp.example.com/.well-known/jwks.json",
        required_scope="projects.read",
        allowed_origins=("https://chatgpt.com",),
        max_results=20,
    )
    client = TestClient(create_app(cfg))
    denied = client.get("/api/stats")
    assert denied.status_code == 401
    allowed = client.get("/api/stats", headers={"Authorization": "Bearer internal-web-token"})
    assert allowed.status_code == 200
    listed = client.post(
        "/mcp",
        headers={"Authorization": "Bearer internal-web-token"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200
    assert listed.json()["result"]["tools"][0]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["projects.read"]}
    ]
