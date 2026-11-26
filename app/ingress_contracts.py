from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .core import stable_hash
from .storage import get_contract_artifact

HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
ALLOWED_ACTIONS = frozenset({"READ", "COMMIT", "EXECUTE", "EXPORT", "PUBLISH"})


@dataclass(frozen=True)
class IngressContractValidation:
    ontology_ok: bool | None
    transition_valid: bool | None
    ontology_status: str
    transition_status: str
    ontology_reason: str
    transition_reason: str
    ontology_hash: str | None
    transition_hash: str | None
    source_state: str | None
    target_state: str | None

    def as_dict(self) -> dict[str, Any]:
        statuses = {self.ontology_status, self.transition_status}
        overall = "BLOCK" if "BLOCK" in statuses else "HOLD" if "HOLD" in statuses else "PASS"
        return {
            "status": overall,
            "ontology_hash": self.ontology_hash,
            "transition_hash": self.transition_hash,
            "ontology_status": self.ontology_status,
            "transition_status": self.transition_status,
            "ontology_reason": self.ontology_reason,
            "transition_reason": self.transition_reason,
            "source_state": self.source_state,
            "target_state": self.target_state,
            "transition_semantics": "DECLARED_CONTRACT_EDGE_ONLY_NOT_CURRENT_STATE_PROOF",
        }


def _load_artifact(artifact_hash: str | None, expected_kind: str) -> tuple[dict[str, Any] | None, bool | None, str]:
    if artifact_hash is None or artifact_hash == "":
        return None, None, f"{expected_kind} hash missing"
    if not isinstance(artifact_hash, str) or HASH_PATTERN.fullmatch(artifact_hash) is None:
        return None, False, f"{expected_kind} hash malformed"
    artifact = get_contract_artifact(artifact_hash)
    if artifact is None:
        return None, None, f"{expected_kind} artifact is not registered"
    if artifact.get("kind") != expected_kind:
        return None, False, f"expected {expected_kind}, found {artifact.get('kind')}"
    content = artifact.get("content")
    if not isinstance(content, dict):
        return None, False, f"{expected_kind} content must be an object"
    if artifact.get("artifact_hash") != artifact_hash or stable_hash(content) != artifact_hash:
        return None, False, f"{expected_kind} content does not match its immutable hash"
    return content, True, f"{expected_kind} artifact resolved"


def _validate_ontology(content: dict[str, Any], action: str) -> tuple[bool, str]:
    if set(content) != {"ontology_id", "version", "allowed_actions"}:
        return False, "ontology schema must contain exactly ontology_id, version and allowed_actions"
    if not isinstance(content["ontology_id"], str) or not content["ontology_id"].strip():
        return False, "ontology_id must be a non-empty string"
    if not isinstance(content["version"], str) or not content["version"].strip():
        return False, "ontology version must be a non-empty string"
    actions = content["allowed_actions"]
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(item, str) or item not in ALLOWED_ACTIONS for item in actions)
        or len(set(actions)) != len(actions)
    ):
        return False, "allowed_actions must be a non-empty unique list of supported actions"
    if action not in actions:
        return False, f"action {action} is not allowed by the registered ontology"
    return True, "action is allowed by the registered ontology"


def _validate_transition(
    content: dict[str, Any],
    action: str,
    source_state: str | None,
    target_state: str | None,
) -> tuple[bool | None, str]:
    if set(content) != {"transition_id", "version", "transitions"}:
        return False, "transition schema must contain exactly transition_id, version and transitions"
    if not isinstance(content["transition_id"], str) or not content["transition_id"].strip():
        return False, "transition_id must be a non-empty string"
    if not isinstance(content["version"], str) or not content["version"].strip():
        return False, "transition version must be a non-empty string"
    transitions = content["transitions"]
    if not isinstance(transitions, list) or not transitions or len(transitions) > 256:
        return False, "transitions must be a non-empty list with at most 256 entries"
    edges: set[tuple[str, str, str]] = set()
    for edge in transitions:
        if not isinstance(edge, dict) or set(edge) != {"action", "from_state", "to_state"}:
            return False, "each transition must contain exactly action, from_state and to_state"
        edge_action = edge["action"]
        source = edge["from_state"]
        target = edge["to_state"]
        if (
            not isinstance(edge_action, str)
            or edge_action not in ALLOWED_ACTIONS
            or not isinstance(source, str)
            or not source.strip()
            or not isinstance(target, str)
            or not target.strip()
        ):
            return False, "transition entries contain invalid action or state identifiers"
        key = (edge_action, source, target)
        if key in edges:
            return False, "duplicate transition edge"
        edges.add(key)
    if not source_state or not target_state:
        return None, "source_state and target_state are required to evaluate the declared transition"
    requested = (action, source_state, target_state)
    if requested not in edges:
        return False, "requested action/state edge is absent from the registered transition contract"
    return True, "requested edge exists in the registered transition contract"


def evaluate_ingress_contracts(
    *,
    action: str,
    ontology_hash: str | None,
    transition_hash: str | None,
    source_state: str | None,
    target_state: str | None,
) -> IngressContractValidation:
    ontology, ontology_resolved, ontology_reason = _load_artifact(ontology_hash, "ontology")
    if ontology_resolved is True:
        ontology_ok, ontology_reason = _validate_ontology(ontology or {}, action)
        ontology_status = "PASS" if ontology_ok else "BLOCK"
    elif ontology_resolved is None:
        ontology_ok = None
        ontology_status = "HOLD"
    else:
        ontology_ok = False
        ontology_status = "BLOCK"

    transition, transition_resolved, transition_reason = _load_artifact(transition_hash, "transition")
    if transition_resolved is True:
        transition_valid, transition_reason = _validate_transition(
            transition or {}, action, source_state, target_state
        )
        transition_status = "PASS" if transition_valid is True else "HOLD" if transition_valid is None else "BLOCK"
    elif transition_resolved is None:
        transition_valid = None
        transition_status = "HOLD"
    else:
        transition_valid = False
        transition_status = "BLOCK"

    return IngressContractValidation(
        ontology_ok=ontology_ok,
        transition_valid=transition_valid,
        ontology_status=ontology_status,
        transition_status=transition_status,
        ontology_reason=ontology_reason,
        transition_reason=transition_reason,
        ontology_hash=ontology_hash,
        transition_hash=transition_hash,
        source_state=source_state,
        target_state=target_state,
    )
