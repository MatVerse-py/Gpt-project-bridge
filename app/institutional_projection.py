from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
import re
from typing import Any

from .institutional_contract import validate_projection_semantics
from .institutional_state_client import InstitutionalStateUnavailable, fetch_state_snapshot, remote_state_enabled
from .organism_loop import constitutional_contract_hash, gate_fingerprint
from .storage import _connect


_REPOSITORY = "MatVerse-py/Gpt-project-bridge"
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_INTEGER = 9_007_199_254_740_991


class ProjectionUnavailable(RuntimeError):
    pass


def _valid_unicode_string(value: str, path: str) -> None:
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise ValueError(f"lone UTF-16 surrogate is not valid JCS input at {path}")


def _assert_jcs_subset(value: Any, path: str = "$") -> None:
    """Restrict v1 payloads to a deterministic RFC 8785-compatible subset.

    Integers are restricted to the IEEE-754 interoperable safe range. JSON
    numbers parsed as Python floats are accepted only when finite, integral and
    inside that same range; they canonicalize to the corresponding integer.
    Non-integral floats remain forbidden. Unicode strings may not contain lone
    UTF-16 surrogates.
    """

    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        _valid_unicode_string(value, path)
        return
    if isinstance(value, int):
        if abs(value) > _SAFE_INTEGER:
            raise ValueError(f"integer outside JCS interoperable range at {path}")
        return
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or abs(value) > _SAFE_INTEGER:
            raise ValueError(f"non-integral or non-interoperable floating point value at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_jcs_subset(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string object key at {path}")
            _valid_unicode_string(key, f"{path}.<key>")
            _assert_jcs_subset(item, f"{path}.{key}")
        return
    raise ValueError(f"unsupported canonical JSON type at {path}: {type(value).__qualname__}")


def _jcs_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16be")


def _jcs_subset_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value))
    if isinstance(value, list):
        return "[" + ",".join(_jcs_subset_text(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=_utf16_sort_key)
        return "{" + ",".join(_jcs_string(key) + ":" + _jcs_subset_text(value[key]) for key in keys) + "}"
    raise TypeError(f"value left validated JCS subset: {type(value).__qualname__}")


def jcs_subset_bytes(value: Any) -> bytes:
    _assert_jcs_subset(value)
    return _jcs_subset_text(value).encode("utf-8")


def jcs_subset_hash(value: Any) -> str:
    return hashlib.sha256(jcs_subset_bytes(value)).hexdigest()


def _genesis_commitment() -> str:
    return jcs_subset_hash({"ledger_head": "GENESIS", "events": 0})


def _build_binding() -> dict[str, str]:
    commit_sha = os.environ.get("MATVERSE_BUILD_COMMIT", "").lower()
    if _GIT_OBJECT_ID.fullmatch(commit_sha) is None:
        raise ProjectionUnavailable("MATVERSE_BUILD_COMMIT must be a 40- or 64-character lowercase Git object id")
    frozen_contract_hash = os.environ.get("MATVERSE_FROZEN_CONTRACT_HASH", "").lower()
    if _SHA256.fullmatch(frozen_contract_hash) is None:
        raise ProjectionUnavailable("MATVERSE_FROZEN_CONTRACT_HASH must be a lowercase SHA-256 digest")
    build_ref = os.environ.get("MATVERSE_BUILD_REF", "main")
    if not isinstance(build_ref, str) or not build_ref or len(build_ref) > 256:
        raise ProjectionUnavailable("MATVERSE_BUILD_REF must be a non-empty identifier <= 256 characters")
    _valid_unicode_string(build_ref, "MATVERSE_BUILD_REF")
    fingerprint = gate_fingerprint()
    return {
        "repository": _REPOSITORY,
        "commit_sha": commit_sha,
        "ref": build_ref,
        "frozen_contract_hash": frozen_contract_hash,
        "gate_fingerprint": fingerprint,
        "constitutional_contract_hash": constitutional_contract_hash(frozen_contract_hash=frozen_contract_hash),
    }


def _read_ledger_from_connection(conn: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM ledger ORDER BY seq").fetchall()]


def _verify_ledger_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prev = "GENESIS"
    for row in rows:
        expected = hashlib.sha256((prev + row["event_json"] + row["decision"]).encode("utf-8")).hexdigest()
        if row["prev_hash"] != prev or row["event_hash"] != expected:
            return {"ok": False, "failed_seq": row["seq"]}
        prev = row["event_hash"]
    return {"ok": True, "events": len(rows), "head": prev}


def _event_source_commit(event: dict[str, Any], seq: int) -> str:
    source_commit = event.get("source_commit")
    if not isinstance(source_commit, str) or _GIT_OBJECT_ID.fullmatch(source_commit.lower()) is None:
        raise ProjectionUnavailable(
            f"ledger event seq={seq} lacks immutable originating source_commit; provenance migration required"
        )
    return source_commit.lower()


def _project_contract_artifacts(rows: list[dict[str, Any]], current_commit: str, ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    receipts_by_artifact: dict[str, dict[str, str]] = {}
    for row in ledger:
        try:
            event = json.loads(row["event_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if event.get("event_type") != "CONTRACT_ARTIFACT_REGISTERED":
            continue
        artifact_hash = event.get("artifact_hash")
        if isinstance(artifact_hash, str):
            source_commit = _event_source_commit(event, int(row["seq"]))
            receipts_by_artifact[artifact_hash] = {
                "evidence_id": f"ledger:{row['seq']}",
                "receipt_hash": row["event_hash"],
                "source_commit": source_commit,
            }

    projected: list[dict[str, Any]] = []
    for row in rows:
        artifact_hash = row["artifact_hash"]
        evidence = receipts_by_artifact.get(artifact_hash)
        projected.append(
            {
                "artifact_id": f"contract:{artifact_hash}",
                "kind": f"contract-registry/{row['kind']}/{row['version']}",
                "content_hash": artifact_hash,
                "source_commit": evidence["source_commit"] if evidence is not None else current_commit,
                "status": "PASS" if evidence is not None else "HOLD",
                "evidence": [evidence] if evidence is not None else [],
            }
        )
    return projected


def _list_contract_artifacts(conn: Any, current_commit: str, ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in conn.execute("SELECT artifact_hash,kind,version FROM contract_artifacts ORDER BY artifact_hash").fetchall()
    ]
    return _project_contract_artifacts(rows, current_commit, ledger)


def _project_receipts(current_commit: str, ledger: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not ledger:
        return [
            {
                "receipt_id": "ledger:GENESIS",
                "receipt_hash": _genesis_commitment(),
                "receipt_type": "LEDGER_GENESIS_COMMITMENT",
                "source_commit": current_commit,
            }
        ]
    output: list[dict[str, str]] = []
    for row in ledger:
        receipt_type = "LEDGER_EVENT"
        try:
            event = json.loads(row["event_json"])
            if isinstance(event.get("event_type"), str):
                receipt_type = event["event_type"]
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProjectionUnavailable(f"ledger event seq={row['seq']} is not parseable JSON") from exc
        output.append(
            {
                "receipt_id": f"ledger:{row['seq']}",
                "receipt_hash": row["event_hash"],
                "receipt_type": receipt_type,
                "source_commit": _event_source_commit(event, int(row["seq"])),
            }
        )
    return output


def _projection_time(ledger: list[dict[str, Any]]) -> str:
    for row in reversed(ledger):
        try:
            event = json.loads(row["event_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for field in ("ledger_at", "accepted_at", "acked_at", "created_at"):
            value = event.get(field)
            if isinstance(value, str):
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                    return value
                except ValueError:
                    continue
    configured = os.environ.get("MATVERSE_BUILD_TIMESTAMP", "")
    if configured:
        try:
            datetime.fromisoformat(configured.replace("Z", "+00:00"))
            return configured
        except ValueError as exc:
            raise ProjectionUnavailable("MATVERSE_BUILD_TIMESTAMP must be ISO-8601 when configured") from exc
    return "1970-01-01T00:00:00+00:00"


def _build_projection(ledger: list[dict[str, Any]], contract_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    chain = _verify_ledger_rows(ledger)
    if not chain.get("ok"):
        raise ProjectionUnavailable(f"canonical ledger integrity failure at seq={chain.get('failed_seq')}")

    source = _build_binding()
    source_receipt = chain.get("head")
    if not isinstance(source_receipt, str) or _SHA256.fullmatch(source_receipt) is None:
        source_receipt = _genesis_commitment()

    projection: dict[str, Any] = {
        "schema_version": "matverse.institutional-surface.v1",
        "projection_policy": {
            "projection_only": True,
            "write_authority": "NONE",
            "allowed_operations": ["READ", "LIST", "FILTER", "SEARCH", "RENDER", "EXPORT_PROJECTION", "CREATE_INTENT"],
            "forbidden_operations": ["MUTATE_OMEGA", "APPEND_LEDGER", "FORGE_RECEIPT", "AUTHORIZE_CONSTRAINT", "PROMOTE_MATURITY", "ALTER_CONSTITUTION", "ALTER_CONTRACT", "WRITE_CANONICAL_STATE"],
        },
        "source": source,
        "subjects": [],
        "authority_traces": [],
        "maturity": [],
        "artifacts": _project_contract_artifacts(contract_artifacts, source["commit_sha"], ledger),
        "claims": [],
        "experiments": [],
        "relations": [],
        "receipts": _project_receipts(source["commit_sha"], ledger),
        "projection": {
            "generated_at": _projection_time(ledger),
            "projection_hash": "0" * 64,
            "source_receipt": source_receipt,
            "freshness": "LIVE",
            "hash_algorithm": "SHA-256",
            "canonicalization": "RFC8785_JCS",
            "hash_excludes": ["projection.projection_hash"],
        },
    }

    payload = deepcopy(projection)
    payload["projection"].pop("projection_hash")
    projection["projection"]["projection_hash"] = jcs_subset_hash(payload)

    validation = validate_projection_semantics(projection)
    if not validation.ok:
        raise ProjectionUnavailable("generated projection failed semantic validation: " + "; ".join(validation.errors))
    return projection


def build_institutional_projection_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    ledger = snapshot.get("ledger")
    contract_artifacts = snapshot.get("contract_artifacts")
    if not isinstance(ledger, list) or not all(isinstance(row, dict) for row in ledger):
        raise ProjectionUnavailable("institutional state snapshot ledger is invalid")
    if not isinstance(contract_artifacts, list) or not all(isinstance(row, dict) for row in contract_artifacts):
        raise ProjectionUnavailable("institutional state snapshot contract_artifacts is invalid")
    return _build_projection(ledger, contract_artifacts)


def build_institutional_projection_from_connection(conn: Any) -> dict[str, Any]:
    """Build a projection from one caller-owned SQLite snapshot/transaction."""

    ledger = _read_ledger_from_connection(conn)
    artifacts = [
        dict(row)
        for row in conn.execute("SELECT artifact_hash,kind,version FROM contract_artifacts ORDER BY artifact_hash").fetchall()
    ]
    return _build_projection(ledger, artifacts)


def build_institutional_projection() -> dict[str, Any]:
    if remote_state_enabled():
        try:
            return build_institutional_projection_from_snapshot(fetch_state_snapshot())
        except InstitutionalStateUnavailable as exc:
            raise ProjectionUnavailable(str(exc)) from exc

    conn = _connect()
    try:
        # One read transaction guarantees Ledger, registry artifacts, source
        # receipt and projection hash are observed from the same WAL snapshot.
        conn.execute("BEGIN")
        return build_institutional_projection_from_connection(conn)
    finally:
        conn.rollback()
        conn.close()
