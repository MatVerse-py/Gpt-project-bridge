from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Settings
from .db import Database
from .files import ArchiveLimits, ingest_archive, ingest_directory
from .ingest import ingest_export


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="projectvault")
    sub = root.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest an official ChatGPT export ZIP")
    ingest.add_argument("export_zip", type=Path)
    ingest.add_argument("--owner-map", type=Path)
    ingest.add_argument("--database", type=Path)

    files = sub.add_parser("ingest-files", help="Ingest an explicitly assigned project directory")
    files.add_argument("directory", type=Path)
    files.add_argument("--project-id", required=True)
    files.add_argument("--project-name", required=True)
    files.add_argument("--database", type=Path)

    manus = sub.add_parser("ingest-manus", help="Ingest a Manus .manustask backup as an auditable external archive")
    manus.add_argument("backup", type=Path)
    manus.add_argument("--project-id")
    manus.add_argument("--project-name")
    manus.add_argument("--database", type=Path)
    manus.add_argument("--staging-dir", type=Path)

    serve = sub.add_parser("serve", help="Run the HTTP and MCP server")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)

    sub.add_parser("stats", help="Show database statistics")
    return root


def main() -> int:
    args = parser().parse_args()
    settings = Settings.from_env()
    if args.command == "ingest":
        db_path = args.database.resolve() if args.database else settings.database_path
        result = ingest_export(Database(db_path), args.export_zip.resolve(), args.owner_map.resolve() if args.owner_map else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest-files":
        db_path = args.database.resolve() if args.database else settings.database_path
        result = ingest_directory(Database(db_path), args.directory.resolve(), args.project_id, args.project_name)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest-manus":
        if args.backup.suffix.lower() != ".manustask":
            raise ValueError("backup_must_be_manustask")
        if bool(args.project_id) != bool(args.project_name):
            raise ValueError("project_id_and_project_name_must_be_supplied_together")
        db_path = args.database.resolve() if args.database else settings.database_path
        staging_dir = (
            args.staging_dir.resolve()
            if args.staging_dir
            else (db_path.parent / "staging" if args.database else settings.staging_dir)
        )
        result = ingest_archive(
            Database(db_path),
            args.backup.resolve(),
            source_type="manus_task_backup",
            limits=ArchiveLimits.from_settings(settings),
            staging_dir=staging_dir,
            project_id=args.project_id,
            project_name=args.project_name,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "stats":
        print(json.dumps(Database(settings.database_path).stats(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        host = args.host or settings.host
        port = args.port or settings.port
        if host != settings.host:
            os.environ["PROJECTVAULT_HOST"] = host
        if port != settings.port:
            os.environ["PROJECTVAULT_PORT"] = str(port)
        live_settings = Settings.from_env()
        uvicorn.run(create_app(live_settings), host=live_settings.host, port=live_settings.port, proxy_headers=True)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
