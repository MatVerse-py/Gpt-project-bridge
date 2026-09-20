from __future__ import annotations

import base64
import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote

import httpx

NATIVE_ADAPTERS = frozenset({
    "github_rest",
    "google_drive",
    "gmail",
    "google_calendar",
    "notion",
})

GITHUB_API_VERSION = "2026-03-10"
NOTION_API_VERSION = "2026-03-11"


class NativeProviderUnavailable(RuntimeError):
    pass


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise NativeProviderUnavailable(f"provider_request_failed:{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise NativeProviderUnavailable("provider_invalid_json")
    return payload


def _client_get(
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, Any] | None,
    timeout: float,
) -> httpx.Response:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            return client.get(url, headers=dict(headers), params=dict(params or {}))
    except Exception as exc:
        raise NativeProviderUnavailable(f"provider_request_failed:{type(exc).__name__}") from exc


def _client_post(
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout: float,
) -> httpx.Response:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            return client.post(url, headers=dict(headers), json=dict(payload))
    except Exception as exc:
        raise NativeProviderUnavailable(f"provider_request_failed:{type(exc).__name__}") from exc


def _pack_json_id(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unpack_json_id(value: str) -> dict[str, Any]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise NativeProviderUnavailable("invalid_provider_source_id") from exc
    if not isinstance(payload, dict):
        raise NativeProviderUnavailable("invalid_provider_source_id")
    return payload


def _safe_option_str(options: Mapping[str, Any], name: str, default: str) -> str:
    value = options.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise NativeProviderUnavailable(f"invalid_option:{name}")
    return value.strip()


def _safe_option_int(options: Mapping[str, Any], name: str, default: int, maximum: int) -> int:
    value = options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise NativeProviderUnavailable(f"invalid_option:{name}")
    return value


def _headers_from_gmail_message(payload: Mapping[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    body = payload.get("payload")
    if not isinstance(body, Mapping):
        return headers
    raw_headers = body.get("headers")
    if not isinstance(raw_headers, list):
        return headers
    for item in raw_headers:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        if name and value and name not in headers:
            headers[name] = value
    return headers


def _decode_gmail_data(data: str) -> str:
    try:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _gmail_body(part: Mapping[str, Any]) -> str:
    mime = str(part.get("mimeType") or "")
    body = part.get("body")
    if isinstance(body, Mapping) and isinstance(body.get("data"), str):
        text = _decode_gmail_data(str(body["data"]))
        if mime == "text/html":
            text = _strip_html(text)
        if text.strip():
            return text.strip()
    parts = part.get("parts")
    if isinstance(parts, list):
        plain: list[str] = []
        html_parts: list[str] = []
        for child in parts:
            if not isinstance(child, Mapping):
                continue
            text = _gmail_body(child)
            if not text:
                continue
            if str(child.get("mimeType") or "") == "text/html":
                html_parts.append(text)
            else:
                plain.append(text)
        return "\n\n".join(plain or html_parts)
    return ""


def _notion_rich_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        text = item.get("plain_text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _notion_page_title(page: Mapping[str, Any]) -> str:
    props = page.get("properties")
    if isinstance(props, Mapping):
        for prop in props.values():
            if isinstance(prop, Mapping) and prop.get("type") == "title":
                title = _notion_rich_text(prop.get("title"))
                if title:
                    return title
    return str(page.get("id") or "Untitled Notion page")


def _notion_block_text(block: Mapping[str, Any]) -> str:
    block_type = str(block.get("type") or "")
    data = block.get(block_type)
    if not isinstance(data, Mapping):
        return ""
    rich = _notion_rich_text(data.get("rich_text"))
    if rich:
        return rich
    if block_type == "child_page":
        title = data.get("title")
        return str(title) if title else ""
    return ""


def _google_token_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}", "accept": "application/json"}


def _github_headers(token: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token}",
        "accept": "application/vnd.github+json",
        "x-github-api-version": GITHUB_API_VERSION,
        "user-agent": "gpt-project-bridge",
    }


def _notion_headers(token: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token}",
        "accept": "application/json",
        "content-type": "application/json",
        "notion-version": NOTION_API_VERSION,
    }


def validate_native_options(adapter: str, options: Mapping[str, Any]) -> None:
    if adapter not in NATIVE_ADAPTERS:
        raise ValueError(f"Unsupported native adapter: {adapter}")
    forbidden = re.compile(r"token|secret|password|credential|api[_-]?key", re.IGNORECASE)
    for key in options:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Partition options keys must be non-empty strings")
        if forbidden.search(key):
            raise ValueError(f"Secret-like option is forbidden: {key}")
    if adapter == "github_rest":
        repository = options.get("repository")
        if repository is not None and (
            not isinstance(repository, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository.strip())
        ):
            raise ValueError("github_rest option repository must be 'owner/repo'")
        kind = options.get("kind", "code")
        if kind not in {"code", "issues"}:
            raise ValueError("github_rest option kind must be code or issues")
    elif adapter == "google_calendar":
        for field, maximum in (("past_days", 3650), ("future_days", 3650)):
            if field in options:
                value = options[field]
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                    raise ValueError(f"google_calendar option {field} must be between 0 and {maximum}")


def native_search(
    adapter: str,
    *,
    token: str,
    query: str,
    limit: int,
    options: Mapping[str, Any],
    timeout: float,
) -> dict[str, Any]:
    if adapter == "github_rest":
        return _github_search(token, query, limit, options, timeout)
    if adapter == "google_drive":
        return _drive_search(token, query, limit, options, timeout)
    if adapter == "gmail":
        return _gmail_search(token, query, limit, options, timeout)
    if adapter == "google_calendar":
        return _calendar_search(token, query, limit, options, timeout)
    if adapter == "notion":
        return _notion_search(token, query, limit, options, timeout)
    raise NativeProviderUnavailable("unsupported_native_adapter")


def native_fetch(
    adapter: str,
    *,
    token: str,
    item_id: str,
    options: Mapping[str, Any],
    timeout: float,
    max_bytes: int,
) -> dict[str, Any]:
    if adapter == "github_rest":
        return _github_fetch(token, item_id, options, timeout, max_bytes)
    if adapter == "google_drive":
        return _drive_fetch(token, item_id, options, timeout, max_bytes)
    if adapter == "gmail":
        return _gmail_fetch(token, item_id, options, timeout)
    if adapter == "google_calendar":
        return _calendar_fetch(token, item_id, options, timeout)
    if adapter == "notion":
        return _notion_fetch(token, item_id, options, timeout)
    raise NativeProviderUnavailable("unsupported_native_adapter")


def _github_search(token: str, query: str, limit: int, options: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    kind = _safe_option_str(options, "kind", "code")
    repository = options.get("repository")
    q = query
    if isinstance(repository, str) and repository.strip():
        q = f"{q} repo:{repository.strip()}"
    url = f"https://api.github.com/search/{'code' if kind == 'code' else 'issues'}"
    payload = _response_json(_client_get(url, headers=_github_headers(token), params={"q": q, "per_page": min(limit, 100)}, timeout=timeout))
    items = payload.get("items")
    if not isinstance(items, list):
        raise NativeProviderUnavailable("provider_search_result_missing_items")
    results: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, Mapping):
            continue
        repo = item.get("repository")
        repo_name = repo.get("full_name") if isinstance(repo, Mapping) else None
        if kind == "code":
            path = str(item.get("path") or "").strip()
            if not repo_name or not path:
                continue
            source_id = _pack_json_id({"kind": "code", "repository": str(repo_name), "path": path})
            results.append({
                "id": source_id,
                "title": f"{repo_name} — {path}",
                "url": item.get("html_url"),
                "metadata": {"provider": "github", "kind": "code", "repository": repo_name, "path": path, "sha": item.get("sha")},
            })
        else:
            number = item.get("number")
            repo_url = str(item.get("repository_url") or "")
            match = re.search(r"/repos/([^/]+/[^/]+)$", repo_url)
            repo_name = str(repo_name or (match.group(1) if match else ""))
            if not repo_name or not isinstance(number, int):
                continue
            source_id = _pack_json_id({"kind": "issues", "repository": repo_name, "number": number})
            results.append({
                "id": source_id,
                "title": f"{repo_name} #{number} — {str(item.get('title') or '').strip()}",
                "url": item.get("html_url"),
                "metadata": {"provider": "github", "kind": "issues", "repository": repo_name, "number": number, "state": item.get("state")},
            })
    return {"results": results}


def _github_fetch(token: str, item_id: str, options: Mapping[str, Any], timeout: float, max_bytes: int) -> dict[str, Any]:
    decoded = _unpack_json_id(item_id)
    kind = decoded.get("kind")
    repo = str(decoded.get("repository") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise NativeProviderUnavailable("invalid_github_repository")
    if kind == "code":
        path = str(decoded.get("path") or "")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise NativeProviderUnavailable("invalid_github_path")
        url = f"https://api.github.com/repos/{repo}/contents/{quote(path, safe='/')}"
        payload = _response_json(_client_get(url, headers=_github_headers(token), params=None, timeout=timeout))
        content = payload.get("content")
        encoding = payload.get("encoding")
        text = ""
        if isinstance(content, str) and encoding == "base64":
            raw = base64.b64decode(content.encode("ascii"), validate=False)
            if len(raw) > max_bytes:
                raise NativeProviderUnavailable("provider_content_too_large")
            text = raw.decode("utf-8", errors="replace")
        return {
            "title": f"{repo} — {path}",
            "text": text,
            "url": payload.get("html_url"),
            "metadata": {"provider": "github", "kind": "code", "repository": repo, "path": path, "sha": payload.get("sha")},
        }
    if kind == "issues":
        number = decoded.get("number")
        if not isinstance(number, int) or number <= 0:
            raise NativeProviderUnavailable("invalid_github_issue_number")
        url = f"https://api.github.com/repos/{repo}/issues/{number}"
        payload = _response_json(_client_get(url, headers=_github_headers(token), params=None, timeout=timeout))
        title = str(payload.get("title") or f"Issue #{number}")
        body = str(payload.get("body") or "")
        return {
            "title": f"{repo} #{number} — {title}",
            "text": body,
            "url": payload.get("html_url"),
            "metadata": {"provider": "github", "kind": "issues", "repository": repo, "number": number, "state": payload.get("state")},
        }
    raise NativeProviderUnavailable("invalid_github_source_kind")


def _drive_query(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    return f"trashed = false and (name contains '{escaped}' or fullText contains '{escaped}')"


def _drive_search(token: str, query: str, limit: int, options: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    params: dict[str, Any] = {
        "q": _drive_query(query),
        "pageSize": min(limit, 1000),
        "spaces": "drive",
        "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size,description,md5Checksum),nextPageToken",
    }
    drive_id = options.get("drive_id")
    if isinstance(drive_id, str) and drive_id.strip():
        params.update({"corpora": "drive", "driveId": drive_id.strip(), "includeItemsFromAllDrives": "true", "supportsAllDrives": "true"})
    payload = _response_json(_client_get("https://www.googleapis.com/drive/v3/files", headers=_google_token_headers(token), params=params, timeout=timeout))
    files = payload.get("files")
    if not isinstance(files, list):
        raise NativeProviderUnavailable("provider_search_result_missing_files")
    results = []
    for item in files[:limit]:
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        results.append({
            "id": str(item["id"]),
            "title": str(item.get("name") or item["id"]),
            "url": item.get("webViewLink"),
            "metadata": {"provider": "google_drive", "mime_type": item.get("mimeType"), "modified_time": item.get("modifiedTime"), "size": item.get("size"), "md5": item.get("md5Checksum")},
        })
    return {"results": results}


def _drive_fetch(token: str, item_id: str, options: Mapping[str, Any], timeout: float, max_bytes: int) -> dict[str, Any]:
    metadata = _response_json(_client_get(
        f"https://www.googleapis.com/drive/v3/files/{quote(item_id, safe='')}",
        headers=_google_token_headers(token),
        params={"fields": "id,name,mimeType,modifiedTime,webViewLink,size,description,md5Checksum", "supportsAllDrives": "true"},
        timeout=timeout,
    ))
    mime = str(metadata.get("mimeType") or "")
    text = ""
    export_mime: str | None = None
    if mime == "application/vnd.google-apps.document":
        export_mime = "text/plain"
    elif mime == "application/vnd.google-apps.spreadsheet":
        export_mime = "text/csv"
    elif mime.startswith("text/") or mime in {"application/json", "application/xml", "application/javascript", "application/x-yaml"}:
        response = _client_get(
            f"https://www.googleapis.com/drive/v3/files/{quote(item_id, safe='')}",
            headers={"authorization": f"Bearer {token}"},
            params={"alt": "media", "supportsAllDrives": "true"},
            timeout=timeout,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            raise NativeProviderUnavailable(f"provider_request_failed:{type(exc).__name__}") from exc
        if len(response.content) > max_bytes:
            raise NativeProviderUnavailable("provider_content_too_large")
        text = response.content.decode("utf-8", errors="replace")
    if export_mime:
        response = _client_get(
            f"https://www.googleapis.com/drive/v3/files/{quote(item_id, safe='')}/export",
            headers={"authorization": f"Bearer {token}"},
            params={"mimeType": export_mime},
            timeout=timeout,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            raise NativeProviderUnavailable(f"provider_request_failed:{type(exc).__name__}") from exc
        if len(response.content) > max_bytes:
            raise NativeProviderUnavailable("provider_content_too_large")
        text = response.content.decode("utf-8", errors="replace")
    if not text and metadata.get("description"):
        text = str(metadata["description"])
    return {
        "title": str(metadata.get("name") or item_id),
        "text": text,
        "url": metadata.get("webViewLink"),
        "metadata": {"provider": "google_drive", "mime_type": mime, "modified_time": metadata.get("modifiedTime"), "size": metadata.get("size"), "md5": metadata.get("md5Checksum"), "content_status": "TEXT_AVAILABLE" if text else "METADATA_ONLY"},
    }


def _gmail_url(user_id: str, message_id: str) -> str:
    return f"https://gmail.googleapis.com/gmail/v1/users/{quote(user_id, safe='')}/messages/{quote(message_id, safe='')}"


def _gmail_search(token: str, query: str, limit: int, options: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    user_id = _safe_option_str(options, "user_id", "me")
    headers = _google_token_headers(token)
    payload = _response_json(_client_get(
        f"https://gmail.googleapis.com/gmail/v1/users/{quote(user_id, safe='')}/messages",
        headers=headers,
        params={"q": query, "maxResults": min(limit, 500)},
        timeout=timeout,
    ))
    messages = payload.get("messages")
    if messages is None:
        return {"results": []}
    if not isinstance(messages, list):
        raise NativeProviderUnavailable("provider_search_result_missing_messages")
    results = []
    for item in messages[:limit]:
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        message_id = str(item["id"])
        meta = _response_json(_client_get(
            _gmail_url(user_id, message_id),
            headers=headers,
            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
            timeout=timeout,
        ))
        mh = _headers_from_gmail_message(meta)
        subject = mh.get("subject") or f"Gmail message {message_id}"
        results.append({
            "id": message_id,
            "title": subject,
            "metadata": {"provider": "gmail", "thread_id": meta.get("threadId"), "from": mh.get("from"), "date": mh.get("date")},
        })
    return {"results": results}


def _gmail_fetch(token: str, item_id: str, options: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    user_id = _safe_option_str(options, "user_id", "me")
    payload = _response_json(_client_get(
        _gmail_url(user_id, item_id),
        headers=_google_token_headers(token),
        params={"format": "full"},
        timeout=timeout,
    ))
    mh = _headers_from_gmail_message(payload)
    body = payload.get("payload")
    text = _gmail_body(body) if isinstance(body, Mapping) else ""
    if not text:
        text = str(payload.get("snippet") or "")
    return {
        "title": mh.get("subject") or f"Gmail message {item_id}",
        "text": text,
        "metadata": {"provider": "gmail", "thread_id": payload.get("threadId"), "from": mh.get("from"), "to": mh.get("to"), "date": mh.get("date"), "labels": payload.get("labelIds")},
    }


def _calendar_search(token: str, query: str, limit: int, options: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    calendar_id = _safe_option_str(options, "calendar_id", "primary")
    past_days = _safe_option_int(options, "past_days", 365, 3650)
    future_days = _safe_option_int(options, "future_days", 365, 3650)
    now = datetime.now(timezone.utc)
    params = {
        "q": query,
        "timeMin": (now - timedelta(days=past_days)).isoformat().replace("+00:00", "Z"),
        "timeMax": (now + timedelta(days=future_days)).isoformat().replace("+00:00", "Z"),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": min(limit, 2500),
    }
    payload = _response_json(_client_get(
        f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events",
        headers=_google_token_headers(token),
        params=params,
        timeout=timeout,
    ))
    items = payload.get("items")
    if items is None:
        return {"results": []}
    if not isinstance(items, list):
        raise NativeProviderUnavailable("provider_search_result_missing_events")
    results = []
    for item in items[:limit]:
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        start = item.get("start")
        when = None
        if isinstance(start, Mapping):
            when = start.get("dateTime") or start.get("date")
        results.append({
            "id": str(item["id"]),
            "title": str(item.get("summary") or "Untitled calendar event"),
            "url": item.get("htmlLink"),
            "metadata": {"provider": "google_calendar", "start": when, "status": item.get("status"), "location": item.get("location")},
        })
    return {"results": results}


def _calendar_fetch(token: str, item_id: str, options: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    calendar_id = _safe_option_str(options, "calendar_id", "primary")
    payload = _response_json(_client_get(
        f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events/{quote(item_id, safe='')}",
        headers=_google_token_headers(token),
        params=None,
        timeout=timeout,
    ))
    lines = [str(payload.get("summary") or "")]
    for key in ("description", "location"):
        if payload.get(key):
            lines.append(str(payload[key]))
    attendees = payload.get("attendees")
    if isinstance(attendees, list):
        names = []
        for person in attendees:
            if isinstance(person, Mapping):
                value = person.get("displayName") or person.get("email")
                if value:
                    names.append(str(value))
        if names:
            lines.append("Attendees: " + ", ".join(names))
    return {
        "title": str(payload.get("summary") or "Untitled calendar event"),
        "text": "\n\n".join(line for line in lines if line),
        "url": payload.get("htmlLink"),
        "metadata": {"provider": "google_calendar", "start": payload.get("start"), "end": payload.get("end"), "status": payload.get("status"), "organizer": payload.get("organizer")},
    }


def _notion_search(token: str, query: str, limit: int, options: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    payload = _response_json(_client_post(
        "https://api.notion.com/v1/search",
        headers=_notion_headers(token),
        payload={"query": query, "page_size": min(limit, 100), "filter": {"property": "object", "value": "page"}},
        timeout=timeout,
    ))
    items = payload.get("results")
    if not isinstance(items, list):
        raise NativeProviderUnavailable("provider_search_result_missing_results")
    results = []
    for page in items[:limit]:
        if not isinstance(page, Mapping) or not page.get("id"):
            continue
        results.append({
            "id": str(page["id"]),
            "title": _notion_page_title(page),
            "url": page.get("url"),
            "metadata": {"provider": "notion", "last_edited_time": page.get("last_edited_time"), "archived": page.get("archived")},
        })
    return {"results": results}


def _notion_fetch(token: str, item_id: str, options: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    headers = _notion_headers(token)
    page = _response_json(_client_get(
        f"https://api.notion.com/v1/pages/{quote(item_id, safe='')}",
        headers=headers,
        params=None,
        timeout=timeout,
    ))
    max_blocks = _safe_option_int(options, "max_blocks", 200, 1000)
    results: list[Mapping[str, Any]] = []
    cursor: str | None = None
    while len(results) < max_blocks:
        params: dict[str, Any] = {"page_size": min(100, max_blocks - len(results))}
        if cursor:
            params["start_cursor"] = cursor
        child = _response_json(_client_get(
            f"https://api.notion.com/v1/blocks/{quote(item_id, safe='')}/children",
            headers=headers,
            params=params,
            timeout=timeout,
        ))
        batch = child.get("results")
        if not isinstance(batch, list):
            break
        results.extend(item for item in batch if isinstance(item, Mapping))
        if not child.get("has_more") or not child.get("next_cursor"):
            break
        cursor = str(child["next_cursor"])
    text = "\n".join(filter(None, (_notion_block_text(block) for block in results)))
    return {
        "title": _notion_page_title(page),
        "text": text,
        "url": page.get("url"),
        "metadata": {"provider": "notion", "last_edited_time": page.get("last_edited_time"), "archived": page.get("archived"), "blocks_read": len(results)},
    }
