from __future__ import annotations

from dataclasses import replace

from app.corpus_commitments import (
    build_commitment_work,
    commit_member,
    verify_membership,
)
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


def test_same_member_random_salts_produce_different_commitments():
    plan = build_fraction_plan(members(1))
    member = plan.fractions[0].members[0]
    salt_a, digest_a = commit_member(member)
    salt_b, digest_b = commit_member(member)

    assert salt_a != salt_b
    assert digest_a != digest_b


def test_membership_verifies_and_mutation_fails():
    plan = build_fraction_plan(members(4), fraction_size=4)
    work = build_commitment_work(plan)
    member = plan.fractions[0].members[0]
    opening = next(item for item in work.openings if item.ordinal == member.ordinal)
    path = work.proofs[(1, member.ordinal)]
    fraction_root = work.public.fractions[0].root

    assert verify_membership(
        member,
        salt_hex=opening.salt_hex,
        path=path,
        expected_root=fraction_root,
    )

    mutated = replace(member, source_hash=sha256_text("mutated"))
    assert not verify_membership(
        mutated,
        salt_hex=opening.salt_hex,
        path=path,
        expected_root=fraction_root,
    )


def test_public_commitment_root_is_salted_and_partition_bound():
    base = build_fraction_plan(members(20), fraction_size=14)
    alt = build_fraction_plan(members(20), fraction_size=17)

    first = build_commitment_work(base)
    second = build_commitment_work(base)
    repartitioned = build_commitment_work(alt)

    assert first.public.root != second.public.root
    assert first.public.root != repartitioned.public.root
    assert first.public.source_protocol == base.protocol
    assert first.public.fraction_size == 14


def test_public_manifest_omits_openings_and_source_identifiers():
    plan = build_fraction_plan(members(5), fraction_size=5)
    work = build_commitment_work(plan)
    public = work.public.public_manifest()
    raw = str(public)

    assert "salt" not in raw.lower()
    assert "document_id" not in raw
    assert "conversation_id" not in raw
    assert "source_hash" not in raw
    assert len(public["root"]) == 64
