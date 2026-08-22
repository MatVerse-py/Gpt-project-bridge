from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def merkle_root(leaves: Sequence[str]) -> str:
    if not leaves:
        return sha256_text("")
    level = [str(item) for item in leaves]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256_text(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def evidence_receipt(event_type: str, inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> dict[str, Any]:
    input_hash = sha256_text(canonical_json(dict(inputs)))
    output_hash = sha256_text(canonical_json(dict(outputs)))
    core = {
        "schema": "matverse.evidence-receipt.v1",
        "event_type": event_type,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "merkle_root": merkle_root([input_hash, output_hash]),
    }
    return {**core, "receipt_hash": sha256_text(canonical_json(core))}
