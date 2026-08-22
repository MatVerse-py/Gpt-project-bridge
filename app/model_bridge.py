from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .core import stable_hash

PROTOCOL_VERSION = "matverse.model-bridge.v1.1"

_ALLOWED_ROOT_KEYS = {
    "kind",
    "public_summary",
    "decision",
    "claims",
    "safety",
    "state",
    "evidence",
    "metadata",
    "ontology_state",
    "policy_state",
}

_FORBIDDEN_CANONICAL = {
    "chainofthought",
    "cot",
    "reasoningtrace",
    "hiddenreasoning",
    "hiddenstate",
    "privatememory",
    "internalmemory",
    "systemprompt",
    "developerprompt",
    "credentials",
    "apikey",
    "secretkey",
    "accesstoken",
    "refreshtoken",
    "password",
}

InvariantMode = Literal["exact", "set_equal", "type_equal"]


@dataclass(frozen=True)
class InvariantResult:
    path: str
    mode: InvariantMode
    passed: bool
    left: Any
    right: Any


def canonical_key(key: Any) -> str:
    text = str(key).strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def contract_hash(contract: Mapping[str, str]) -> str:
    required = {
        "ontology_hash",
        "policy_hash",
        "task_hash",
        "rubric_hash",
        "memory_policy_hash",
    }
    missing = sorted(required - set(contract))
    extra = sorted(set(contract) - required)
    if missing:
        raise ValueError(f"missing frozen contract fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"unexpected frozen contract fields: {', '.join(extra)}")
    for key in required:
        value = contract[key]
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"{key} must be a 64-character SHA-256 hex digest")
        try:
            int(value, 16)
        except ValueError as exc:
            raise ValueError(f"{key} must be a SHA-256 hex digest") from exc
    return stable_hash({"protocol_version": PROTOCOL_VERSION, **dict(contract)})


def assert_transferable_state(value: Any, path: str = "$", *, root: bool = True) -> None:
    if isinstance(value, Mapping):
        if root:
            unexpected = sorted(str(key) for key in value if str(key) not in _ALLOWED_ROOT_KEYS)
            if unexpected:
                raise ValueError(f"undeclared transferable root fields: {', '.join(unexpected)}")
        for key, nested in value.items():
            canonical = canonical_key(key)
            if canonical in _FORBIDDEN_CANONICAL:
                raise ValueError(f"forbidden hidden/private field at {path}.{key}")
            assert_transferable_state(nested, f"{path}.{key}", root=False)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            assert_transferable_state(nested, f"{path}[{index}]", root=False)


def build_handoff_digest(
    *,
    session_id: str,
    sequence: int,
    from_participant: str,
    to_participant: str,
    parent_handoff_id: str | None,
    payload: Mapping[str, Any],
    frozen_contract_hash: str,
) -> str:
    assert_transferable_state(payload)
    return stable_hash(
        {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": session_id,
            "sequence": sequence,
            "from_participant": from_participant,
            "to_participant": to_participant,
            "parent_handoff_id": parent_handoff_id,
            "payload": dict(payload),
            "frozen_contract_hash": frozen_contract_hash,
        }
    )


def _resolve_path(document: Mapping[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if not part:
            raise ValueError("invariant path cannot contain empty segments")
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def _compare_values(left: Any, right: Any, mode: InvariantMode) -> bool:
    if mode == "exact":
        return left == right
    if mode == "type_equal":
        return type(left) is type(right)
    if mode == "set_equal":
        if isinstance(left, (str, bytes)) or isinstance(right, (str, bytes)):
            return False
        try:
            return set(left) == set(right)
        except TypeError:
            return False
    raise ValueError(f"unsupported invariant mode: {mode}")


def compare_invariants(
    *,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    rules: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    assert_transferable_state(left)
    assert_transferable_state(right)
    if not rules:
        raise ValueError("at least one pre-declared invariant rule is required")

    results: list[InvariantResult] = []
    for rule in rules:
        path = rule.get("path", "")
        mode = rule.get("mode", "exact")
        if mode not in {"exact", "set_equal", "type_equal"}:
            raise ValueError(f"unsupported invariant mode: {mode}")
        try:
            left_value = _resolve_path(left, path)
            right_value = _resolve_path(right, path)
            passed = _compare_values(left_value, right_value, mode)  # type: ignore[arg-type]
        except KeyError:
            left_value = None
            right_value = None
            passed = False
        results.append(InvariantResult(path=path, mode=mode, passed=passed, left=left_value, right=right_value))  # type: ignore[arg-type]

    portable = all(item.passed for item in results)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "portable": portable,
        "passed": sum(1 for item in results if item.passed),
        "total": len(results),
        "results": [
            {"path": item.path, "mode": item.mode, "passed": item.passed, "left": item.left, "right": item.right}
            for item in results
        ],
    }
