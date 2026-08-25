from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.institutional_contract import validate_projection_semantics


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "institutional-surface-v1.schema.json"
INTENT_SCHEMA = ROOT / "contracts" / "institutional-intent-v1.schema.json"
DOC = ROOT / "docs" / "MANUS_INSTITUTIONAL_SURFACE_CONTRACT_V1.md"
UI_DOC = ROOT / "docs" / "MANUS_UI_BINDING_V1.md"


def _schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def _intent_schema() -> dict:
    return json.loads(INTENT_SCHEMA.read_text(encoding="utf-8"))


def _evidence() -> dict:
    return {
        "evidence_id": "ev-1",
        "receipt_hash": "a" * 64,
        "source_commit": "b" * 40,
    }


def _projection() -> dict:
    evidence = _evidence()
    return {
        "schema_version": "matverse.institutional-surface.v1",
        "projection_policy": {
            "projection_only": True,
            "write_authority": "NONE",
            "allowed_operations": ["READ", "LIST", "FILTER", "SEARCH", "RENDER", "EXPORT_PROJECTION", "CREATE_INTENT"],
            "forbidden_operations": [
                "MUTATE_OMEGA",
                "APPEND_LEDGER",
                "FORGE_RECEIPT",
                "AUTHORIZE_CONSTRAINT",
                "PROMOTE_MATURITY",
                "ALTER_CONSTITUTION",
                "ALTER_CONTRACT",
                "WRITE_CANONICAL_STATE",
            ],
        },
        "source": {
            "repository": "MatVerse-py/Gpt-project-bridge",
            "commit_sha": "c" * 40,
            "ref": "main",
            "frozen_contract_hash": "d" * 64,
            "gate_fingerprint": "e" * 64,
            "constitutional_contract_hash": "f" * 64,
        },
        "subjects": [],
        "authority_traces": [
            {
                "trace_id": "auth-1",
                "proposer_id": "proposer",
                "executor_id": "executor",
                "evidence_producer_id": "witness",
                "final_validator_id": "validator",
                "decision": "PASS",
                "evidence": [evidence],
            }
        ],
        "maturity": [
            {
                "target_kind": "SYSTEM",
                "object_id": "matverse-core",
                "gate": "IMPLEMENTATION_PASS",
                "decision": "PASS",
                "validator_id": "validator",
                "authority_trace_id": "auth-1",
                "evidence": [evidence],
            }
        ],
        "artifacts": [],
        "claims": [],
        "experiments": [],
        "relations": [],
        "receipts": [],
        "projection": {
            "generated_at": "2026-08-25T21:00:00Z",
            "projection_hash": "1" * 64,
            "source_receipt": "2" * 64,
            "freshness": "LIVE",
            "hash_algorithm": "SHA-256",
            "canonicalization": "RFC8785_JCS",
            "hash_excludes": ["projection.projection_hash"],
        },
    }


def test_institutional_surface_is_projection_only_and_policy_sets_are_mandatory():
    schema = _schema()
    policy = schema["properties"]["projection_policy"]["properties"]
    assert policy["projection_only"]["const"] is True
    assert policy["write_authority"]["const"] == "NONE"
    assert policy["allowed_operations"]["const"] == ["READ", "LIST", "FILTER", "SEARCH", "RENDER", "EXPORT_PROJECTION", "CREATE_INTENT"]
    assert policy["forbidden_operations"]["const"] == [
        "MUTATE_OMEGA",
        "APPEND_LEDGER",
        "FORGE_RECEIPT",
        "AUTHORIZE_CONSTRAINT",
        "PROMOTE_MATURITY",
        "ALTER_CONSTITUTION",
        "ALTER_CONTRACT",
        "WRITE_CANONICAL_STATE",
    ]


def test_git_commit_identity_is_distinct_from_sha256_content_digest():
    schema = _schema()
    assert schema["$defs"]["gitObjectId"]["pattern"] == "^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
    assert schema["$defs"]["sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert schema["$defs"]["sourceBinding"]["properties"]["commit_sha"] == {"$ref": "#/$defs/gitObjectId"}
    assert schema["$defs"]["evidencePointer"]["properties"]["source_commit"] == {"$ref": "#/$defs/gitObjectId"}


def test_intent_has_separate_machine_readable_noncanonical_contract():
    schema = _intent_schema()
    assert schema["properties"]["schema_version"]["const"] == "matverse.institutional-intent.v1"
    assert {"intent_id", "requested_operation", "actor_id", "target", "parameters", "created_at", "source", "intent_hash"} <= set(schema["required"])
    assert schema["properties"]["hash_excludes"]["const"] == ["intent_hash"]
    assert schema["$defs"]["sourceBinding"]["properties"]["projection_hash"] == {"$ref": "#/$defs/sha256"}


def test_pass_claim_and_experiment_require_evidence_conditionally():
    schema = _schema()
    for definition_name in ("claim", "experiment", "artifact"):
        definition = schema["$defs"][definition_name]
        serialized = json.dumps(definition["allOf"], sort_keys=True)
        assert '"PASS"' in serialized
        assert '"minItems": 1' in serialized


def test_scientific_pass_is_structurally_claim_scoped():
    schema = _schema()
    maturity = schema["$defs"]["maturityState"]
    serialized = json.dumps(maturity["allOf"], sort_keys=True)
    assert "SCIENTIFIC_PASS" in serialized
    assert '"target_kind": {"const": "CLAIM"}' in serialized


def test_relation_witness_is_required_only_for_pass_so_hold_is_representable():
    schema = _schema()
    relation = schema["$defs"]["relation"]
    assert "witness" not in relation["required"]
    serialized = json.dumps(relation["allOf"], sort_keys=True)
    assert '"PASS"' in serialized
    assert '"required": ["witness"]' in serialized


def test_verified_identifier_requires_witness_only_when_promoted_to_pass():
    schema = _schema()
    identifier = schema["$defs"]["verifiedIdentifier"]
    assert "witness" not in identifier["required"]
    assert {"ORCID", "GITHUB", "HUGGING_FACE", "ZENODO"} <= set(identifier["properties"]["scheme"]["enum"])
    serialized = json.dumps(identifier["allOf"], sort_keys=True)
    assert '"required": ["witness"]' in serialized


def test_projection_hash_rule_is_non_self_referential_and_cross_language_defined():
    schema = _schema()
    projection = schema["$defs"]["projectionMeta"]["properties"]
    assert projection["hash_algorithm"]["const"] == "SHA-256"
    assert projection["canonicalization"]["const"] == "RFC8785_JCS"
    assert projection["hash_excludes"]["const"] == ["projection.projection_hash"]


def test_authority_trace_fields_exist_for_semantic_separation_validation():
    schema = _schema()
    trace = schema["$defs"]["authorityTrace"]
    assert {"trace_id", "proposer_id", "final_validator_id", "decision", "evidence"} <= set(trace["required"])
    assert {"executor_id", "generator_id", "authorizer_id", "evidence_producer_id", "promoter_id"} <= set(trace["properties"])
    assert "authority_traces" in schema["required"]
    assert "authority_trace_id" in schema["$defs"]["maturityState"]["required"]


def test_semantic_validator_accepts_admissible_projection():
    result = validate_projection_semantics(_projection())
    assert result.decision == "PASS"
    assert result.errors == ()


def test_semantic_validator_blocks_self_validation_and_execution_as_evidence():
    payload = _projection()
    payload["authority_traces"][0]["final_validator_id"] = "proposer"
    payload["authority_traces"][0]["evidence_producer_id"] = "executor"
    payload["maturity"][0]["validator_id"] = "proposer"
    result = validate_projection_semantics(payload)
    assert result.decision == "BLOCK"
    assert any("proposer must differ from final validator" in error for error in result.errors)
    assert any("executor must differ from evidence producer" in error for error in result.errors)


def test_semantic_validator_blocks_scientific_pass_on_non_claim_target():
    payload = _projection()
    payload["maturity"][0]["gate"] = "SCIENTIFIC_PASS"
    result = validate_projection_semantics(payload)
    assert result.decision == "BLOCK"
    assert any("SCIENTIFIC_PASS is claim-scoped" in error for error in result.errors)


def test_semantic_validator_blocks_pass_without_evidence_but_allows_witnessless_hold_relation():
    payload = _projection()
    payload["maturity"][0]["evidence"] = []
    payload["relations"] = [
        {
            "relation_id": "rel-1",
            "subject_id": "subject-1",
            "predicate": "MEMBER_OF",
            "object_id": "org-1",
            "decision": "HOLD",
        }
    ]
    result = validate_projection_semantics(payload)
    assert result.decision == "BLOCK"
    assert any("PASS requires evidence" in error for error in result.errors)
    assert not any("relation rel-1" in error for error in result.errors)


def test_document_contains_exact_fail_closed_normative_rules():
    text = DOC.read_text(encoding="utf-8")
    assert "A database used by the surface is a read model/cache only." in text
    assert "A surface MUST NOT infer current authority or maturity from locally persisted UI records alone." in text
    assert "It MUST NOT silently promote a cached record to `LIVE`." in text
    assert "If the canonical source cannot be reached, the surface MUST expose `SOURCE_UNAVAILABLE` or the corresponding HOLD state." in text
    normalized = " ".join(text.split())
    for mapping in (
        "canonical source unavailable -> HOLD / SOURCE_UNAVAILABLE",
        "receipt cannot be verified -> HOLD or BLOCK according to validator result",
        "relation witness absent -> HOLD",
        "contract binding mismatch -> BLOCK",
        "unknown maturity evidence -> HOLD",
    ):
        assert " ".join(mapping.split()) in normalized


def test_ui_binding_demotes_badges_and_actions_to_projection_or_intent():
    text = UI_DOC.read_text(encoding="utf-8")
    assert "The presence of an identifier string or outbound URL is not equivalent to verification" in text
    assert "A badge MUST NOT be generated from a local boolean" in text
    assert "UI interaction -> CREATE_INTENT -> canonical runtime -> gate -> execution -> receipt -> refreshed projection" in text
    assert "The UI does not need to be rebuilt. The authority model underneath it does." in text
