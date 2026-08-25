from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "institutional-surface-v1.schema.json"
DOC = ROOT / "docs" / "MANUS_INSTITUTIONAL_SURFACE_CONTRACT_V1.md"
UI_DOC = ROOT / "docs" / "MANUS_UI_BINDING_V1.md"


def _schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def test_institutional_surface_is_projection_only_and_has_no_write_authority():
    schema = _schema()
    policy = schema["properties"]["projection_policy"]["properties"]
    assert policy["projection_only"]["const"] is True
    assert policy["write_authority"]["const"] == "NONE"

    forbidden = set(policy["forbidden_operations"]["items"]["enum"])
    assert {
        "MUTATE_OMEGA",
        "APPEND_LEDGER",
        "FORGE_RECEIPT",
        "AUTHORIZE_CONSTRAINT",
        "PROMOTE_MATURITY",
        "ALTER_CONSTITUTION",
        "ALTER_CONTRACT",
        "WRITE_CANONICAL_STATE",
    } <= forbidden


def test_researcher_subject_identifiers_require_validator_and_witness():
    schema = _schema()
    assert "subjects" in schema["required"]
    identifier = schema["$defs"]["verifiedIdentifier"]
    assert {"scheme", "value", "decision", "validator_id", "witness"} <= set(identifier["required"])
    assert identifier["properties"]["witness"] == {"$ref": "#/$defs/evidencePointer"}
    assert {"ORCID", "GITHUB", "HUGGING_FACE", "ZENODO"} <= set(identifier["properties"]["scheme"]["enum"])


def test_maturity_requires_validator_evidence_and_scientific_pass_is_explicit():
    schema = _schema()
    maturity = schema["$defs"]["maturityState"]
    assert {"object_id", "gate", "decision", "validator_id", "evidence"} <= set(maturity["required"])
    assert maturity["properties"]["evidence"]["minItems"] == 1
    assert "SCIENTIFIC_PASS" in maturity["properties"]["gate"]["enum"]
    assert "INDEPENDENT_REPLICATION_PASS" in maturity["properties"]["gate"]["enum"]


def test_relations_require_independent_witness_pointer():
    schema = _schema()
    relation = schema["$defs"]["relation"]
    assert "witness" in relation["required"]
    assert relation["properties"]["witness"] == {"$ref": "#/$defs/evidencePointer"}


def test_source_binding_is_pinned_to_canonical_repository_and_constitutional_hashes():
    schema = _schema()
    source = schema["$defs"]["sourceBinding"]
    assert source["properties"]["repository"]["const"] == "MatVerse-py/Gpt-project-bridge"
    assert {"commit_sha", "frozen_contract_hash", "gate_fingerprint", "constitutional_contract_hash"} <= set(source["required"])


def test_document_forbids_local_ui_state_from_becoming_canonical_truth():
    text = DOC.read_text(encoding="utf-8")
    assert "read model/cache only" in text
    assert "MUST NOT" in text
    assert "MaturityTransition requires ValidatorEvidence" in text
    assert "EntityIntegrity != RelationIntegrity" in text


def test_ui_binding_demotes_badges_and_actions_to_projection_or_intent():
    text = UI_DOC.read_text(encoding="utf-8")
    assert "The presence of an identifier string or outbound URL is not equivalent to verification" in text
    assert "A badge MUST NOT be generated from a local boolean" in text
    assert "UI interaction -> CREATE_INTENT -> canonical runtime -> gate -> execution -> receipt -> refreshed projection" in text
    assert "The UI does not need to be rebuilt. The authority model underneath it does." in text
