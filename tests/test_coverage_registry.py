from __future__ import annotations

import pytest

from app.coverage_registry import (
    CoverageError,
    CoverageRegistry,
    CoverageState,
    MembershipStatus,
    SweepObservation,
)


def obs(name: str, content: str) -> SweepObservation:
    return SweepObservation.from_content(
        partition_id="git",
        source_uri=f"repo://matverse/{name}",
        artifact_type="file",
        content=content,
        metadata={"name": name},
    )


def test_two_zero_delta_complete_sweeps_are_required(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")
    b = obs("b.txt", "beta")

    first = registry.execute_sweep([a, b], partitions=["git"])
    assert first.new_items == 2
    assert first.zero_streak == 0
    assert not first.discovery_saturated

    second = registry.execute_sweep([a, b], partitions=["git"])
    assert second.new_items == 0
    assert second.missing_items == 0
    assert second.zero_streak == 1
    assert not second.discovery_saturated

    third = registry.execute_sweep([a, b], partitions=["git"])
    assert third.zero_streak == 2
    assert third.discovery_saturated

    # Discovery saturation is not the same as corpus closure: membership is still unresolved.
    report = registry.report(partitions=["git"])
    assert report["discovery_saturated"] is True
    assert report["coverage_complete"] is False

    for item in (a, b):
        item_id = registry.item_id(item.partition_id, item.source_uri, item.artifact_type)
        registry.set_status(item_id, state=CoverageState.INDEXED, membership_status=MembershipStatus.MEMBER)

    report = registry.report(partitions=["git"])
    assert report["coverage_complete"] is True


def test_missing_known_item_breaks_saturation_and_becomes_orphan(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")
    b = obs("b.txt", "beta")

    registry.execute_sweep([a, b], partitions=["git"])
    registry.execute_sweep([a, b], partitions=["git"])
    saturated = registry.execute_sweep([a, b], partitions=["git"])
    assert saturated.discovery_saturated

    missing = registry.execute_sweep([a], partitions=["git"])
    assert missing.missing_items == 1
    assert not missing.discovery_saturated

    b_id = registry.item_id(b.partition_id, b.source_uri, b.artifact_type)
    assert registry.get_item(b_id)["state"] == CoverageState.ORPHAN.value


def test_content_revision_is_changed_not_new(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a1 = obs("a.txt", "alpha")
    a2 = obs("a.txt", "alpha-v2")

    first = registry.execute_sweep([a1], partitions=["git"])
    second = registry.execute_sweep([a2], partitions=["git"])

    assert first.new_items == 1
    assert second.new_items == 0
    assert second.changed_items == 1
    assert registry.item_id(a1.partition_id, a1.source_uri, a1.artifact_type) == registry.item_id(
        a2.partition_id, a2.source_uri, a2.artifact_type
    )


def test_relation_integrity_requires_evidence(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")
    b = obs("b.txt", "beta")
    registry.execute_sweep([a, b], partitions=["git"])

    a_id = registry.item_id(a.partition_id, a.source_uri, a.artifact_type)
    b_id = registry.item_id(b.partition_id, b.source_uri, b.artifact_type)

    with pytest.raises(CoverageError, match="independent evidence_ref"):
        registry.record_relation(subject_id=a_id, predicate="DERIVED_FROM", object_id=b_id, evidence_ref="")

    relation_id = registry.record_relation(
        subject_id=a_id,
        predicate="DERIVED_FROM",
        object_id=b_id,
        evidence_ref="receipt://relation-proof-001",
    )
    assert len(relation_id) == 64


def test_observation_outside_declared_scope_is_rejected(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    item = SweepObservation.from_content(
        partition_id="drive",
        source_uri="drive://file/1",
        artifact_type="file",
        content="x",
    )
    with pytest.raises(CoverageError, match="outside declared sweep scope"):
        registry.execute_sweep([item], partitions=["git"])
