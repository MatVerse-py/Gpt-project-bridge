from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from projectvault.access_fabric import AccessFabric, load_partition_specs
from projectvault.auth import Principal
from projectvault.config import Settings
from projectvault.db import Database


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


def owner() -> Principal:
    return Principal("owner-test", frozenset({"projects.read"}), {})


def registry(tmp_path: Path, partitions: list[dict[str, Any]]) -> Path:
    path = tmp_path / "partitions.json"
    path.write_text(json.dumps({"protocol": "matverse.owner-access-fabric.v1", "partitions": partitions}), encoding="utf-8")
    return path


class FakeResponse:
    def __init__(self, *, payload: Any = None, content: bytes | None = None, status_code: int = 200):
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload or {}).encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_{self.status_code}")

    def json(self) -> Any:
        return self._payload


class ProviderFakeClient:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    def __enter__(self) -> "ProviderFakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def get(self, url: str, *, headers: dict[str, str], params: dict[str, Any]) -> FakeResponse:
        self.calls.append(("GET", url, {"headers": headers, "params": params}))
        if url == "https://api.github.com/search/code":
            return FakeResponse(payload={"items": [{"path": "README.md", "sha": "abc", "html_url": "https://github.com/acme/repo/blob/main/README.md", "repository": {"full_name": "acme/repo"}}]})
        if url == "https://api.github.com/repos/acme/repo/contents/README.md":
            content = base64.b64encode(b"Bridge architecture").decode("ascii")
            return FakeResponse(payload={"content": content, "encoding": "base64", "html_url": "https://github.com/acme/repo/blob/main/README.md", "sha": "abc"})
        if url == "https://www.googleapis.com/drive/v3/files":
            return FakeResponse(payload={"files": [{"id": "drive-1", "name": "Patent note", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "2026-09-01T00:00:00Z", "webViewLink": "https://drive.google.com/file/d/drive-1"}]})
        if url == "https://www.googleapis.com/drive/v3/files/drive-1":
            return FakeResponse(payload={"id": "drive-1", "name": "Patent note", "mimeType": "application/vnd.google-apps.document", "webViewLink": "https://drive.google.com/file/d/drive-1"})
        if url == "https://www.googleapis.com/drive/v3/files/drive-1/export":
            return FakeResponse(content=b"Patent family GCCI")
        if url.endswith("/gmail/v1/users/me/messages"):
            return FakeResponse(payload={"messages": [{"id": "mail-1", "threadId": "thread-1"}]})
        if url.endswith("/gmail/v1/users/me/messages/mail-1"):
            if params.get("format") == "metadata":
                return FakeResponse(payload={"id": "mail-1", "threadId": "thread-1", "payload": {"headers": [{"name": "Subject", "value": "Bridge mail"}, {"name": "From", "value": "alice@example.test"}]}})
            body = base64.urlsafe_b64encode(b"mail body about Bridge").decode("ascii").rstrip("=")
            return FakeResponse(payload={"id": "mail-1", "threadId": "thread-1", "payload": {"mimeType": "text/plain", "headers": [{"name": "Subject", "value": "Bridge mail"}], "body": {"data": body}}})
        if url == "https://www.googleapis.com/calendar/v3/calendars/primary/events":
            return FakeResponse(payload={"items": [{"id": "evt-1", "summary": "MatVerse review", "htmlLink": "https://calendar.google.com/event?eid=1", "start": {"dateTime": "2026-09-07T12:00:00Z"}, "status": "confirmed"}]})
        if url == "https://www.googleapis.com/calendar/v3/calendars/primary/events/evt-1":
            return FakeResponse(payload={"id": "evt-1", "summary": "MatVerse review", "description": "Review Bridge", "start": {"dateTime": "2026-09-07T12:00:00Z"}, "end": {"dateTime": "2026-09-07T13:00:00Z"}, "htmlLink": "https://calendar.google.com/event?eid=1"})
        if url == "https://api.notion.com/v1/pages/page-1":
            return FakeResponse(payload={"id": "page-1", "url": "https://notion.so/page-1", "properties": {"Name": {"type": "title", "title": [{"plain_text": "Bridge notebook"}]}}})
        if url == "https://api.notion.com/v1/blocks/page-1/children":
            return FakeResponse(payload={"results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Notion Bridge content"}]}}], "has_more": False})
        raise AssertionError(f"unexpected GET {url} params={params}")

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
        self.calls.append(("POST", url, {"headers": headers, "json": json}))
        if url == "https://api.notion.com/v1/search":
            return FakeResponse(payload={"results": [{"id": "page-1", "object": "page", "url": "https://notion.so/page-1", "properties": {"Name": {"type": "title", "title": [{"plain_text": "Bridge notebook"}]}}}]})
        raise AssertionError(f"unexpected POST {url}")


def native_partition(adapter: str, credential_env: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"partition_id": adapter.replace("google_", "g-"), "title": adapter, "adapter": adapter, "credential_env": credential_env}
    item.update(extra)
    return item


def configure_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AccessFabric:
    partitions = [
        native_partition("github_rest", "GITHUB_TOKEN_TEST", options={"repository": "acme/repo", "kind": "code"}),
        native_partition("google_drive", "GOOGLE_TOKEN_TEST"),
        native_partition("gmail", "GOOGLE_TOKEN_TEST"),
        native_partition("google_calendar", "GOOGLE_TOKEN_TEST", options={"calendar_id": "primary", "past_days": 30, "future_days": 30}),
        native_partition("notion", "NOTION_TOKEN_TEST"),
    ]
    for env in ("GITHUB_TOKEN_TEST", "GOOGLE_TOKEN_TEST", "NOTION_TOKEN_TEST"):
        monkeypatch.setenv(env, "token-value")
    monkeypatch.setattr("projectvault.provider_adapters.httpx.Client", ProviderFakeClient)
    path = registry(tmp_path, partitions)
    cfg = replace(settings(tmp_path), partition_registry_path=path)
    return AccessFabric(cfg, Database(cfg.database_path))


def test_native_registry_accepts_official_adapters_and_never_exposes_secret_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fabric = configure_all(tmp_path, monkeypatch)
    listed = fabric.list_partitions(owner())
    adapters = {item["adapter"] for item in listed["partitions"]}
    assert {"github_rest", "google_drive", "gmail", "google_calendar", "notion"}.issubset(adapters)
    serialized = json.dumps(listed)
    assert "GITHUB_TOKEN_TEST" not in serialized
    assert "GOOGLE_TOKEN_TEST" not in serialized
    assert "NOTION_TOKEN_TEST" not in serialized


def test_native_adapter_rejects_custom_endpoint_and_secret_like_options(tmp_path: Path) -> None:
    bad_endpoint = registry(tmp_path, [{"partition_id": "drive", "adapter": "google_drive", "credential_env": "TOKEN", "endpoint": "https://evil.example"}])
    with pytest.raises(ValueError, match="does not accept custom endpoint"):
        load_partition_specs(replace(settings(tmp_path), partition_registry_path=bad_endpoint))

    bad_option = registry(tmp_path, [{"partition_id": "drive", "adapter": "google_drive", "credential_env": "TOKEN", "options": {"api_key": "x"}}])
    with pytest.raises(ValueError, match="Secret-like option"):
        load_partition_specs(replace(settings(tmp_path), partition_registry_path=bad_option))


def test_github_native_search_and_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fabric = configure_all(tmp_path, monkeypatch)
    result = fabric.search(owner(), "Bridge", partitions=["github_rest"])
    assert result["results"][0]["title"] == "acme/repo — README.md"
    fetched = fabric.fetch(owner(), result["results"][0]["id"])
    assert fetched["text"] == "Bridge architecture"
    assert fetched["metadata"]["provider"] == "github"


def test_drive_native_search_and_export_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fabric = configure_all(tmp_path, monkeypatch)
    result = fabric.search(owner(), "Patent", partitions=["g-drive"])
    assert result["results"][0]["source_id"] == "drive-1"
    fetched = fabric.fetch(owner(), result["results"][0]["id"])
    assert fetched["text"] == "Patent family GCCI"
    assert fetched["metadata"]["content_status"] == "TEXT_AVAILABLE"


def test_gmail_native_search_and_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fabric = configure_all(tmp_path, monkeypatch)
    result = fabric.search(owner(), "Bridge", partitions=["gmail"])
    assert result["results"][0]["title"] == "Bridge mail"
    fetched = fabric.fetch(owner(), result["results"][0]["id"])
    assert "mail body about Bridge" in fetched["text"]


def test_calendar_native_search_and_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fabric = configure_all(tmp_path, monkeypatch)
    result = fabric.search(owner(), "MatVerse", partitions=["g-calendar"])
    assert result["results"][0]["title"] == "MatVerse review"
    fetched = fabric.fetch(owner(), result["results"][0]["id"])
    assert "Review Bridge" in fetched["text"]


def test_notion_native_search_and_fetch_uses_current_version_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ProviderFakeClient.calls.clear()
    fabric = configure_all(tmp_path, monkeypatch)
    result = fabric.search(owner(), "Bridge", partitions=["notion"])
    fetched = fabric.fetch(owner(), result["results"][0]["id"])
    assert fetched["title"] == "Bridge notebook"
    assert "Notion Bridge content" in fetched["text"]
    notion_calls = [entry for entry in ProviderFakeClient.calls if entry[1].startswith("https://api.notion.com/")]
    assert notion_calls
    assert all(call[2]["headers"]["notion-version"] == "2026-03-11" for call in notion_calls)
