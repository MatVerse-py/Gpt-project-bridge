from __future__ import annotations

import ast
import hashlib
from pathlib import Path

PATH = Path("app/organism_loop.py")
EXPECTED_GIT_BLOB_SHA = "dee9c7b7c6538c5e5a47048278ac2071ebfb808c"


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one replacement target, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    raw = PATH.read_bytes()
    observed = git_blob_sha(raw)
    if observed != EXPECTED_GIT_BLOB_SHA:
        raise SystemExit(
            f"preimage mismatch: expected {EXPECTED_GIT_BLOB_SHA}, observed {observed}; refuse to patch"
        )

    text = raw.decode("utf-8")

    text = replace_once(
        text,
        '''        self._constraints: dict[str, InheritedConstraint] = {}\n        self._lineage: list[dict[str, Any]] = []\n        if state is not None:\n            self._restore(state)\n\n    def _state_mac(self, payload: Mapping[str, Any]) -> str:\n''',
        '''        self._constraints: dict[str, InheritedConstraint] = {}\n        self._lineage: list[dict[str, Any]] = []\n        self._state_generation = 0\n        self._cached_state_root: str | None = None\n        self._cached_state_generation = -1\n        if state is not None:\n            self._restore(state)\n\n    def _mark_state_mutated(self) -> None:\n        self._state_generation += 1\n        self._cached_state_root = None\n\n    def _state_mac(self, payload: Mapping[str, Any]) -> str:\n''',
        "initialize root cache",
    )

    text = replace_once(
        text,
        '''    def state_root(self) -> str:\n        return stable_hash(self.state_payload())\n\n    def export_state(self) -> dict[str, Any]:\n        payload = self.state_payload()\n        return {**payload, "state_root": stable_hash(payload), "state_mac": self._state_mac(payload)}\n''',
        '''    def state_root(self) -> str:\n        if self._cached_state_root is None or self._cached_state_generation != self._state_generation:\n            self._cached_state_root = stable_hash(self.state_payload())\n            self._cached_state_generation = self._state_generation\n        return self._cached_state_root\n\n    def export_state(self) -> dict[str, Any]:\n        payload = self.state_payload()\n        return {**payload, "state_root": self.state_root(), "state_mac": self._state_mac(payload)}\n''',
        "cache state root",
    )

    text = replace_once(
        text,
        '''        self._lineage.append({\n            "type": "CONSTRAINT_PROMOTED",\n            "source_event_id": candidate.source_event_id,\n            "source_receipt_hash": candidate.source_receipt_hash,\n            "constraint_id": constraint.constraint_id,\n            "authorizer_id": grant.principal_id,\n        })\n        return constraint\n''',
        '''        self._lineage.append({\n            "type": "CONSTRAINT_PROMOTED",\n            "source_event_id": candidate.source_event_id,\n            "source_receipt_hash": candidate.source_receipt_hash,\n            "constraint_id": constraint.constraint_id,\n            "authorizer_id": grant.principal_id,\n        })\n        self._mark_state_mutated()\n        return constraint\n''',
        "invalidate after constraint promotion",
    )

    text = replace_once(
        text,
        '''        self._lineage.append({\n            "type": "EVALUATION",\n            "event_id": event_id,\n            "runtime_id": self.runtime_id,\n            "proposal": proposal_copy,\n            "gate_inputs": gate_inputs,\n            "decision": decision.value,\n            "reason": reason,\n            "matched_constraint_id": matched_id,\n            "receipt_hash": receipt["receipt_hash"],\n        })\n        return LoopResult(\n''',
        '''        self._lineage.append({\n            "type": "EVALUATION",\n            "event_id": event_id,\n            "runtime_id": self.runtime_id,\n            "proposal": proposal_copy,\n            "gate_inputs": gate_inputs,\n            "decision": decision.value,\n            "reason": reason,\n            "matched_constraint_id": matched_id,\n            "receipt_hash": receipt["receipt_hash"],\n        })\n        self._mark_state_mutated()\n        return LoopResult(\n''',
        "invalidate after evaluation",
    )

    ast.parse(text)
    PATH.write_text(text, encoding="utf-8")
    print("patched app/organism_loop.py with exact state-root memoization")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
