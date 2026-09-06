import json
from pathlib import Path


REGISTER = Path("evidence/corpus_risk_register.v1.json")


def load_register():
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def test_corpus_risk_register_schema_and_unique_ids():
    data = load_register()
    assert data["schema"] == "matverse.corpus-risk-register.v1"
    ids = [item["id"] for item in data["risks"]]
    assert len(ids) == len(set(ids))
    assert all(item["severity"] in {"P0", "P1", "P2"} for item in data["risks"])


def test_no_risk_allows_automatic_promotion():
    data = load_register()
    assert data["risks"]
    assert all(item["automatic_promotion"] is False for item in data["risks"])


def test_promotion_order_never_skips_external_witness_before_scientific_validation():
    data = load_register()
    order = data["promotion_order"]
    assert order.index("PASS_LOCAL") < order.index("REPLAYED")
    assert order.index("REPLAYED") < order.index("WITNESSED_EXTERNAL")
    assert order.index("WITNESSED_EXTERNAL") < order.index("SCIENTIFICALLY_VALIDATED")


def test_unresolved_terms_remain_hold_and_no_silent_merge():
    data = load_register()
    policy = set(data["unresolved_term_policy"]["states"])
    assert {"PRESERVE", "HOLD_DEFINITION", "SOURCE_RECOVERY_REQUIRED", "NO_SILENT_MERGE"} <= policy
    assert data["unresolved_term_policy"]["terms"]


def test_forbidden_equivalences_cover_core_semantic_boundaries():
    data = load_register()
    rules = set(data["forbidden_equivalences"])
    assert "ARGUS=ARGOS" in rules
    assert "OSX=SymbiOS" in rules
    assert "MNB=MMNB" in rules
    assert "hash=truth" in rules
    assert "simulation=measurement" in rules
