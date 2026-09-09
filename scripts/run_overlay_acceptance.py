#!/usr/bin/env python3
"""Run real loopback domains, a severable HTTP link, and executor replacement.

No provider API, network namespace, Docker, GPU, or paid service is required.
All child processes are terminated; databases and keys live in a private
temporary directory. The exported report contains no payloads or private keys.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.overlay_domain import OverlayDomain
from app.overlay_protocol import CONTRACT_HASH
from scripts.overlay import provision_pair


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def read_health(port: int) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
        return json.load(response)


class FaultLink(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, target: int):
        self.target = target
        self.drop_next_result = False
        super().__init__(("127.0.0.1", port), LinkHandler)


class LinkHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length) if length else None
        connection = http.client.HTTPConnection("127.0.0.1", self.server.target, timeout=5)
        try:
            connection.request(self.command, self.path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            if self.command == "POST" and self.server.drop_next_result:
                self.server.drop_next_result = False
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            connection.close()


def start_link(port, target):
    link = FaultLink(port, target)
    thread = threading.Thread(target=lambda: link.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    return link


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"protocol": "matverse.overlay-acceptance.v1", "status": "FAIL", "scope": "two loopback services, separate databases and keys, one administrative host", "contract_hash": CONTRACT_HASH, "checks": {}, "external_independence": "NOT_TESTED", "model_substitution": "NOT_TESTED; executor substitution is Python to Node.js"}
    source_root = Path(__file__).resolve().parents[1]
    report["tested_at"] = datetime.now(timezone.utc).isoformat()
    report["source_files"] = {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in [*sorted((source_root / "app").glob("overlay_*")), source_root / "app/federation_key_registry.py", Path(__file__).resolve(), Path(__file__).with_name("overlay.py")] if path.is_file()}
    children = []
    link = None
    try:
        with tempfile.TemporaryDirectory(prefix="matverse-overlay-") as folder:
            ports = set()
            while len(ports) < 3:
                ports.add(free_port())
            port_a, port_b, port_link = sorted(ports)
            paths = provision_pair(Path(folder) / "domains", port_a, port_b)
            configs = [json.loads(path.read_text()) for path in paths]
            configs[0]["peers"]["domain-b"] = f"http://127.0.0.1:{port_link}"
            paths[0].write_text(json.dumps(configs[0]))
            log_path = Path(folder) / "services.log"

            def start(index, backend):
                with log_path.open("ab") as log:
                    proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("overlay.py")), "serve", "--config", str(paths[index]), "--backend", backend], stdout=log, stderr=log)
                children.append(proc)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError("domain_start_failed:" + log_path.read_text()[-2000:])
                    try:
                        if read_health(configs[index]["listen_port"])["status"] == "ready":
                            return proc
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.05)
                raise RuntimeError("domain_start_timeout")

            server_a = start(0, "python")
            server_b = start(1, "python")
            link = start_link(port_link, port_b)
            a, b = [OverlayDomain(config) for config in configs]
            identity_before = {key: a.state()[key] for key in ("domain_id", "organism_id", "public_key_id")}
            # Real repository text is the workload; only its hash is reported.
            corpus_text = (Path(__file__).resolve().parents[1] / "MODEL_BRIDGE_V1.md").read_text()
            corpus_text = corpus_text.replace("\n", "\r\n")
            report["input_sha256"] = hashlib.sha256(corpus_text.encode()).hexdigest()

            def submit(text):
                task = a.enqueue(target="domain-b", relation_id="domain-a-to-domain-b", text=text)
                status = a.dispatch(task)
                return task, status

            first, status = submit(corpus_text)
            assert status == "ACKED", a.task(first)["last_error"]
            first_result = json.loads(a.task(first)["response_json"])["body"]
            report["checks"]["signed_discovery_route_execution"] = True

            # Sever the communication link while the receiver remains healthy.
            link.shutdown()
            link.server_close()
            link = None
            pending, status = submit(corpus_text)
            assert status == "PENDING"
            assert read_health(port_b)["domain_id"] == "domain-b"
            a.execute_local("local work continues\r\n")
            assert a.state()["committed_results"] == 1
            report["checks"]["network_partition_with_live_receiver"] = True
            report["checks"]["local_autonomy"] = True

            # Reconstruct source state after its endpoint restarts.
            stop(server_a)
            del a
            server_a = start(0, "python")
            a = OverlayDomain(configs[0])
            assert a.task(pending)["status"] == "PENDING"
            assert {key: a.state()[key] for key in identity_before} == identity_before
            link = start_link(port_link, port_b)
            assert a.flush()[pending] == "ACKED"
            report["checks"]["durable_outbox_after_restart"] = True
            report["checks"]["reconnection_and_reconciliation"] = True

            # Receiver commits, but a real socket closure discards the result.
            before_count = b.state()["committed_results"]
            link.drop_next_result = True
            lost, status = submit("response lost after commit\r\n")
            assert status == "PENDING"
            assert b.state()["committed_results"] == before_count + 1
            stop(server_b)
            server_b = start(1, "python")
            assert a.dispatch(lost) == "ACKED"
            assert b.state()["committed_results"] == before_count + 1
            report["checks"]["lost_response_retry_without_duplicate_commit"] = True

            # Substitute the actual computation runtime; domain identity persists.
            before_identity = b.state()["public_key_id"]
            stop(server_b)
            server_b = start(1, "node")
            changed, status = submit(corpus_text)
            assert status == "ACKED", a.task(changed)["last_error"]
            changed_result = json.loads(a.task(changed)["response_json"])["body"]
            assert changed_result["output"] == first_result["output"]
            assert changed_result["binding_hash"] != first_result["binding_hash"]
            assert changed_result["executor"]["backend"] == "node"
            assert b.state()["public_key_id"] == before_identity
            report["checks"]["python_to_node_executor_substitution"] = True
            report["checks"]["identity_contract_and_output_preserved"] = True
            report["executor_bindings"] = [first_result["executor"], changed_result["executor"]]

            # Both domain directions are operational, not just one receiving URL.
            reverse = b.enqueue(target="domain-a", relation_id="domain-b-to-domain-a", text="reverse traversal\r\n")
            assert b.dispatch(reverse) == "ACKED"
            report["checks"]["bidirectional_execution"] = True
            report["result_counts"] = {"domain-a": a.state()["committed_results"], "domain-b": b.state()["committed_results"]}
            report["status"] = "PASS_LOCAL"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if link is not None:
            link.shutdown()
            link.server_close()
        for proc in children:
            stop(proc)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS_LOCAL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
