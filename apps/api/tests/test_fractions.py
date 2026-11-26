from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from projectvault.db import Database
from projectvault.fractions import FRACTION_PROTOCOL, build_fraction_plan
from projectvault.ingest import ingest_export


def members(count: int) -> list[dict[str, object]]:
    return [
        {
            "ordinal": index,
            "document_id": f"chat:conv-{index}",
            "conversation_id": f"conv-{index}",
            "source_hash": f"{index:064x}",
        }
        for index in range(1, count + 1)
    ]


def export_with_conversations(path: Path, count: int) -> None:
    payload = [
        {
            "id": f"conv-{index}",
            "title": f"Conversation {index}",
            "create_time": float(index),
            "update_time": float(index),
            "project_id": "patente",
            "project_name": "PATENTE",
            "messages": [
                {
                    "id": f"m-{index}",
                    "role": "user",
                    "create_time": float(index),
                    "content": {"parts": [f"content-{index}"]},
                }
            ],
        }
        for index in range(1, count + 1)
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps(payload, ensure_ascii=False))


def test_51_items_are_partitioned_as_14_14_14_9() -> None:
    plan = build_fraction_plan(members(51), fraction_size=14)

    assert plan.protocol == FRACTION_PROTOCOL
    assert plan.total_items == 51
    assert plan.fraction_count == 4
    assert [fraction.member_count for fraction in plan.fractions] == [14, 14, 14, 9]
    assert [(fraction.start_ordinal, fraction.end_ordinal) for fraction in plan.fractions] == [
        (1, 14),
        (15, 28),
        (29, 42),
        (43, 51),
    ]
    assert len(plan.merge_root) == 64
    assert all(len(fraction.manifest_hash) == 64 for fraction in plan.fractions)


def test_merge_root_changes_when_a_member_hash_changes() -> None:
    baseline = members(51)
    mutated = members(51)
    mutated[27] = {**mutated[27], "source_hash": "f" * 64}

    baseline_plan = build_fraction_plan(baseline, fraction_size=14)
    mutated_plan = build_fraction_plan(mutated, fraction_size=14)

    assert baseline_plan.merge_root != mutated_plan.merge_root
    assert baseline_plan.fractions[0].manifest_hash == mutated_plan.fractions[0].manifest_hash
    assert baseline_plan.fractions[1].manifest_hash != mutated_plan.fractions[1].manifest_hash


def test_fraction_plan_rejects_non_contiguous_ordinals() -> None:
    broken = members(3)
    broken[1] = {**broken[1], "ordinal": 9}

    with pytest.raises(ValueError, match="contiguous"):
        build_fraction_plan(broken, fraction_size=14)


def test_ingestion_persists_fraction_lineage_and_merge_root(tmp_path: Path) -> None:
    export = tmp_path / "patente-51.zip"
    export_with_conversations(export, 51)
    db = Database(tmp_path / "vault.db")

    run = ingest_export(db, export)

    assert run["imported_documents"] == 51
    metadata = run["metadata"]
    assert metadata["fraction_protocol"] == FRACTION_PROTOCOL
    assert metadata["fraction_size"] == 14
    assert metadata["fraction_count"] == 4
    assert [item["member_count"] for item in metadata["fractions"]] == [14, 14, 14, 9]
    assert len(metadata["fraction_merge_root"]) == 64

    first = db.fetch("chat:conv-1")
    fifteenth = db.fetch("chat:conv-15")
    last = db.fetch("chat:conv-51")
    assert first is not None and fifteenth is not None and last is not None

    first_metadata = json.loads(first["metadata_json"])
    fifteenth_metadata = json.loads(fifteenth["metadata_json"])
    last_metadata = json.loads(last["metadata_json"])

    assert (first_metadata["corpus_ordinal"], first_metadata["fraction_index"]) == (1, 1)
    assert (fifteenth_metadata["corpus_ordinal"], fifteenth_metadata["fraction_index"]) == (15, 2)
    assert (last_metadata["corpus_ordinal"], last_metadata["fraction_index"]) == (51, 4)


def test_fraction_size_is_configurable_without_changing_protocol(tmp_path: Path) -> None:
    export = tmp_path / "eleven.zip"
    export_with_conversations(export, 11)
    db = Database(tmp_path / "vault.db")

    run = ingest_export(db, export, fraction_size=5)

    assert run["metadata"]["fraction_protocol"] == FRACTION_PROTOCOL
    assert [item["member_count"] for item in run["metadata"]["fractions"]] == [5, 5, 1]
