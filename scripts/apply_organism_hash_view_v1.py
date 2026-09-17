from __future__ import annotations

import ast
import hashlib
from pathlib import Path

PATH = Path("app/organism_loop.py")
EXPECTED_GIT_BLOB_SHA = "c5994fc14073260192257e5142b79d85f486e141"


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def main() -> int:
    raw = PATH.read_bytes()
    observed = git_blob_sha(raw)
    if observed != EXPECTED_GIT_BLOB_SHA:
        raise SystemExit(
            f"preimage mismatch: expected {EXPECTED_GIT_BLOB_SHA}, observed {observed}; refuse to patch"
        )

    text = raw.decode("utf-8")
    old = '''    def state_payload(self) -> dict[str, Any]:\n        return {\n            "schema": SCHEMA_VERSION,\n            "organism_id": self.organism_id,\n            "constitutional_contract_hash": self.constitutional_contract_hash,\n            "gate_fingerprint": self.gate_fingerprint,\n            "constraints": [asdict(self._constraints[key]) for key in sorted(self._constraints)],\n            "lineage": _json_clone(self._lineage),\n        }\n\n    def state_root(self) -> str:\n        if self._cached_state_root is None or self._cached_state_generation != self._state_generation:\n            self._cached_state_root = stable_hash(self.state_payload())\n            self._cached_state_generation = self._state_generation\n        return self._cached_state_root\n'''
    new = '''    def _state_hash_payload(self) -> dict[str, Any]:\n        # Internal read-only view for hashing. The private lineage remains owned by\n        # GovernedOrganism and is never returned to callers through this method.\n        return {\n            "schema": SCHEMA_VERSION,\n            "organism_id": self.organism_id,\n            "constitutional_contract_hash": self.constitutional_contract_hash,\n            "gate_fingerprint": self.gate_fingerprint,\n            "constraints": [asdict(self._constraints[key]) for key in sorted(self._constraints)],\n            "lineage": self._lineage,\n        }\n\n    def state_payload(self) -> dict[str, Any]:\n        payload = self._state_hash_payload()\n        return {**payload, "lineage": _json_clone(payload["lineage"])}\n\n    def state_root(self) -> str:\n        if self._cached_state_root is None or self._cached_state_generation != self._state_generation:\n            self._cached_state_root = stable_hash(self._state_hash_payload())\n            self._cached_state_generation = self._state_generation\n        return self._cached_state_root\n'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one state-payload/root block, found {count}")
    text = text.replace(old, new, 1)
    ast.parse(text)
    PATH.write_text(text, encoding="utf-8")
    print("patched app/organism_loop.py with internal no-clone hash view")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
