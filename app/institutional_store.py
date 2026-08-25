from __future__ import annotations

import json
from typing import Any

from .institutional_projection import jcs_subset_hash
from .model_bridge import assert_transferable_state
from .storage import _append_ledger_tx, _canonical_json, _connect, _now


INTENT_STATUS = "PENDING_EVALUATION"
PARAMETER_PERSISTENCE = "HASH_ONLY"


def _ensure_table(conn: Any) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS institutional_intents (
            intent_id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            requested_operation TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            parameters_hash TEXT NOT NULL,
            source_json TEXT NOT NULL,
            intent_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK(status IN ('PENDING_EVALUATION')),
            created_at TEXT NOT NULL,
            receipt_json TEXT NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_institutional_intents_actor ON institutional_intents(principal_id,created_at)")


def _verify_internal_boundary(intent: dict[str, Any], principal_id: str) -> tuple[str, str, dict[str, Any], dict[str, Any], dict[str, Any], str]:
    if intent.get("actor_id") != principal_id:
        raise ValueError("intent actor_id must match authenticated principal")
    intent_id = intent.get("intent_id")
    intent_hash = intent.get("intent_hash")
    operation = intent.get("requested_operation")
    target = intent.get("target")
    parameters = intent.get("parameters", {})
    source = intent.get("source")
    created_at = intent.get("created_at")
    if not isinstance(intent_id, str) or not intent_id:
        raise ValueError("intent_id missing")
    if not isinstance(intent_hash, str):
        raise ValueError("intent_hash missing")
    if not isinstance(operation, str) or not operation:
        raise ValueError("requested_operation missing")
    if not isinstance(target, dict) or not isinstance(target.get("kind"), str) or not isinstance(target.get("id"), str):
        raise ValueError("target invalid")
    if not isinstance(parameters, dict):
        raise ValueError("parameters invalid")
    if not isinstance(source, dict):
        raise ValueError("source binding invalid")
    if not isinstance(created_at, str):
        raise ValueError("created_at invalid")
    assert_transferable_state({"metadata": parameters})
    parameters_hash = jcs_subset_hash(parameters)
    canonical_intent = dict(intent)
    canonical_intent.pop("intent_hash", None)
    if jcs_subset_hash(canonical_intent) != intent_hash:
        raise ValueError("intent_hash mismatch")
    return intent_id, intent_hash, target, parameters, source, parameters_hash


def persist_intent(*, intent: dict[str, Any], principal_id: str) -> dict[str, Any]:
    """Persist a non-canonical intent commitment and ledger its acceptance.

    Raw intent parameters are deliberately NOT stored in canonical state. Only
    their JCS/SHA-256 commitment is persisted. A future executor must obtain the
    operation payload again, verify the same commitment, then apply the proper
    HDB/Omega/authorization gates before any canonical mutation or execution.
    """

    intent_id, intent_hash, target, _parameters, source, parameters_hash = _verify_internal_boundary(intent, principal_id)
    operation = str(intent["requested_operation"])
    created_at = str(intent["created_at"])

    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_table(conn)
        existing = conn.execute("SELECT * FROM institutional_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if existing is not None:
            if existing["intent_hash"] != intent_hash or existing["principal_id"] != principal_id:
                raise ValueError("intent_id collision or principal mismatch")
            conn.commit()
            return {
                "intent_id": intent_id,
                "intent_hash": existing["intent_hash"],
                "status": existing["status"],
                "principal_id": existing["principal_id"],
                "requested_operation": existing["requested_operation"],
                "target": {"kind": existing["target_kind"], "id": existing["target_id"]},
                "parameters_hash": existing["parameters_hash"],
                "parameter_persistence": PARAMETER_PERSISTENCE,
                "created_at": existing["created_at"],
                "receipt": json.loads(existing["receipt_json"]),
                "idempotent": True,
                "execution_decision": "HOLD",
            }

        collision = conn.execute("SELECT intent_id FROM institutional_intents WHERE intent_hash=?", (intent_hash,)).fetchone()
        if collision is not None:
            raise ValueError("intent_hash already registered under a different intent_id")

        event = {
            "event_type": "INSTITUTIONAL_INTENT_ACCEPTED",
            "intent_id": intent_id,
            "intent_hash": intent_hash,
            "requested_operation": operation,
            "target_kind": target["kind"],
            "target_id": target["id"],
            "parameters_hash": parameters_hash,
            "parameter_persistence": PARAMETER_PERSISTENCE,
            "principal_id": principal_id,
            "projection_hash": source["projection_hash"],
            "source_commit": source["commit_sha"],
            "created_at": created_at,
            "accepted_at": _now(),
            "execution_decision": "HOLD",
            "execution_reason": "intent commitment accepted; raw parameters require later authorized resubmission and evaluation",
        }
        receipt = _append_ledger_tx(conn, event, "PASS")
        conn.execute(
            "INSERT INTO institutional_intents(intent_id,principal_id,requested_operation,target_kind,target_id,parameters_hash,source_json,intent_hash,status,created_at,receipt_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                intent_id,
                principal_id,
                operation,
                target["kind"],
                target["id"],
                parameters_hash,
                _canonical_json(source),
                intent_hash,
                INTENT_STATUS,
                created_at,
                _canonical_json(receipt),
            ),
        )
        conn.commit()
        return {
            "intent_id": intent_id,
            "intent_hash": intent_hash,
            "status": INTENT_STATUS,
            "principal_id": principal_id,
            "requested_operation": operation,
            "target": dict(target),
            "parameters_hash": parameters_hash,
            "parameter_persistence": PARAMETER_PERSISTENCE,
            "created_at": created_at,
            "receipt": receipt,
            "idempotent": False,
            "execution_decision": "HOLD",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_intent(intent_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        _ensure_table(conn)
        row = conn.execute("SELECT * FROM institutional_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            return None
        return {
            "intent_id": row["intent_id"],
            "intent_hash": row["intent_hash"],
            "status": row["status"],
            "principal_id": row["principal_id"],
            "requested_operation": row["requested_operation"],
            "target": {"kind": row["target_kind"], "id": row["target_id"]},
            "parameters_hash": row["parameters_hash"],
            "parameter_persistence": PARAMETER_PERSISTENCE,
            "source": json.loads(row["source_json"]),
            "created_at": row["created_at"],
            "receipt": json.loads(row["receipt_json"]),
            "execution_decision": "HOLD",
        }
    finally:
        conn.close()


def list_intents_for_principal(principal_id: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        _ensure_table(conn)
        rows = conn.execute("SELECT intent_id FROM institutional_intents WHERE principal_id=? ORDER BY created_at,intent_id", (principal_id,)).fetchall()
    finally:
        conn.close()
    return [item for row in rows if (item := get_intent(row["intent_id"])) is not None]
