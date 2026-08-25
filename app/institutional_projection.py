from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
import re
from typing import Any

from .organism_loop import constitutional_contract_hash, gate_fingerprint
from .storage import _connect, read_ledger, verify_chain


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
    """Restrict v1 payloads to an interoperable RFC 8785 subset.

    Floating point values are intentionally rejected. Integers are restricted
    to the IEEE-754 interoperable safe range. Unicode strings may not contain
    lone UTF-16 surrogates. These restrictions remove cross-runtime ambiguity
    while preserving normal institutional JSON data.
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
        raise ValueError(f"floating point values are not allowed in institutional v1 canonical payloads at {path}")
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
    # Python's JSON string escaping matches JSON.stringify for the accepted
    # Unicode subset. Object ordering is handled separately using UTF-16 units.
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


def _build_binding() -> dict[str, str]:
    commit_sha = os.environ.get("MATVERSE_BUILD_COMMIT", "").lower()
    if _GIT_OBJECT_ID.fullmatch(commit_sha) is None:
        raise ProjectionUnavailable("MATVERSE_BUILD_COMMIT must be a 40- or 64-character lowercase Git object id")
    frozen_contract_hash = os.environ.get("MATVERSE_FROZEN_CONTRACT_HASH", "").lower()
    if _SHA256.fullmatch(frozen_contract_hash) is None:
        raise ProjectionUnavailable("MATVERSE_FROZEN_CONTRACT_HASH must be a lowercase SHA-256 digest")
    fingerprint = gate_fingerprint()
    return {
        "repository": _REPOSITORY,
        "commit_sha": commit_sha,
        "ref": os.environ.get("MATVERSE_BUILD_REF", "main"),
        "frozen_contract_hash": frozen_contract_hash,
        "gate_fingerprint": fingerprint,
        "constitutional_contract_hash": constitutional_contract_hash(frozen_contract_hash=frozen_contract_hash),
    }


def _list_contract_artifacts(commit_sha: str, ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            receipts_by_artifact[artifact_hash] = {
                "evidence_id": f"ledger:{row['seq']}",
                "receipt_hash": row["event_hash"],
                "source_commit": commit_sha,
            }

    conn = _connect()
    try:
        rows = conn.execute("SELECT artifact_hash,kind,version FROM contract_artifacts ORDER BY artifact_hash").fetchall()
    finally:
        conn.close()

    projected: list[dict[str, Any]] = []
    for row in rows:
        evidence = receipts_by_artifact.get(row["artifact_hash"])
        projected.append(
            {
                "artifact_id": f"contract:{row['artifact_hash']}",
                "kind": f"contract/{row['kind']}/{row['version']}",
                "content_hash": row["artifact_hash"],
                "source_commit": commit_sha,
                "status": "PASS" if evidence is not None else "HOLD",
                "evidence": [evidence] if evidence is not None else [],
            }
        )
    return projected


def _project_receipts(commit_sha: str, ledger: list[dict[str, Any]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in ledger:
        receipt_type = "LEDGER_EVENT"
        try:
            event = json.loads(row["event_json"])
            if isinstance(event.get("event_type"), str):
                receipt_type = event["event_type"]
        except (TypeError, json.JSONDecodeError):
            receipt_type = "LEDGER_EVENT_UNPARSEABLE"
        output.append(
            {
                "receipt_id": f"ledger:{row['seq']}",
                "receipt_hash": row["event_hash"],
                "receipt_type": receipt_type,
                "source_commit": commit_sha,
            }
        )
    return output


def _projection_time(ledger: list[dict[str, Any]]) -> str:
    for row in reversed(ledger):
        try:
            event = json.loads(row["event_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for field in ("accepted_at", "acked_at", "created_at"):
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


def build_institutional_projection() -> dict[str, Any]:
    chain = verify_chain()
    if not chain.get("ok"):
        raise ProjectionUnavailable(f"canonical ledger integrity failure at seq={chain.get('failed_seq')}")

    source = _build_binding()
    ledger = read_ledger()
    source_receipt = chain.get("head")
    if not isinstance(source_receipt, str) or _SHA256.fullmatch(source_receipt) is None:
        source_receipt = jcs_subset_hash({"ledger_head": "GENESIS", "events": 0})

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
        "artifacts": _list_contract_artifacts(source["commit_sha"], ledger),
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
    return projection
