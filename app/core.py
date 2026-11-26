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
    # This exact dependency contract is intentionally enforced inside the
    # production guard. In source-less deployments the constitutional binding
    # fingerprints this function's bytecode/constants, so an incompatible
    # HDBResult replacement cannot silently share the same admissibility
    # contract: evaluation fails closed before producing a decision.
    params = getattr(HDBResult, "__dataclass_params__", None)
    fields = getattr(HDBResult, "__dataclass_fields__", None)
    if (
        HDBResult.__module__ != __name__
        or HDBResult.__qualname__ != "HDBResult"
        or params is None
        or getattr(params, "frozen", False) is not True
        or not isinstance(fields, dict)
        or tuple(fields) != ("decision", "reason")
        or tuple(str(fields[name].type) for name in ("decision", "reason")) != ("Decision", "str")
        or HDBResult.__getattribute__ is not object.__getattribute__
        or "__post_init__" in HDBResult.__dict__
    ):
        raise RuntimeError("HDBResult constitutional contract mismatch")

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


def omega_gate(*, hdb: HDBResult, action: str, ontology_ok: bool | None, signature_valid: bool | None, transition_valid: bool | None) -> tuple[Decision, str]:
    # Known violations dominate unknown inputs; unresolved contracts fail closed as HOLD.
    if hdb.decision is Decision.BLOCK:
        return Decision.BLOCK, hdb.reason
    if ontology_ok is False:
        return Decision.BLOCK, "ontology violation"
    if transition_valid is False:
        return Decision.BLOCK, "valid_signature != valid_transition: transition contract violation"
    if signature_valid is False:
        return Decision.BLOCK, "invalid signature"
    if ontology_ok is None:
        return Decision.HOLD, "ontology unresolved"
    if signature_valid is None:
        return Decision.HOLD, "authentication context unresolved"
    if transition_valid is None:
        return Decision.HOLD, "transition contract unresolved"
    if hdb.decision is Decision.HOLD:
        return Decision.HOLD, hdb.reason
    return Decision.PASS, "admissible declared transition"
