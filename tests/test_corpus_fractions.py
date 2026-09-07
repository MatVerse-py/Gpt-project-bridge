from __future__ import annotations

from app.corpus_fractions import build_fraction_plan
from app.evidence import sha256_text


def members(count: int):
    return [
        {
            "ordinal": index,
            "document_id": f"doc-{index:03d}",
            "conversation_id": f"conv-{index:03d}",
            "source_hash": sha256_text(f"source-{index}"),
        }
        for index in range(1, count + 1)
    ]


def test_canonical_51_item_fraction_plan():
    plan = build_fraction_plan(members(51), fraction_size=14)
    assert [fraction.member_count for fraction in plan.fractions] == [14, 14, 14, 9]
    assert [(fraction.start_ordinal, fraction.end_ordinal) for fraction in plan.fractions] == [
        (1, 14),
        (15, 28),
        (29, 42),
        (43, 51),
    ]
    assert len(plan.merge_root) == 64


def test_fraction_merge_root_changes_when_member_source_changes():
    baseline_members = members(20)
    changed_members = members(20)
    changed_members[15] = {**changed_members[15], "source_hash": sha256_text("changed-source")}

    baseline = build_fraction_plan(baseline_members, fraction_size=14)
    changed = build_fraction_plan(changed_members, fraction_size=14)

    assert baseline.merge_root != changed.merge_root
    assert baseline.fractions[0].manifest_hash == changed.fractions[0].manifest_hash
    assert baseline.fractions[1].manifest_hash != changed.fractions[1].manifest_hash
