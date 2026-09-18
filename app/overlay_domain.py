"""Durable overlay endpoint, reusing the Bridge federation and ledger primitives.

Only pure text transformations are admitted. Delivery is at least once; the
receiver commits one immutable result per (source_domain, task_id). This does
not claim exactly-once effects for arbitrary external services.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import storage
from .core import Decision, evaluate_hdb, omega_gate
from .federation_ed25519 import Ed25519RelationWitness, ed25519_public_key_hex
from .federation_key_registry import AuthorityKeyRecord, FederationAuthorityKeyRegistry, GovernedEd25519RelationIntegrityGate, authority_key_id
from .federation_relation import FederationRelation, RelationStatus, RelationRequest, FederatedCapabilityGraph, FederatedCrossing
from .federation_routing import AdmissibilityGate, CapabilityNode, Criterion, Direction, PreferenceModel
from .overlay_protocol import CAPABILITY, CONTRACT, CONTRACT_HASH, Job, MAX_WIRE_BYTES, TextExecutor, TextInput, canonical, digest, normalized_result, sign, verify
from .state_store import SQLiteStateStore


def relation_from_dict(raw: dict) -> FederationRelation:
    data = dict(raw)
    witness = data.pop("witness", None)
    data["status"] = RelationStatus(data.get("status", "ACTIVE"))
    data["capabilities"] = tuple(data["capabilities"])
    return FederationRelation(**data, witness=None if witness is None else Ed25519RelationWitness(**witness))


def endpoint_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("peer_url_must_be_origin")
    if not parsed.hostname or parsed.scheme not in {"http", "https"}:
        raise ValueError("invalid_peer_origin")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("remote_peer_requires_https")
    return value.rstrip("/")


class OverlayDomain:
    def __init__(self, config: dict, *, now=None):
        self.now = now or (lambda: int(time.time()))
        self.domain_id = config["domain_id"]
        self.organism_id = config["organism_id"]
        # Validate persistent identity with the same grammar as wire identities.
        Job(task_id="0" * 32, organism_id=self.organism_id, source_domain=self.domain_id, target_domain="validation", relation_id="validation", contract_hash=CONTRACT_HASH, issued_at=0, expires_at=1, input=TextInput(text=""))
        self.path = Path(config["database"]).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_file = Path(config["private_key_file"])
        if os.name == "posix" and key_file.stat().st_mode & 0o077:
            raise PermissionError("private_key_permissions_must_be_0600")
        self.private_key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        if not isinstance(self.private_key, Ed25519PrivateKey):
            raise ValueError("ed25519_key_required")
        self.public_key = ed25519_public_key_hex(self.private_key)
        self.store = SQLiteStateStore(self.path)
        self.registry = FederationAuthorityKeyRegistry(connect=self.store.connect)
        self.gate = GovernedEd25519RelationIntegrityGate(self.registry, now=self.now)
        self.peers = {name: endpoint_url(url) for name, url in config.get("peers", {}).items()}
        self.executor = TextExecutor(config.get("backend", "python"), config.get("node_binary"))
        self._initialize()
        os.chmod(self.path, 0o600)
        for raw in config["public_keys"]:
            self.registry.register_key(AuthorityKeyRecord(**raw), actor_id=self.domain_id)
        own = self.registry.get_key(authority_key_id(self.public_key))
        if own is None or own.authority_id != self.domain_id:
            raise PermissionError("local_key_not_pinned")
        relations = [relation_from_dict(item) for item in config.get("relations", [])]
        self.relations = {r.relation_id: r for r in relations}
        if len(relations) != len(self.relations):
            raise ValueError("duplicate_relation_id")
        for relation in relations:
            # The local provisioning file binds domain and signing-authority names.
            if relation.source_authority != relation.source_domain or relation.target_authority != relation.target_domain:
                raise PermissionError("domain_authority_mismatch")
            if self.domain_id not in {relation.source_domain, relation.target_domain}:
                raise PermissionError("unrelated_federation_relation")
            bound = self.registry.get_relation_binding(relation.relation_id)
            if bound is not None:
                if bound.relation_sha256 != relation.payload_sha256():
                    raise PermissionError("provisioned_relation_changed")
                continue
            matching = {}
            for authority in (relation.source_authority, relation.target_authority):
                candidates = [k for k in config["public_keys"] if k["authority_id"] == authority and k["valid_from"] <= relation.valid_from and k["valid_until"] >= relation.valid_until]
                if len(candidates) != 1:
                    raise PermissionError("ambiguous_or_missing_relation_key")
                matching[authority] = candidates[0]["key_id"]
            self.registry.register_relation_binding(relation, source_key_id=matching[relation.source_authority], target_key_id=matching[relation.target_authority], actor_id=self.domain_id)

    @contextmanager
    def transaction(self, *, write=False):
        conn = self.store.connect()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self):
        with self.transaction(write=True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS overlay_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), domain_id TEXT NOT NULL, organism_id TEXT NOT NULL, public_key TEXT NOT NULL)")
            row = conn.execute("SELECT * FROM overlay_identity").fetchone()
            identity = (self.domain_id, self.organism_id, self.public_key)
            if row is not None and tuple(row)[1:] != identity:
                raise PermissionError("persistent_identity_mismatch")
            conn.execute("INSERT OR IGNORE INTO overlay_identity VALUES (1,?,?,?)", identity)
            conn.execute("""CREATE TABLE IF NOT EXISTS overlay_outbox (
                task_id TEXT PRIMARY KEY, request_json TEXT NOT NULL, request_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','ACKED','BLOCKED')),
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, response_json TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS overlay_results (
                source_domain TEXT NOT NULL, task_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                response_json TEXT NOT NULL, PRIMARY KEY(source_domain,task_id))""")

    def _relation(self, job: Job):
        relation = self.relations.get(job.relation_id)
        if relation is None:
            raise PermissionError("unknown_relation")
        request = RelationRequest(job.source_domain, job.target_domain, job.contract_hash, job.capability)
        decision = self.gate.evaluate(relation, request)
        if not decision.admissible:
            raise PermissionError("relation_blocked:" + ",".join(decision.reasons))
        return relation

    def _public_for(self, relation: FederationRelation, *, source: bool) -> str:
        binding = self.registry.get_relation_binding(relation.relation_id)
        if binding is None:
            raise PermissionError("missing_key_binding")
        record = self.registry.get_key(binding.source_key_id if source else binding.target_key_id)
        if record is None:
            raise PermissionError("missing_bound_key")
        return record.public_key_hex

    def _validate_job(self, job: Job):
        now = self.now()
        if job.contract_hash != CONTRACT_HASH:
            raise PermissionError("unsupported_contract")
        if not job.issued_at <= now < job.expires_at or job.expires_at - job.issued_at > 86400:
            raise PermissionError("job_outside_validity_window")
        decision, reason = omega_gate(hdb=evaluate_hdb(job.human), action="EXECUTE", ontology_ok=True, signature_valid=True, transition_valid=True)
        if decision is not Decision.PASS:
            raise PermissionError(reason)

    def route(self, job: Job):
        relation = self._relation(job)
        graph = FederatedCapabilityGraph(
            nodes=[CapabilityNode(n, "domain", {"available": 1.0}) for n in (job.source_domain, job.target_domain)],
            crossings=[FederatedCrossing(job.source_domain, job.target_domain, 0.0, relation.relation_id, job.capability, job.contract_hash)],
            capability_gate=AdmissibilityGate([]),
            preference=PreferenceModel({"available": Criterion("available", Direction.HIGHER_IS_BETTER, 0, 1)}, {"available": 1.0}),
            relations=[relation], relation_gate=self.gate,
        )
        return graph.route(job.source_domain, [job.target_domain], capability=job.capability)

    def enqueue(self, *, target: str, relation_id: str, text: str, ttl: int = 3600, human: dict | None = None) -> str:
        if target not in self.peers:
            raise PermissionError("peer_not_configured")
        now = self.now()
        job = Job(task_id=uuid.uuid4().hex, organism_id=self.organism_id, source_domain=self.domain_id, target_domain=target, relation_id=relation_id, contract_hash=CONTRACT_HASH, issued_at=now, expires_at=now + ttl, input=TextInput(text=text), human=human)
        self._validate_job(job)
        route = self.route(job)
        message = sign("request", job.model_dump(), self.private_key)
        with self.transaction(write=True) as conn:
            if conn.execute("SELECT count(*) FROM overlay_outbox WHERE status='PENDING'").fetchone()[0] >= 1000:
                raise RuntimeError("outbox_capacity_exceeded")
            conn.execute("INSERT INTO overlay_outbox(task_id,request_json,request_hash,status) VALUES (?,?,?,'PENDING')", (job.task_id, canonical(message).decode(), digest(job.model_dump())))
            storage._append_ledger_tx(conn, {"event_type": "OVERLAY_QUEUED", "task_id": job.task_id, "request_hash": digest(job.model_dump()), "route_hash": route.relation_receipt_sha256}, "PASS")
        return job.task_id

    def _commit_result(self, job: Job) -> dict:
        request_hash = digest(job.model_dump())
        with self.transaction(write=True) as conn:
            # Serialize authorization against local revocation writes as well as
            # result commits. The capability is pure and bounded to five seconds.
            self._validate_job(job)
            if job.source_domain != self.domain_id:
                self._relation(job)
            previous = conn.execute("SELECT * FROM overlay_results WHERE source_domain=? AND task_id=?", (job.source_domain, job.task_id)).fetchone()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise PermissionError("task_id_payload_conflict")
                return json.loads(previous["response_json"])
            output = self.executor.execute(job.input.text)
            self._validate_job(job)
            if job.source_domain != self.domain_id:
                self._relation(job)
            body = {"task_id": job.task_id, "organism_id": job.organism_id, "source_domain": job.source_domain, "target_domain": self.domain_id, "request_hash": request_hash, "contract_hash": CONTRACT_HASH, "capability": CAPABILITY, "output": output, "executor": self.executor.binding, "binding_hash": self.executor.binding_hash}
            response = sign("result", body, self.private_key)
            conn.execute("INSERT INTO overlay_results VALUES (?,?,?,?)", (job.source_domain, job.task_id, request_hash, canonical(response).decode()))
            storage._append_ledger_tx(conn, {"event_type": "OVERLAY_EXECUTED", "task_id": job.task_id, "source_domain": job.source_domain, "request_hash": request_hash, "result_hash": digest(response), "binding_hash": self.executor.binding_hash}, "PASS")
            return response

    def receive(self, message: dict) -> dict:
        job = Job.model_validate(message.get("body"))
        if job.target_domain != self.domain_id or job.source_domain == self.domain_id:
            raise PermissionError("wrong_execution_boundary")
        relation = self._relation(job)
        verify(message, "request", self._public_for(relation, source=True))
        self._validate_job(job)
        return self._commit_result(job)

    def execute_local(self, text: str, *, human: dict | None = None) -> dict:
        now = self.now()
        job = Job(task_id=uuid.uuid4().hex, organism_id=self.organism_id, source_domain=self.domain_id, target_domain=self.domain_id, relation_id="local", contract_hash=CONTRACT_HASH, issued_at=now, expires_at=now + 60, input=TextInput(text=text), human=human)
        self._validate_job(job)
        return self._commit_result(job)

    def discovery(self, challenge: str) -> dict:
        if len(challenge) != 32 or any(c not in "0123456789abcdef" for c in challenge):
            raise ValueError("invalid_discovery_challenge")
        return sign("discovery", {"domain_id": self.domain_id, "challenge": challenge, "expires_at": self.now() + 30, "capabilities": [CONTRACT], "executor": self.executor.binding, "binding_hash": self.executor.binding_hash}, self.private_key)

    def _exchange(self, method: str, url: str, body: dict | None = None) -> dict:
        # Pin peer origins in local configuration. Never follow redirects or
        # ambient proxies; remote links require normal TLS certificate validation.
        with httpx.Client(timeout=5.0, trust_env=False, follow_redirects=False) as client:
            with client.stream(method, url, json=body) as response:
                response.raise_for_status()
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > MAX_WIRE_BYTES:
                        raise ValueError("peer_response_too_large")
        return json.loads(raw)

    def discover_peer(self, job: Job) -> dict:
        relation = self._relation(job)
        challenge = uuid.uuid4().hex
        message = self._exchange("GET", self.peers[job.target_domain] + "/v1/capabilities?challenge=" + challenge)
        body = verify(message, "discovery", self._public_for(relation, source=False))
        if body.get("domain_id") != job.target_domain or body.get("challenge") != challenge:
            raise PermissionError("discovery_identity_or_challenge_mismatch")
        if not self.now() < body.get("expires_at", 0) <= self.now() + 60:
            raise PermissionError("stale_discovery")
        if CONTRACT not in body.get("capabilities", []) or digest(body.get("executor")) != body.get("binding_hash"):
            raise PermissionError("discovery_contract_mismatch")
        return body

    def accept_result(self, task_id: str, response: dict):
        row = self.task(task_id)
        job = Job.model_validate(json.loads(row["request_json"])["body"])
        relation = self._relation(job)
        body = verify(response, "result", self._public_for(relation, source=False))
        expected = {"task_id": task_id, "organism_id": job.organism_id, "source_domain": self.domain_id, "target_domain": job.target_domain, "request_hash": row["request_hash"], "contract_hash": CONTRACT_HASH, "capability": CAPABILITY}
        if any(body.get(key) != value for key, value in expected.items()):
            raise PermissionError("result_identity_or_contract_mismatch")
        if body.get("output") != normalized_result(job.input.text) or digest(body.get("executor")) != body.get("binding_hash"):
            raise PermissionError("result_output_or_binding_mismatch")
        with self.transaction(write=True) as conn:
            current = conn.execute("SELECT status,response_json FROM overlay_outbox WHERE task_id=?", (task_id,)).fetchone()
            encoded = canonical(response).decode()
            if current["status"] == "ACKED":
                if current["response_json"] != encoded:
                    raise PermissionError("conflicting_acknowledgement")
                return
            if current["status"] != "PENDING":
                raise PermissionError("task_is_terminal")
            conn.execute("UPDATE overlay_outbox SET status='ACKED',response_json=?,last_error=NULL WHERE task_id=?", (encoded, task_id))
            storage._append_ledger_tx(conn, {"event_type": "OVERLAY_ACKED", "task_id": task_id, "result_hash": digest(response)}, "PASS")

    def dispatch(self, task_id: str) -> str:
        row = self.task(task_id)
        if row["status"] != "PENDING":
            return row["status"]
        with self.transaction(write=True) as conn:
            conn.execute("UPDATE overlay_outbox SET attempts=attempts+1 WHERE task_id=? AND status='PENDING'", (task_id,))
        try:
            message = json.loads(row["request_json"])
            job = Job.model_validate(message["body"])
            self._validate_job(job)
            relation = self._relation(job)
            verify(message, "request", self._public_for(relation, source=True))
            self.discover_peer(job)
            self.route(job)
            response = self._exchange("POST", self.peers[job.target_domain] + "/v1/execute", message)
            self.accept_result(task_id, response)
        except httpx.HTTPStatusError as exc:
            self._delivery_failure(task_id, "peer_http_" + str(exc.response.status_code), terminal=400 <= exc.response.status_code < 500 and exc.response.status_code not in {408, 429})
        except (PermissionError, ValueError, KeyError, TypeError) as exc:
            self._delivery_failure(task_id, type(exc).__name__ + ":protocol_rejected", terminal=True)
        except (httpx.TransportError, OSError) as exc:
            self._delivery_failure(task_id, type(exc).__name__, terminal=False)
        return self.task(task_id)["status"]

    def _delivery_failure(self, task_id: str, reason: str, *, terminal: bool):
        with self.transaction(write=True) as conn:
            conn.execute("UPDATE overlay_outbox SET status=?,last_error=? WHERE task_id=? AND status='PENDING'", ("BLOCKED" if terminal else "PENDING", reason, task_id))

    def flush(self) -> dict[str, str]:
        with self.transaction() as conn:
            ids = [row[0] for row in conn.execute("SELECT task_id FROM overlay_outbox WHERE status='PENDING' ORDER BY rowid LIMIT 1000")]
        return {task_id: self.dispatch(task_id) for task_id in ids}

    def task(self, task_id: str) -> dict:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM overlay_outbox WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise LookupError("task_not_found")
            return dict(row)

    def state(self) -> dict:
        with self.transaction() as conn:
            return {"domain_id": self.domain_id, "organism_id": self.organism_id, "public_key_id": authority_key_id(self.public_key), "outbox": {row[0]: row[1] for row in conn.execute("SELECT status,count(*) FROM overlay_outbox GROUP BY status")}, "committed_results": conn.execute("SELECT count(*) FROM overlay_results").fetchone()[0], "executor": self.executor.binding, "binding_hash": self.executor.binding_hash}
