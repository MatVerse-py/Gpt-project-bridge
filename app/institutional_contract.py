from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_ALLOWED_OPERATIONS = [
    "READ",
    "LIST",
    "FILTER",
    "SEARCH",
    "RENDER",
    "EXPORT_PROJECTION",
    "CREATE_INTENT",
]
_FORBIDDEN_OPERATIONS = [
    "MUTATE_OMEGA",
    "APPEND_LEDGER",
    "FORGE_RECEIPT",
    "AUTHORIZE_CONSTRAINT",
    "PROMOTE_MATURITY",
    "ALTER_CONSTITUTION",
    "ALTER_CONTRACT",
    "WRITE_CANONICAL_STATE",
]


@dataclass(frozen=True)
class ContractValidation:
    decision: str
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.decision == "PASS"


def _is_evidence_pointer(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("evidence_id"), str)
        and isinstance(value.get("receipt_hash"), str)
        and bool(_SHA256.fullmatch(value["receipt_hash"]))
        and isinstance(value.get("source_commit"), str)
        and bool(_GIT_OBJECT_ID.fullmatch(value["source_commit"]))
    )


def _has_evidence(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(_is_evidence_pointer(item) for item in value)


def validate_projection_semantics(payload: dict[str, Any]) -> ContractValidation:
    """Validate semantic invariants JSON Schema cannot express by itself.

    This validator is intentionally fail-closed. It does not promote maturity; it
    only decides whether an institutional projection is internally admissible.
    """

    errors: list[str] = []

    policy = payload.get("projection_policy")
    if not isinstance(policy, dict):
        errors.append("projection_policy missing")
    else:
        if policy.get("projection_only") is not True:
            errors.append("projection_only must be true")
        if policy.get("write_authority") != "NONE":
            errors.append("institutional surface must have no canonical write authority")
        if policy.get("allowed_operations") != _ALLOWED_OPERATIONS:
            errors.append("allowed_operations mismatch")
        if policy.get("forbidden_operations") != _FORBIDDEN_OPERATIONS:
            errors.append("forbidden_operations mismatch")

    source = payload.get("source")
    if not isinstance(source, dict):
        errors.append("source binding missing")
    else:
        if source.get("repository") != "MatVerse-py/Gpt-project-bridge":
            errors.append("source repository mismatch")
        commit_sha = source.get("commit_sha")
        if not isinstance(commit_sha, str) or not _GIT_OBJECT_ID.fullmatch(commit_sha):
            errors.append("source commit is not a supported Git object id")
        for field in ("frozen_contract_hash", "gate_fingerprint", "constitutional_contract_hash"):
            value = source.get(field)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                errors.append(f"{field} must be a lowercase SHA-256 digest")

    traces_raw = payload.get("authority_traces", [])
    traces: dict[str, dict[str, Any]] = {}
    if not isinstance(traces_raw, list):
        errors.append("authority_traces must be an array")
    else:
        for trace in traces_raw:
            if not isinstance(trace, dict) or not isinstance(trace.get("trace_id"), str):
                errors.append("authority trace missing trace_id")
                continue
            trace_id = trace["trace_id"]
            if trace_id in traces:
                errors.append(f"duplicate authority trace: {trace_id}")
                continue
            traces[trace_id] = trace
            if trace.get("proposer_id") == trace.get("final_validator_id"):
                errors.append(f"authority trace {trace_id}: proposer must differ from final validator")
            if trace.get("generator_id") is not None and trace.get("generator_id") == trace.get("authorizer_id"):
                errors.append(f"authority trace {trace_id}: generator must differ from authorizer")
            if trace.get("executor_id") is not None and trace.get("executor_id") == trace.get("evidence_producer_id"):
                errors.append(f"authority trace {trace_id}: executor must differ from evidence producer")
            if trace.get("decision") == "PASS" and not _has_evidence(trace.get("evidence")):
                errors.append(f"authority trace {trace_id}: PASS requires evidence")

    for maturity in payload.get("maturity", []):
        if not isinstance(maturity, dict):
            errors.append("invalid maturity entry")
            continue
        object_id = maturity.get("object_id", "<unknown>")
        trace_id = maturity.get("authority_trace_id")
        trace = traces.get(trace_id) if isinstance(trace_id, str) else None
        if trace is None:
            errors.append(f"maturity {object_id}: authority trace missing")
        elif maturity.get("validator_id") != trace.get("final_validator_id"):
            errors.append(f"maturity {object_id}: validator does not match authority trace")
        if maturity.get("decision") == "PASS" and not _has_evidence(maturity.get("evidence")):
            errors.append(f"maturity {object_id}: PASS requires evidence")
        if maturity.get("gate") == "SCIENTIFIC_PASS" and maturity.get("target_kind") != "CLAIM":
            errors.append(f"maturity {object_id}: SCIENTIFIC_PASS is claim-scoped")

    for subject in payload.get("subjects", []):
        if not isinstance(subject, dict):
            errors.append("invalid subject entry")
            continue
        for identifier in subject.get("identifiers", []):
            if isinstance(identifier, dict) and identifier.get("decision") == "PASS" and not _is_evidence_pointer(identifier.get("witness")):
                errors.append(f"subject {subject.get('subject_id', '<unknown>')}: verified identifier requires witness")

    for collection, id_field, decision_field in (
        ("artifacts", "artifact_id", "status"),
        ("claims", "claim_id", "decision"),
        ("experiments", "experiment_id", "status"),
    ):
        for item in payload.get(collection, []):
            if not isinstance(item, dict):
                errors.append(f"invalid {collection} entry")
                continue
            if item.get(decision_field) == "PASS" and not _has_evidence(item.get("evidence")):
                errors.append(f"{collection[:-1]} {item.get(id_field, '<unknown>')}: PASS requires evidence")

    for relation in payload.get("relations", []):
        if not isinstance(relation, dict):
            errors.append("invalid relation entry")
            continue
        if relation.get("decision") == "PASS" and not _is_evidence_pointer(relation.get("witness")):
            errors.append(f"relation {relation.get('relation_id', '<unknown>')}: PASS requires witness")

    projection = payload.get("projection")
    if not isinstance(projection, dict):
        errors.append("projection metadata missing")
    else:
        if projection.get("hash_algorithm") != "SHA-256":
            errors.append("projection hash algorithm mismatch")
        if projection.get("canonicalization") != "RFC8785_JCS":
            errors.append("projection canonicalization mismatch")
        if projection.get("hash_excludes") != ["projection.projection_hash"]:
            errors.append("projection hash exclusion mismatch")
        value = projection.get("projection_hash")
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            errors.append("projection_hash must be a lowercase SHA-256 digest")

    return ContractValidation("PASS" if not errors else "BLOCK", tuple(errors))
