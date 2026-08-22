from app.evidence import canonical_json, evidence_receipt, merkle_root, sha256_text


def test_evidence_receipt_is_deterministic_for_same_event():
    first = evidence_receipt("X", {"b": 2, "a": 1}, {"ok": True})
    second = evidence_receipt("X", {"a": 1, "b": 2}, {"ok": True})
    assert first == second
    assert len(first["receipt_hash"]) == 64
    assert first["merkle_root"] == merkle_root([first["input_hash"], first["output_hash"]])


def test_canonical_json_stabilizes_mapping_order():
    assert sha256_text(canonical_json({"b": 2, "a": 1})) == sha256_text(canonical_json({"a": 1, "b": 2}))
