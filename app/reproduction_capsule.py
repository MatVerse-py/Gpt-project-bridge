from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence import canonical_json

SCHEMA_VERSION = "matverse.reproduction-capsule.v1"
MANIFEST_NAME = "capsule_manifest.json"


@dataclass(frozen=True)
class CapsuleEntry:
    path: str
    sha256: str
    size: int
    mode: int


@dataclass(frozen=True)
class CapsuleResult:
    archive_path: str
    archive_sha256: str
    manifest_sha256: str
    entries: tuple[CapsuleEntry, ...]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(root: Path, candidate: str | os.PathLike[str]) -> Path:
    relative = Path(candidate)
    if relative.is_absolute():
        raise ValueError("capsule paths must be relative")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("capsule path escapes root") from exc
    return resolved


def _collect_files(root: Path, include: Iterable[str | os.PathLike[str]]) -> tuple[Path, ...]:
    files: dict[str, Path] = {}
    for raw in include:
        resolved = _safe_relative(root, raw)
        if not resolved.exists():
            raise FileNotFoundError(str(raw))
        if resolved.is_symlink():
            raise ValueError(f"symlinks are not allowed in capsules: {raw}")
        if resolved.is_dir():
            for child in sorted(resolved.rglob("*")):
                if child.is_symlink():
                    raise ValueError(f"symlinks are not allowed in capsules: {child}")
                if child.is_file():
                    rel = child.relative_to(root).as_posix()
                    files[rel] = child
        elif resolved.is_file():
            rel = resolved.relative_to(root).as_posix()
            files[rel] = resolved
        else:
            raise ValueError(f"unsupported capsule entry: {raw}")
    return tuple(files[key] for key in sorted(files))


def build_capsule(
    *,
    root: str | os.PathLike[str],
    include: Iterable[str | os.PathLike[str]],
    output: str | os.PathLike[str],
    metadata: Mapping[str, Any] | None = None,
) -> CapsuleResult:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(str(root_path))
    output_path = Path(output).resolve()
    files = _collect_files(root_path, include)
    if not files:
        raise ValueError("capsule must contain at least one file")
    if output_path.exists() and output_path.is_dir():
        raise IsADirectoryError(str(output_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    entries: list[CapsuleEntry] = []
    for path in files:
        stat = path.stat()
        entries.append(
            CapsuleEntry(
                path=path.relative_to(root_path).as_posix(),
                sha256=_sha256_file(path),
                size=int(stat.st_size),
                mode=int(stat.st_mode & 0o777),
            )
        )

    manifest = {
        "schema": SCHEMA_VERSION,
        "metadata": json.loads(canonical_json(dict(metadata or {}))),
        "entries": [entry.__dict__ for entry in entries],
    }
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    manifest_sha = _sha256_bytes(manifest_bytes)

    with tarfile.open(output_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
        manifest_info = tarfile.TarInfo(MANIFEST_NAME)
        manifest_info.size = len(manifest_bytes)
        manifest_info.mtime = 0
        manifest_info.uid = 0
        manifest_info.gid = 0
        manifest_info.uname = ""
        manifest_info.gname = ""
        manifest_info.mode = 0o644
        archive.addfile(manifest_info, io.BytesIO(manifest_bytes))

        for entry, path in zip(entries, files, strict=True):
            info = tarfile.TarInfo(entry.path)
            info.size = entry.size
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = entry.mode
            with path.open("rb") as handle:
                archive.addfile(info, handle)

    return CapsuleResult(
        archive_path=str(output_path),
        archive_sha256=_sha256_file(output_path),
        manifest_sha256=manifest_sha,
        entries=tuple(entries),
    )


def verify_capsule(path: str | os.PathLike[str]) -> dict[str, Any]:
    archive_path = Path(path).resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(str(archive_path))
    with tarfile.open(archive_path, mode="r") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if not names or names[0] != MANIFEST_NAME:
            raise ValueError("capsule manifest must be the first member")
        if len(names) != len(set(names)):
            raise ValueError("duplicate members in capsule")
        for member in members:
            name_path = Path(member.name)
            if name_path.is_absolute() or ".." in name_path.parts:
                raise ValueError("unsafe path in capsule")
            if not member.isfile():
                raise ValueError("capsule may only contain regular files")
        manifest_handle = archive.extractfile(MANIFEST_NAME)
        if manifest_handle is None:
            raise ValueError("capsule manifest is unreadable")
        manifest_bytes = manifest_handle.read()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if manifest.get("schema") != SCHEMA_VERSION:
            raise ValueError("unsupported capsule schema")
        expected_entries = manifest.get("entries")
        if not isinstance(expected_entries, list) or not expected_entries:
            raise ValueError("capsule manifest entries are missing")
        expected_names = [MANIFEST_NAME] + [str(item["path"]) for item in expected_entries]
        if names != expected_names:
            raise ValueError("capsule members do not match manifest order/content")
        verified: list[dict[str, Any]] = []
        for item in expected_entries:
            member_name = str(item["path"])
            handle = archive.extractfile(member_name)
            if handle is None:
                raise ValueError(f"capsule member unreadable: {member_name}")
            data = handle.read()
            digest = _sha256_bytes(data)
            if digest != str(item["sha256"]):
                raise ValueError(f"hash mismatch: {member_name}")
            if len(data) != int(item["size"]):
                raise ValueError(f"size mismatch: {member_name}")
            verified.append({"path": member_name, "sha256": digest, "size": len(data)})
    return {
        "schema": SCHEMA_VERSION,
        "archive_sha256": _sha256_file(archive_path),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "entries": verified,
        "verified": True,
    }
