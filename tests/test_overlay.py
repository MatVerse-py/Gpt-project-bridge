from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.overlay_domain import OverlayDomain, endpoint_url
from app.overlay_http import create_app
from app.overlay_protocol import CONTRACT_HASH, Job, TextInput, TextExecutor, normalized_result, sign, verify
from scripts.overlay import provision_pair


@pytest.fixture
def pair(tmp_path):
    paths = provision_pair(tmp_path / "pair", 8791, 8792)
    configs = [json.loads(path.read_text()) for path in paths]
    domains = [OverlayDomain(config) for config in configs]
    return domains, configs


def queued(a, text="MatVerse\r\nlinha 2\r🙂"):
    task = a.enqueue(target="domain-b", relation_id="domain-a-to-domain-b", text=text)
    return task, json.loads(a.task(task)["request_json"])


def test_two_databases_recovery_and_result_deduplication(pair):
    (a, b), configs = pair
    task, message = queued(a)
    result = b.receive(message)
    # Source crashed after target committed; neither Python object survives.
    a, b = [OverlayDomain(config) for config in configs]
    assert a.task(task)["status"] == "PENDING"
    assert b.receive(message) == result
    a.accept_result(task, result)
    a.accept_result(task, result)
    assert a.task(task)["status"] == "ACKED"
    assert b.state()["committed_results"] == 1
    assert a.state()["committed_results"] == 0


def test_concurrent_deliveries_commit_one_result(pair):
    (a, b), _ = pair
    _, message = queued(a)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(b.receive, [message] * 8))
    assert all(item == results[0] for item in results)
    assert b.state()["committed_results"] == 1


@pytest.mark.parametrize("mutation", ["signature", "target", "contract", "expired", "capability"])
def test_invalid_requests_never_commit(pair, mutation):
    (a, b), _ = pair
    _, message = queued(a)
    if mutation == "signature":
        message["signature"] = "00" * 64
    else:
        fields = {"target": ("target_domain", "domain-c"), "contract": ("contract_hash", "0" * 64), "expired": ("expires_at", 1), "capability": ("capability", "shell:execute")}
        key, value = fields[mutation]
        message["body"][key] = value
        message = sign("request", message["body"], a.private_key)
    with pytest.raises((PermissionError, ValueError)):
        b.receive(message)
    assert b.state()["committed_results"] == 0


def test_signed_task_id_reuse_with_other_input_is_rejected(pair):
    (a, b), _ = pair
    _, message = queued(a)
    b.receive(message)
    message["body"]["input"]["text"] = "changed"
    with pytest.raises(PermissionError, match="task_id_payload_conflict"):
        b.receive(sign("request", message["body"], a.private_key))
    assert b.state()["committed_results"] == 1


def test_revocation_blocks_previously_queued_work(pair):
    (a, b), _ = pair
    _, message = queued(a)
    binding = b.registry.get_relation_binding(message["body"]["relation_id"])
    b.registry.revoke_key("domain-a", binding.source_key_id, effective_at=b.now(), reason="local administrator revoked", actor_id="domain-b")
    with pytest.raises(PermissionError, match="revoked"):
        b.receive(message)
    assert b.state()["committed_results"] == 0


def test_expired_queued_task_becomes_terminal_without_network(pair):
    (a, _), _ = pair
    task, _ = queued(a)
    now = a.now()
    a.now = lambda: now + 4000
    assert a.dispatch(task) == "BLOCKED"
    assert a.task(task)["attempts"] == 1
    assert a.dispatch(task) == "BLOCKED"
    assert a.task(task)["attempts"] == 1


def test_revoked_binding_is_preserved_across_restart(pair):
    (a, b), configs = pair
    _, message = queued(a)
    binding = b.registry.get_relation_binding(message["body"]["relation_id"])
    b.registry.revoke_key("domain-a", binding.source_key_id, effective_at=b.now(), reason="revoked", actor_id="domain-b")
    restarted = OverlayDomain(configs[1])
    with pytest.raises(PermissionError, match="revoked"):
        restarted.receive(message)


def test_forged_result_and_cross_job_result_are_rejected(pair):
    (a, b), _ = pair
    task, message = queued(a)
    result = b.receive(message)
    bad = json.loads(json.dumps(result))
    bad["body"]["output"]["text"] = "wrong"
    with pytest.raises(PermissionError):
        a.accept_result(task, bad)
    with pytest.raises(PermissionError, match="output"):
        a.accept_result(task, sign("result", bad["body"], b.private_key))
    other, _ = queued(a)
    with pytest.raises(PermissionError):
        a.accept_result(other, result)
    assert a.task(task)["status"] == "PENDING"


def test_secret_human_data_never_enters_outbox(pair):
    (a, _), _ = pair
    with pytest.raises(PermissionError, match="SECRET"):
        a.enqueue(target="domain-b", relation_id="domain-a-to-domain-b", text="private", human={"sensitivity": "SECRET"})
    assert a.state()["outbox"] == {}


def test_identity_cannot_silently_change_on_restart(pair):
    _, configs = pair
    changed = {**configs[0], "organism_id": "another-organism"}
    with pytest.raises(PermissionError, match="persistent_identity"):
        OverlayDomain(changed)


def test_discovery_is_challenge_bound_and_signed(pair):
    (_, b), _ = pair
    body = verify(b.discovery("a" * 32), "discovery", b.public_key)
    assert body["challenge"] == "a" * 32
    assert body["executor"]["contract_hash"] == CONTRACT_HASH


def test_real_node_substitution_preserves_result_and_identity(pair):
    (a, b), configs = pair
    _, first = queued(a)
    before = b.receive(first)
    config = {**configs[1], "backend": "node"}
    replacement = OverlayDomain(config)
    _, second = queued(a)
    after = replacement.receive(second)
    assert before["body"]["output"] == after["body"]["output"]
    assert before["body"]["binding_hash"] != after["body"]["binding_hash"]
    assert replacement.state()["public_key_id"] == b.state()["public_key_id"]
    assert replacement.receive(first) == before


@pytest.mark.parametrize("text", ["", "a\r\nb\rc\n", "🙂ação\u0000\t\n", "é e\u0301", "\r\r\n"])
def test_text_contract_across_real_runtimes(text):
    python = TextExecutor("python")
    node = TextExecutor("node")
    assert python.execute(text) == node.execute(text) == normalized_result(text)


@pytest.mark.parametrize("text", ["\ud800", "a" * 65537])
def test_invalid_text_is_rejected(text):
    with pytest.raises(ValueError):
        TextInput(text=text)


def test_http_surface_rejects_bad_signature_size_and_types(pair):
    (a, b), _ = pair
    _, message = queued(a)
    with TestClient(create_app(b)) as client:
        response = client.post("/v1/execute", json=message)
        assert response.status_code == 200
        message["signature"] = "00" * 64
        assert client.post("/v1/execute", json=message).status_code == 403
        assert client.post("/v1/execute", json=[]).status_code == 422
        assert client.post("/v1/execute", content="x" * 524289, headers={"content-type": "application/json"}).status_code == 413


@pytest.mark.parametrize("url", ["http://example.com", "https://user:pass@example.com", "https://example.com/path", "file:///tmp/data"])
def test_peer_endpoints_are_bounded(url):
    with pytest.raises(ValueError):
        endpoint_url(url)
