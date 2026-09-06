from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

FRACTION_PROTOCOL = "matverse.corpus-fractions.v1"
DEFAULT_FRACTION_SIZE = 14


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class FractionMember:
    ordinal: int
    document_id: str
    conversation_id: str
    source_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "document_id": self.document_id,
            "conversation_id": self.conversation_id,
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True)
class FractionManifest:
    fraction_index: int
    start_ordinal: int
    end_ordinal: int
    member_count: int
    members: tuple[FractionMember, ...]
    manifest_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "fraction_index": self.fraction_index,
            "start_ordinal": self.start_ordinal,
            "end_ordinal": self.end_ordinal,
            "member_count": self.member_count,
            "members": [member.as_dict() for member in self.members],
            "manifest_hash": self.manifest_hash,
        }


@dataclass(frozen=True)
class FractionPlan:
    protocol: str
    fraction_size: int
    total_items: int
    fraction_count: int
    fractions: tuple[FractionManifest, ...]
    merge_root: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "fraction_size": self.fraction_size,
            "total_items": self.total_items,
            "fraction_count": self.fraction_count,
            "fractions": [fraction.as_dict() for fraction in self.fractions],
            "merge_root": self.merge_root,
        }


def fraction_index_for_ordinal(ordinal: int, fraction_size: int = DEFAULT_FRACTION_SIZE) -> int:
    if fraction_size <= 0:
        raise ValueError("fraction_size must be greater than zero")
    if ordinal <= 0:
        raise ValueError("ordinal must be 1-based and greater than zero")
    return ((ordinal - 1) // fraction_size) + 1


def build_fraction_plan(
    members: Iterable[Mapping[str, Any]],
    fraction_size: int = DEFAULT_FRACTION_SIZE,
) -> FractionPlan:
    if fraction_size <= 0:
        raise ValueError("fraction_size must be greater than zero")

    normalized: list[FractionMember] = []
    for expected_ordinal, raw in enumerate(members, start=1):
        ordinal = int(raw.get("ordinal", expected_ordinal))
        if ordinal != expected_ordinal:
            raise ValueError(
                f"fraction members must be contiguous and 1-based: expected {expected_ordinal}, got {ordinal}"
            )
        document_id = str(raw.get("document_id") or "").strip()
        conversation_id = str(raw.get("conversation_id") or "").strip()
        source_hash = str(raw.get("source_hash") or "").strip().lower()
        if not document_id or not conversation_id:
            raise ValueError(f"member {ordinal} requires document_id and conversation_id")
        if len(source_hash) != 64 or any(ch not in "0123456789abcdef" for ch in source_hash):
            raise ValueError(f"member {ordinal} requires a lowercase SHA-256 source_hash")
        normalized.append(
            FractionMember(
                ordinal=ordinal,
                document_id=document_id,
                conversation_id=conversation_id,
                source_hash=source_hash,
            )
        )

    manifests: list[FractionManifest] = []
    for offset in range(0, len(normalized), fraction_size):
        chunk = tuple(normalized[offset : offset + fraction_size])
        fraction_index = (offset // fraction_size) + 1
        payload = {
            "protocol": FRACTION_PROTOCOL,
            "fraction_index": fraction_index,
            "fraction_size": fraction_size,
            "start_ordinal": chunk[0].ordinal,
            "end_ordinal": chunk[-1].ordinal,
            "member_count": len(chunk),
            "members": [member.as_dict() for member in chunk],
        }
        manifests.append(
            FractionManifest(
                fraction_index=fraction_index,
                start_ordinal=chunk[0].ordinal,
                end_ordinal=chunk[-1].ordinal,
                member_count=len(chunk),
                members=chunk,
                manifest_hash=_sha256(payload),
            )
        )

    merge_payload = {
        "protocol": FRACTION_PROTOCOL,
        "fraction_size": fraction_size,
        "total_items": len(normalized),
        "fraction_count": len(manifests),
        "fraction_manifest_hashes": [manifest.manifest_hash for manifest in manifests],
    }
    return FractionPlan(
        protocol=FRACTION_PROTOCOL,
        fraction_size=fraction_size,
        total_items=len(normalized),
        fraction_count=len(manifests),
        fractions=tuple(manifests),
        merge_root=_sha256(merge_payload),
    )
