from __future__ import annotations

import hashlib
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .db import Database

CONVERSATION_FILE = re.compile(r"(^|/)(conversations(?:[-_ ]?\d+)?\.json)$", re.IGNORECASE)


@dataclass(frozen=True)
class OwnerProject:
    project_id: str
    project_name: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scalar(obj: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return None


def explicit_project(obj: Any, depth: int = 0) -> OwnerProject | None:
    if depth > 5:
        return None
    if isinstance(obj, dict):
        project_id = scalar(obj, ("project_id", "projectId", "workspace_project_id"))
        project_name = scalar(obj, ("project_name", "projectName"))
        project = obj.get("project")
        if isinstance(project, dict):
            project_id = project_id or scalar(project, ("id", "project_id"))
            project_name = project_name or scalar(project, ("name", "title"))
        elif isinstance(project, str) and project.strip():
            project_name = project_name or project.strip()
        if project_id or project_name:
            stable_id = project_id or f"owner-name:{sha256_bytes(project_name.encode('utf-8'))[:20]}"
            return OwnerProject(stable_id, project_name or stable_id)
        for key, value in obj.items():
            if "project" in str(key).lower() or key in {"metadata", "context", "conversation"}:
                found = explicit_project(value, depth + 1)
                if found:
                    return found
    elif isinstance(obj, list):
        for value in obj[:200]:
            found = explicit_project(value, depth + 1)
            if found:
                return found
    return None


def extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(filter(None, (extract_text(item) for item in content)))
    if not isinstance(content, dict):
        return str(content)
    parts = content.get("parts")
    if isinstance(parts, list):
        return "\n".join(filter(None, (extract_text(part) for part in parts)))
    for key in ("text", "result", "content"):
        if key in content:
            return extract_text(content[key])
    return ""


def epoch(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def iso_epoch(value: float | None) -> str:
    if value is None:
        return "unknown-time"
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return "unknown-time"


def messages(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    mapping = conversation.get("mapping")
    nodes: list[Any]
    if isinstance(mapping, dict):
        nodes = list(mapping.values())
    else:
        direct = conversation.get("messages")
        nodes = direct if isinstance(direct, list) else []
    output: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        message = node.get("message") if isinstance(node.get("message"), dict) else node
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = "unknown"
        if isinstance(author, dict):
            role = str(author.get("role") or "unknown")
        elif isinstance(message.get("role"), str):
            role = str(message["role"])
        text = extract_text(message.get("content")).strip()
        if not text:
            continue
        created = epoch(message.get("create_time", message.get("created_at")))
        output.append({
            "message_id": str(message.get("id") or node.get("id") or f"message-{index}"),
            "role": role,
            "created_at_epoch": created,
            "text": text,
        })
    output.sort(key=lambda item: (item["created_at_epoch"] is None, item["created_at_epoch"] or 0, item["message_id"]))
    return output


def render_body(title: str, project: OwnerProject, basis: str, items: list[dict[str, Any]]) -> str:
    lines = [
        f"# {title}", "", "## Provenance", "",
        f"Project: {project.project_name}",
        f"Project ID: {project.project_id}",
        f"Attribution basis: {basis}", "", "## Conversation", "",
    ]
    for item in items:
        lines.extend([
            f"### {item['role']} — {iso_epoch(item['created_at_epoch'])}", "",
            item["text"], "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def iter_conversations(payload: Any) -> Iterator[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(payload, dict):
        return
    for key in ("conversations", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
            return
    if any(key in payload for key in ("mapping", "messages", "conversation_id", "id")):
        yield payload


def load_owner_map(path: Path | None) -> dict[str, OwnerProject]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("conversations") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        raise ValueError("Owner map must contain an object named 'conversations'")
    result: dict[str, OwnerProject] = {}
    for conversation_id, value in entries.items():
        if not isinstance(value, dict):
            raise ValueError("Invalid owner mapping for {conversation_id}")
        project_id = str(value.get("project_id") or "").strip()
        project_name = str(value.get("project_name") or "").strip()
        if not project_id or not project_name:
            raise ValueError("Owner mapping for {conversation_id} requires project_id and project_name")
        result[str(conversation_id)] = OwnerProject(project_id, project_name)
    return result


def ingest_export(db: Database, export_zip: Path, owner_map_path: Path | None = None) -> dict[str, Any]:
    if not export_zip.is_file() or not zipfile.is_zipfile(export_zip):
        raise ValueError(f"Invalid export ZIP: {export_zip}")
    owner_map = load_owner_map(owner_map_path)
    started = utc_now()
    source_hash = sha256_file(export_zip)
    imported = assigned = unassigned = 0
    source_files: list[str] = []

    with zipfile.ZipFile(export_zip) as archive:
        names = [name for name in archive.namelist() if CONVERSATION_FILE.search(name)]
        if not names:
            raise ValueError("No conversations*.json file found in export ZIP")
        for source_name in sorted(names):
            source_files.append(source_name)
            raw = archive.read(source_name)
            payload = json.loads(raw.decode("utf-8-sig"))
            for index, conversation in enumerate(iter_conversations(payload)):
                conversation_id = str(
                    conversation.get("conversation_id")
                    or conversation.get("id")
                    or conversation.get("uuid")
                    or f"{source_name}:{index}"
                )
                explicit = explicit_project(conversation)
                mapped = owner_map.get(conversation_id)
                if explicit and mapped and (
                    explicit.project_id != mapped.project_id or explicit.project_name != mapped.project_name
                ):
                    raise ValueError(
                        f"Project attribution conflict for conversation {conversation_id}: "
                        f"export={explicit.project_id}/{explicit.project_name}, "
                        f"owner_map={mapped.project_id}/{mapped.project_name}"
                    )
                if explicit:
                    project = explicit
                    basis = "explicit_export_metadata"
                elif mapped:
                    project = mapped
                    basis = "owner_mapping"
                else:
                    project = OwnerProject("unassigned", "UNASSIGNED")
                    basis = "no_explicit_project_metadata"

                title = str(conversation.get("title") or "Untitled conversation")
                created = epoch(conversation.get("create_time", conversation.get("created_at")))
                updated = epoch(conversation.get("update_time", conversation.get("updated_at")))
                items = messages(conversation)
                canonical = json.dumps(conversation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                source_conversation_hash = sha256_bytes(canonical)
                document_id = f"chat:{conversation_id}"

                db.upsert_project(project.project_id, project.project_name, basis)
                db.upsert_document({
                    "document_id": document_id,
                    "conversation_id": conversation_id,
                    "title": title,
                    "project_id": project.project_id,
                    "project_name": project.project_name,
                    "attribution_basis": basis,
                    "created_at_epoch": created,
                    "updated_at_epoch": updated,
                    "source_file": source_name,
                    "source_hash": source_conversation_hash,
                    "body": render_body(title, project, basis, items),
                    "metadata": {
                        "message_count": len(items),
                        "source_export": export_zip.name,
                        "source_file": source_name,
                        "source_hash": source_conversation_hash,
                    },
                })
                imported += 1
                if project.project_id == "unassigned":
                    unassigned += 1
                else:
                    assigned += 1

    completed = utc_now()
    run = {
        "run_id": str(uuid.uuid4()),
        "source_name": export_zip.name,
        "source_hash": source_hash,
        "imported_documents": imported,
        "assigned_documents": assigned,
        "unassigned_documents": unassigned,
        "started_at": started,
        "completed_at": completed,
        "metadata": {"conversation_files": source_files, "owner_map": str(owner_map_path) if owner_map_path else None},
    }
    db.record_ingestion(run)
    return run
