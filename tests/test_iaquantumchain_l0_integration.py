from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app
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
