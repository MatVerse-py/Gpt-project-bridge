from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from docx import Document as DocxDocument
from openpyxl import load_workbook
from pypdf import PdfReader
from pptx import Presentation

from .db import Database
from .ingest import sha256_file, utc_now

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".html", ".htm",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".php", ".rb", ".swift", ".kt", ".kts", ".scala", ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".env", ".log", ".tex",
}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CELL_COUNT = 200_000


def _text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        parts.append(f"## Page {index}\n\n{page.extract_text() or ''}")
    return "\n\n".join(parts)


def _docx(path: Path) -> str:
    document = DocxDocument(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table_index, table in enumerate(document.tables, start=1):
        parts.append(f"\n## Table {table_index}")
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _xlsx(path: Path) -> str:
    workbook = load_workbook(path, read_only=True, data_only=True)
    parts: list[str] = []
    cells = 0
    try:
        for worksheet in workbook.worksheets:
            parts.append(f"# Sheet: {worksheet.title}")
            for row in worksheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    parts.append("\t".join(values))
                cells += len(values)
                if cells > MAX_CELL_COUNT:
                    parts.append("[TRUNCATED: spreadsheet cell limit reached]")
                    return "\n".join(parts)
    finally:
        workbook.close()
    return "\n".join(parts)


def _pptx(path: Path) -> str:
    presentation = Presentation(str(path))
    parts: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        parts.append(f"# Slide {index}")
        for shape in slide.shapes:
            text = getattr(shape, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n".join(parts)


def extractor(path: Path) -> Callable[[Path], str] | None:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return _text
    if suffix == ".pdf":
        return _pdf
    if suffix == ".docx":
        return _docx
    if suffix in {".xlsx", ".xlsm"}:
        return _xlsx
    if suffix == ".pptx":
        return _pptx
    return None


def ingest_directory(
    db: Database,
    root: Path,
    project_id: str,
    project_name: str,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    project_id = project_id.strip()
    project_name = project_name.strip()
    if not project_id or not project_name:
        raise ValueError("project_id and project_name are required")

    started = utc_now()
    db.upsert_project(project_id, project_name, "owner_supplied_directory")
    imported = skipped = failed = 0
    failures: list[dict[str, str]] = []
    aggregate = hashlib.sha256()

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if path.stat().st_size > max_file_bytes:
            skipped += 1
            failures.append({"path": relative, "reason": "file_too_large"})
            continue
        read = extractor(path)
        if read is None:
            skipped += 1
            failures.append({"path": relative, "reason": "unsupported_type"})
            continue
        try:
            text = read(path).strip()
            if not text:
                skipped += 1
                failures.append({"path": relative, "reason": "empty_extracted_text"})
                continue
            content_hash = sha256_file(path)
            aggregate.update(relative.encode("utf-8"))
            aggregate.update(content_hash.encode("ascii"))
            document_id = f"file:{hashlib.sha256((project_id + '\0' + relative).encode('utf-8')).hexdigest()}"
            timestamp = path.stat().st_mtime
            body = (
                f"# {path.name}\n\n## Provenance\n\nProject: {project_name}\n"
                f"Project ID: {project_id}\nAttribution basis: owner_supplied_directory\n"
                f"Relative path: {relative}\nSHA-256: {content_hash}\n\n## Extracted content\n\n{text}\n"
            )
            db.upsert_document({
                "document_id": document_id,
                "conversation_id": f"file:{relative}",
                "title": path.name,
                "project_id": project_id,
                "project_name": project_name,
                "attribution_basis": "owner_supplied_directory",
                "created_at_epoch": timestamp,
                "updated_at_epoch": timestamp,
                "source_file": relative,
                "source_hash": content_hash,
                "body": body,
                "metadata": {
                    "source_type": "file",
                    "relative_path": relative,
                    "size_bytes": path.stat().st_size,
                    "mime_type": mimetypes.guess_type(path.name)[0],
                    "source_hash": content_hash,
                },
            })
            imported += 1
        except Exception as exc:
            failed += 1
            failures.append({"path": relative, "reason": f"{type(exc).__name__}: {exc}"})

    completed = utc_now()
    run = {
        "run_id": str(uuid.uuid4()),
        "source_name": str(root),
        "source_hash": aggregate.hexdigest(),
        "imported_documents": imported,
        "assigned_documents": imported,
        "unassigned_documents": 0,
        "started_at": started,
        "completed_at": completed,
        "metadata": {
            "source_type": "directory",
            "project_id": project_id,
            "project_name": project_name,
            "skipped": skipped,
            "failed": failed,
            "failures": failures,
        },
    }
    db.record_ingestion(run)
    return run
