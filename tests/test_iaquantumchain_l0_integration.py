from __future__ import annotations

import hashlib
import json
from dataclasses import replace
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app
from app.auth import sign_request
from app.iaquantumchain.qex_adapter import QeXAdapter, QeXAdmissionError
from app.iaquantumchain.service import IAQuantumChainService


@pytest.mark.parametrize("bit", [0, 1])
def test_l0_uses_real_qex_bridge_governance_tsc_and_shared_ledger(tmp_path, monkeypatch, bit):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / f"iaquantumchain-{bit}.sqlite3")
    service = IAQuantumChainService()
    assert service.qex.available is True

    result = service.submit(
        principal_id="l0-integration-test",
        authenticated=True,
        experiment_id="QEX-SUBSTRATE-01",
        payload={"bit": bit},
        request_id=str(uuid.uuid4()),
    )

    assert result["status"] == "EXECUTED_L0"
    assert result["decision"] == "PASS"
    assert result["tsc"]["status"] == "EXACT"
    assert result["tsc"]["receipt"]["schema"] == "matverse.evidence-receipt.v1"
    assert len(result["tsc"]["receipt"]["receipt_hash"]) == 64
    assert len(result["results"]) == 2
    assert result["results"][0]["execution_class"] == "CLASSICAL"
    assert result["results"][1]["execution_class"] == "QUANTUM_SIMULATED"
    assert all(item["physical_execution"] is False for item in result["results"])
    assert all(item["observable"]["result"] == 1 - bit for item in result["results"])
    assert result["execution_boundary"]["physical_qpu"] == "NOT_EXECUTED"
    assert result["execution_boundary"]["quantum_advantage"] == "NOT_CLAIMED"

    ledger = service.verify_ledger()
    assert ledger["ok"] is True
    assert ledger["events"] >= 2
    assert service.get_run(result["run_id"])["found"] is True



def test_read_only_api_status_and_dashboard_are_mounted():
    client = TestClient(app)
    status = client.get("/v1/iaquantumchain/status/public")
    assert status.status_code == 200
    assert status.json()["modules"]["QeX"]["state"] == "READY_L0"
    dashboard = client.get("/v1/iaquantumchain/dashboard")
    assert dashboard.status_code == 200
    assert "MatVerse" in dashboard.text



def test_authenticated_capability_api_executes_l0(tmp_path, monkeypatch):
    principal = "l0-api-ci"
    secret = "ci-only-secret"
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "iaquantumchain-api.sqlite3")
    monkeypatch.setenv(
        "MATVERSE_PRINCIPALS_JSON",
        json.dumps({
            principal: {
                "secret": secret,
                "capabilities": ["iaquantumchain:experiment:submit"],
            }
        }),
    )
    monkeypatch.delenv("MATVERSE_AUTH_NONCE_URL", raising=False)

    path = "/v1/iaquantumchain/experiments"
    body = json.dumps(
        {
            "experiment_id": "QEX-SUBSTRATE-01",
            "payload": {"bit": 1},
            "request_id": str(uuid.uuid4()),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    digest = hashlib.sha256(body).hexdigest()
    signature = sign_request(secret, "POST", path, timestamp, nonce, digest)
    headers = {
        "X-MatVerse-Principal": principal,
        "X-MatVerse-Timestamp": timestamp,
        "X-MatVerse-Nonce": nonce,
        "X-MatVerse-Content-SHA256": digest,
        "X-MatVerse-Signature": signature,
        "Content-Type": "application/json",
    }

    response = TestClient(app).post(path, content=body, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "EXECUTED_L0"
    assert result["decision"] == "PASS"
    assert result["tsc"]["status"] == "EXACT"
    assert all(item["observable"]["result"] == 0 for item in result["results"])



def test_noncanonical_qex_problem_is_not_ontology_admissible():
    qex = QeXAdapter()
    assert qex.ontology_admissible() is True
    canonical_contract = qex.contract
    qex.contract = lambda: replace(canonical_contract(), problem_hash="0" * 64)
    assert qex.ontology_admissible() is False
    with pytest.raises(QeXAdmissionError):
        qex.execute_l0_matrix(
            {"bit": 0},
            ontology_ok=qex.ontology_admissible(),
            signature_valid=True,
            transition_valid=True,
        )
