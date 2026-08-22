from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .core import stable_hash

PROTOCOL_VERSION = "matverse.model-bridge.v1"

FORBIDDEN_STATE_KEYS = {
    "chain_of_thought",
    "chain-of-thought",
    "cot",
    "reasoning_trace",
    "hidden_reasoning",
    "hidden_state",
    "private_memory",
    "internal_memory",
    "system_prompt",
    "developer_prompt",
    "credentials",
    "api_key",
    "secret_key",
}

InvariantMode = Literal["exact", "set_equal", "type_equal"]


@dataclass(frozen=True)
class InvariantResult:
    path: str
    mode: InvariantMode
    passed: bool
    left: Any
    right: Any


def contract_hash(contract: Mapping[str, str]) -> str:
    required = {
        "ontology_hash",
        "policy_hash",
        "task_hash",
        "rubric_hash",
        "memory_policy_hash",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise ValueError(f"missing frozen contract fields: {', '.join(missing)}")
    for key in required:
        value = contract[key]
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"{key} must be a 64-character SHA-256 hex digest")
        try:
            int(value, 16)
        except ValueError as exc:
            raise ValueError(f"{key} must be a SHA-256 hex digest") from exc
    return stable_hash({"protocol_version": PROTOCOL_VERSION, **dict(contract)})


def assert_transferable_state(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_STATE_KEYS:
                raise ValueError(f"forbidden hidden/private field at {path}.{key}")
            assert_transferable_state(nested, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            assert_transferable_state(nested, f"{path}[{index}]")


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
        results.append(
            InvariantResult(
                path=path,
                mode=mode,  # type: ignore[arg-type]
                passed=passed,
                left=left_value,
                right=right_value,
            )
        )

    portable = all(item.passed for item in results)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "portable": portable,
        "passed": sum(1 for item in results if item.passed),
        "total": len(results),
        "results": [
            {
                "path": item.path,
                "mode": item.mode,
                "passed": item.passed,
                "left": item.left,
                "right": item.right,
            }
            for item in results
        ],
    }
