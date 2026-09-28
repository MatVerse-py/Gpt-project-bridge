from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Mapping, Sequence

from .corpus_fractions import FRACTION_PROTOCOL, FractionMember, FractionPlan
from .evidence import canonical_json

COMMITMENT_PROTOCOL = "matverse.corpus-commitments.v1"
LEAF_TAG = b"\x00"
NODE_TAG = b"\x01"
ROOT_TAG = b"\x02"
SALT_BYTES = 16


class CommitmentError(RuntimeError):
    pass


def _h(*parts: bytes) -> bytes:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
    return digest.digest()


def _hex(value: bytes) -> str:
    return value.hex()


def _require_hex_digest(value: str, *, field: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _member_payload(member: FractionMember) -> dict[str, object]:
    return {
        "ordinal": member.ordinal,
        "document_id": member.document_id,
        "conversation_id": member.conversation_id,
        "source_hash": member.source_hash,
    }


@dataclass(frozen=True)
class LeafOpening:
    fraction_index: int
    ordinal: int
    salt_hex: str
    digest: str

    def salt_bytes(self) -> bytes:
        return bytes.fromhex(self.salt_hex)


@dataclass(frozen=True)
class MerkleProofStep:
    sibling: str
    side: str


@dataclass(frozen=True)
class FractionCommitment:
    fraction_index: int
    start_ordinal: int
    end_ordinal: int
    member_count: int
    root: str
    leaf_digests: tuple[str, ...]


@dataclass(frozen=True)
class CorpusCommitment:
    protocol: str
    source_protocol: str
    total_items: int
    fraction_size: int
    fraction_count: int
    fractions: tuple[FractionCommitment, ...]
    root: str

    def public_manifest(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "source_protocol": self.source_protocol,
            "total_items": self.total_items,
            "fraction_size": self.fraction_size,
            "fraction_count": self.fraction_count,
            "fractions": [
                {
                    "fraction_index": item.fraction_index,
                    "start_ordinal": item.start_ordinal,
                    "end_ordinal": item.end_ordinal,
                    "member_count": item.member_count,
                    "root": item.root,
                }
                for item in self.fractions
            ],
            "root": self.root,
        }


@dataclass(frozen=True)
class CommitmentWork:
    public: CorpusCommitment
    openings: tuple[LeafOpening, ...]
    proofs: Mapping[tuple[int, int], tuple[MerkleProofStep, ...]]


def commit_member(member: FractionMember, salt: bytes | None = None) -> tuple[bytes, bytes]:
    opening = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    if len(opening) != SALT_BYTES:
        raise ValueError(f"salt must be exactly {SALT_BYTES} bytes")
    payload = canonical_json(_member_payload(member)).encode("utf-8")
    return opening, _h(LEAF_TAG, opening, payload)


def _build_merkle(
    digests: Sequence[bytes],
) -> tuple[bytes, tuple[tuple[MerkleProofStep, ...], ...]]:
    if not digests:
        raise CommitmentError("empty fraction has no commitment root")

    paths: list[list[MerkleProofStep]] = [[] for _ in digests]
    nodes: list[tuple[bytes, tuple[int, ...]]] = [
        (digest, (index,)) for index, digest in enumerate(digests)
    ]

    while len(nodes) > 1:
        if len(nodes) % 2:
            digest, leaves = nodes[-1]
            nodes.append((digest, leaves))
        next_nodes: list[tuple[bytes, tuple[int, ...]]] = []
        for index in range(0, len(nodes), 2):
            left_digest, left_leaves = nodes[index]
            right_digest, right_leaves = nodes[index + 1]
            parent = _h(NODE_TAG, left_digest, right_digest)

            left_set = set(left_leaves)
            right_set = set(right_leaves)
            for leaf_index in left_set:
                paths[leaf_index].append(MerkleProofStep(_hex(right_digest), "R"))
            for leaf_index in right_set - left_set:
                paths[leaf_index].append(MerkleProofStep(_hex(left_digest), "L"))

            merged = tuple(sorted(left_set | right_set))
            next_nodes.append((parent, merged))
        nodes = next_nodes

    return nodes[0][0], tuple(tuple(path) for path in paths)


def verify_membership(
    member: FractionMember,
    *,
    salt_hex: str,
    path: Sequence[MerkleProofStep],
    expected_root: str,
) -> bool:
    salt = bytes.fromhex(salt_hex)
    if len(salt) != SALT_BYTES:
        return False
    _, digest = commit_member(member, salt=salt)
    current = digest
    for step in path:
        sibling = bytes.fromhex(_require_hex_digest(step.sibling, field="sibling"))
        if step.side == "L":
            current = _h(NODE_TAG, sibling, current)
        elif step.side == "R":
            current = _h(NODE_TAG, current, sibling)
        else:
            raise ValueError(f"invalid proof side: {step.side!r}")
    return hmac.compare_digest(current.hex(), _require_hex_digest(expected_root, field="expected_root"))


def build_commitment_work(
    plan: FractionPlan,
    *,
    salts: Mapping[tuple[int, int], bytes] | None = None,
) -> CommitmentWork:
    if plan.protocol != FRACTION_PROTOCOL:
        raise CommitmentError(f"unsupported fraction protocol: {plan.protocol}")

    openings: list[LeafOpening] = []
    proof_map: dict[tuple[int, int], tuple[MerkleProofStep, ...]] = {}
    public_fractions: list[FractionCommitment] = []
    fraction_roots: list[bytes] = []

    for fraction in plan.fractions:
        digests: list[bytes] = []
        per_fraction_openings: list[tuple[FractionMember, bytes, bytes]] = []
        for member in fraction.members:
            key = (fraction.fraction_index, member.ordinal)
            salt = salts[key] if salts is not None and key in salts else None
            opening, digest = commit_member(member, salt=salt)
            digests.append(digest)
            per_fraction_openings.append((member, opening, digest))

        fraction_root, paths = _build_merkle(digests)
        fraction_roots.append(fraction_root)

        for idx, (member, opening, digest) in enumerate(per_fraction_openings):
            openings.append(
                LeafOpening(
                    fraction_index=fraction.fraction_index,
                    ordinal=member.ordinal,
                    salt_hex=opening.hex(),
                    digest=digest.hex(),
                )
            )
            proof_map[(fraction.fraction_index, member.ordinal)] = paths[idx]

        public_fractions.append(
            FractionCommitment(
                fraction_index=fraction.fraction_index,
                start_ordinal=fraction.start_ordinal,
                end_ordinal=fraction.end_ordinal,
                member_count=fraction.member_count,
                root=fraction_root.hex(),
                leaf_digests=tuple(digest.hex() for digest in digests),
            )
        )

    root = _h(
        ROOT_TAG,
        COMMITMENT_PROTOCOL.encode("utf-8"),
        plan.protocol.encode("utf-8"),
        plan.total_items.to_bytes(8, "big"),
        plan.fraction_size.to_bytes(8, "big"),
        plan.fraction_count.to_bytes(8, "big"),
        *fraction_roots,
    )

    return CommitmentWork(
        public=CorpusCommitment(
            protocol=COMMITMENT_PROTOCOL,
            source_protocol=plan.protocol,
            total_items=plan.total_items,
            fraction_size=plan.fraction_size,
            fraction_count=plan.fraction_count,
            fractions=tuple(public_fractions),
            root=root.hex(),
        ),
        openings=tuple(openings),
        proofs=proof_map,
    )
