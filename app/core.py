from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any
import json


class Decision(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    HOLD = "HOLD"


class POState(str, Enum):
    READY = "PO_READY"
    EVALUATING = "PO_EVALUATING"
    PASS = "PO_PASS"
    FAIL = "PO_FAIL"


@dataclass(frozen=True)
class HDBResult:
    decision: Decision
    reason: str


BLOCKED_ACTIONS = {"COMMIT", "EXECUTE", "EXPORT", "PUBLISH", "PROVIDER_EXPOSURE"}


def stable_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(payload).hexdigest()


def evaluate_hdb(human: dict[str, Any] | None) -> HDBResult:
    if human is None:
        return HDBResult(Decision.PASS, "no human data")
    if human.get("serialize_human") is True:
        return HDBResult(Decision.BLOCK, "H must not be serialized")
    if human.get("third_party") is True and human.get("third_party_consent") is not True:
        return HDBResult(Decision.BLOCK, "third-party data without consent")
    if human.get("sensitivity") == "SECRET":
        return HDBResult(Decision.BLOCK, "SECRET is non-exportable")
    if human.get("consent") is not True:
        return HDBResult(Decision.HOLD, "human data requires explicit consent")
    if not human.get("purpose"):
        return HDBResult(Decision.HOLD, "purpose binding required")
    return HDBResult(Decision.PASS, "authorized representation")


def omega_gate(*, hdb: HDBResult, action: str, ontology_ok: bool, signature_valid: bool, transition_valid: bool) -> tuple[Decision, str]:
    if not ontology_ok:
        return Decision.BLOCK, "ontology violation"
    if hdb.decision is Decision.BLOCK:
        return Decision.BLOCK, hdb.reason
    if hdb.decision is Decision.HOLD:
        return Decision.HOLD, hdb.reason
    if not signature_valid:
        return Decision.BLOCK, "invalid signature"
    if signature_valid and not transition_valid:
        return Decision.BLOCK, "valid_signature != valid_transition"
    if action in BLOCKED_ACTIONS and not transition_valid:
        return Decision.BLOCK, "critical action denied"
    return Decision.PASS, "admissible transition"
