from __future__ import annotations

import pytest

from app.coverage_registry import (
    CoverageError,
    CoverageLifecycle,
    CoverageRegistry,
    CoverageVerdict,
    LineageStatus,
    MembershipStatus,
    PresenceStatus,
    SweepObservation,
)


def obs(name: str, content: str, *, partition: str = "git") -> SweepObservation:
    return SweepObservation.from_content(
        partition_id=partition,
        source_uri=f"repo://matverse/{name}",
        artifact_type="file",
        content=content,
        metadata={"name": name},
    )


def test_two_zero_delta_complete_sweeps_are_required(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")
    b = obs("b.txt", "beta")

    first = registry.execute_sweep(
        [a, b],
        partitions=["git"],
        source_ids=["git-main"],
    )
    assert first.new_items == 2
    assert first.delta_sources == 1
    assert first.zero_streak == 0

    second = registry.execute_sweep(
        [a, b],
        partitions=["git"],
        source_ids=["git-main"],
    )
    assert second.new_items == 0
    assert second.missing_items == 0
    assert second.delta_sources == 0
    assert second.zero_streak == 1
    assert not second.discovery_saturated

    third = registry.execute_sweep(
        [a, b],
        partitions=["git"],
        source_ids=["git-main"],
    )
    assert third.zero_streak == 2
    assert third.discovery_saturated

    report = registry.report(partitions=["git"], source_ids=["git-main"])
    assert report["discovery_saturated"] is True
    assert report["adjudication_complete"] is False
    assert report["coverage_complete"] is False

    a_id = registry.item_id(a.partition_id, a.source_uri, a.artifact_type)
    b_id = registry.item_id(b.partition_id, b.source_uri, b.artifact_type)
    registry.record_relation(
        subject_id=a_id,
        predicate="RELATED_TO",
        object_id=b_id,
        evidence_ref="receipt://relation/ab",
    )
    for item_id in (a_id, b_id):
        registry.set_status(
            item_id,
            lifecycle=CoverageLifecycle.ADJUDICATED,
            verdict=CoverageVerdict.CANONICAL,
            membership_status=MembershipStatus.MEMBER,
            lineage_status=LineageStatus.ROOT,
            semantic_status="RESOLVED",
            evidence_status="TESTED",
        )

    report = registry.report(partitions=["git"], source_ids=["git-main"])
    assert report["orphan_items"] == 0
    assert report["adjudication_complete"] is True
    assert report["coverage_complete"] is True


def test_partial_sweeps_never_contribute_to_saturation(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")

    first = registry.execute_sweep(
        [a],
        partitions=["git"],
        source_ids=["git-main"],
        complete_scope=False,
    )
    second = registry.execute_sweep(
        [a],
        partitions=["git"],
        source_ids=["git-main"],
        complete_scope=False,
    )

    assert first.status == "PARTIAL_SCOPE"
    assert second.status == "PARTIAL_SCOPE"
    assert second.zero_streak == 0
    assert not second.discovery_saturated


def test_duplicate_identity_in_one_sweep_is_rejected(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")

    with pytest.raises(CoverageError, match="duplicate coverage identity"):
        registry.execute_sweep([a, a], partitions=["git"])


def test_missing_known_item_is_presence_not_orphan_state(tmp_path):
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
    current = registry.get_item(b_id)
    assert current["presence_status"] == PresenceStatus.MISSING.value
    assert current["lifecycle"] == CoverageLifecycle.DISCOVERED.value
    assert current["verdict"] == CoverageVerdict.PENDING.value


def test_content_revision_is_changed_not_new_and_requires_readjudication(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a1 = obs("a.txt", "alpha")
    a2 = obs("a.txt", "alpha-v2")

    registry.execute_sweep([a1], partitions=["git"])
    item_id = registry.item_id(a1.partition_id, a1.source_uri, a1.artifact_type)
    registry.set_status(
        item_id,
        lifecycle=CoverageLifecycle.ADJUDICATED,
        verdict=CoverageVerdict.UNRESOLVED,
        membership_status=MembershipStatus.MEMBER,
        lineage_status=LineageStatus.ROOT,
        semantic_status="RESOLVED",
        evidence_status="TESTED",
        justification_hash="a" * 64,
    )

    second = registry.execute_sweep([a2], partitions=["git"])

    assert second.new_items == 0
    assert second.changed_items == 1
    current = registry.get_item(item_id)
    assert current["lifecycle"] == CoverageLifecycle.DISCOVERED.value
    assert current["verdict"] == CoverageVerdict.PENDING.value
    assert current["semantic_status"] == "UNRESOLVED"
    assert current["evidence_status"] == "UNVERIFIED"
    assert current["justification_hash"] is None


def test_relation_integrity_requires_evidence_and_orphan_is_derived(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")
    b = obs("b.txt", "beta")
    registry.execute_sweep([a, b], partitions=["git"])

    a_id = registry.item_id(a.partition_id, a.source_uri, a.artifact_type)
    b_id = registry.item_id(b.partition_id, b.source_uri, b.artifact_type)

    assert registry.is_orphan(a_id)
    assert registry.is_orphan(b_id)

    with pytest.raises(CoverageError, match="independent evidence_ref"):
        registry.record_relation(
            subject_id=a_id,
            predicate="DERIVED_FROM",
            object_id=b_id,
            evidence_ref="",
        )

    registry.set_status(b_id, verdict=CoverageVerdict.CANONICAL)
    relation_id = registry.record_relation(
        subject_id=a_id,
        predicate="DERIVED_FROM",
        object_id=b_id,
        evidence_ref="receipt://relation-proof-001",
    )
    assert len(relation_id) == 64
    assert not registry.is_orphan(a_id)


def test_canonical_orphan_and_unjustified_unresolved_are_violations(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")
    b = obs("b.txt", "beta")
    registry.execute_sweep([a, b], partitions=["git"])
    a_id = registry.item_id(a.partition_id, a.source_uri, a.artifact_type)
    b_id = registry.item_id(b.partition_id, b.source_uri, b.artifact_type)

    registry.set_status(
        a_id,
        lifecycle=CoverageLifecycle.ADJUDICATED,
        verdict=CoverageVerdict.CANONICAL,
    )
    registry.set_status(
        b_id,
        lifecycle=CoverageLifecycle.ADJUDICATED,
        verdict=CoverageVerdict.UNRESOLVED,
    )
    violations = registry.violations(partitions=["git"])
    assert any("orphan with verdict CANONICAL" in item for item in violations)
    assert any("orphan without hashed justification" in item for item in violations)


def test_external_reference_closure_blocks_saturation(tmp_path):
    registry = CoverageRegistry(tmp_path / "coverage.db")
    a = obs("a.txt", "alpha")

    registry.execute_sweep(
        [a],
        partitions=["git"],
        source_ids=["git-main"],
        referenced_sources=["hf-index"],
    )
    second = registry.execute_sweep(
        [a],
        partitions=["git"],
        source_ids=["git-main"],
        referenced_sources=["hf-index"],
    )
    third = registry.execute_sweep(
        [a],
        partitions=["git"],
        source_ids=["git-main"],
        referenced_sources=["hf-index"],
    )

    assert second.external_refs == ("hf-index",)
    assert third.external_refs == ("hf-index",)
    assert not third.discovery_saturated

    registry.execute_sweep(
        [a],
        partitions=["git"],
        source_ids=["git-main", "hf-index"],
    )
    clean1 = registry.execute_sweep(
        [a],
        partitions=["git"],
        source_ids=["git-main", "hf-index"],
    )
    clean2 = registry.execute_sweep(
        [a],
        partitions=["git"],
        source_ids=["git-main", "hf-index"],
    )
    assert clean1.zero_streak == 1
    assert clean2.discovery_saturated


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
