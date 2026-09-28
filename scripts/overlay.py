#!/usr/bin/env python3
"""Provision local domains, run endpoints, submit work, and drain durable queues."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.federation_ed25519 import ED25519_PUBLIC_KEY_SCHEME, ed25519_public_key_hex, sign_relation_ed25519_source, sign_relation_ed25519_target
from app.federation_key_registry import AuthorityKeyRecord, authority_key_id
from app.federation_relation import FederationRelation
from app.overlay_domain import OverlayDomain
from app.overlay_protocol import CAPABILITY, CONTRACT_HASH


def write_private(path: Path, raw: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(raw)


def provision_pair(root: Path, port_a: int, port_b: int) -> tuple[Path, Path]:
    """Single-administrator local acceptance setup, never independent custody."""
    if not 1 <= port_a <= 65535 or not 1 <= port_b <= 65535 or port_a == port_b:
        raise ValueError("distinct_valid_ports_required")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(root.iterdir()):
        raise FileExistsError("provisioning_directory_must_be_empty")
    now = int(time.time())
    keys = {name: Ed25519PrivateKey.generate() for name in ("domain-a", "domain-b")}
    records = []
    for name, key in keys.items():
        folder = root / name
        folder.mkdir(mode=0o700)
        write_private(folder / "identity.pem", key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        public = ed25519_public_key_hex(key)
        records.append(asdict(AuthorityKeyRecord(name, authority_key_id(public), public, now - 60, now + 86400)))
    relations = []
    for source, target in (("domain-a", "domain-b"), ("domain-b", "domain-a")):
        relation = FederationRelation(relation_id=source + "-to-" + target, source_domain=source, target_domain=target, source_authority=source, target_authority=target, contract_hash=CONTRACT_HASH, capabilities=(CAPABILITY,), valid_from=now - 60, valid_until=now + 86400, witness_scheme=ED25519_PUBLIC_KEY_SCHEME)
        relation = sign_relation_ed25519_source(relation, private_key=keys[source])
        relation = sign_relation_ed25519_target(relation, private_key=keys[target])
        relations.append(asdict(relation))
    configs = []
    for name, port, other, other_port in (("domain-a", port_a, "domain-b", port_b), ("domain-b", port_b, "domain-a", port_a)):
        folder = root / name
        config = {"domain_id": name, "organism_id": "organism-overlay-1", "private_key_file": str(folder / "identity.pem"), "database": str(folder / "state.sqlite3"), "backend": "python", "listen_port": port, "peers": {other: f"http://127.0.0.1:{other_port}"}, "public_keys": records, "relations": relations}
        path = folder / "config.json"
        write_private(path, json.dumps(config, indent=2).encode())
        configs.append(path)
    return tuple(configs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("provision-pair")
    pair.add_argument("--directory", type=Path, required=True)
    pair.add_argument("--port-a", type=int, default=8791)
    pair.add_argument("--port-b", type=int, default=8792)
    for action in ("serve", "submit", "flush", "state", "local"):
        sub = commands.add_parser(action)
        sub.add_argument("--config", type=Path, required=True)
        if action == "serve":
            sub.add_argument("--backend", choices=["python", "node"])
        if action in {"submit", "local"}:
            sub.add_argument("--file", type=Path, required=True)
            sub.add_argument("--human-context", type=Path)
        if action == "submit":
            sub.add_argument("--target", required=True)
            sub.add_argument("--relation", required=True)
    args = parser.parse_args()
    if args.command == "provision-pair":
        print(json.dumps({"configs": [str(p) for p in provision_pair(args.directory, args.port_a, args.port_b)]}))
        return 0
    config = json.loads(args.config.read_text())
    if args.command == "serve" and args.backend:
        config["backend"] = args.backend
    domain = OverlayDomain(config)
    if args.command == "serve":
        import uvicorn
        from app.overlay_http import create_app
        uvicorn.run(create_app(domain), host="127.0.0.1", port=config["listen_port"], limit_concurrency=16, timeout_keep_alive=5, log_level="warning")
        return 0
    if args.command in {"submit", "local"}:
        from app.overlay_protocol import MAX_TEXT_BYTES
        with args.file.open("rb") as incoming:
            raw = incoming.read(MAX_TEXT_BYTES + 1)
        text = raw.decode("utf-8")
        human = json.loads(args.human_context.read_text()) if args.human_context else None
        if args.command == "local":
            print(json.dumps(domain.execute_local(text, human=human), ensure_ascii=False))
            return 0
        task_id = domain.enqueue(target=args.target, relation_id=args.relation, text=text, human=human)
        status = domain.dispatch(task_id)
        print(json.dumps({"task_id": task_id, "status": status}))
        return 0 if status == "ACKED" else 2
    result = domain.flush() if args.command == "flush" else domain.state()
    print(json.dumps(result, ensure_ascii=False))
    return 2 if args.command == "flush" and any(status != "ACKED" for status in result.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
