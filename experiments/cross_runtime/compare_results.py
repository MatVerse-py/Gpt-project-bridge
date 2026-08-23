from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compare(results: list[dict[str, object]]) -> dict[str, object]:
    if len(results) < 2:
        raise ValueError("at least two runtime results are required")
    model_ids = [str(x["model_id"]) for x in results]
    contract_hashes = {str(x["contract_hash"]) for x in results}
    invariant_vectors = [x["hard_invariants"] for x in results]
    distinct_models = len(set(model_ids)) == len(model_ids)
    same_contract = len(contract_hashes) == 1
    every_runtime_pass = all(bool(x.get("runtime_pass")) for x in results)
    same_invariants = all(v == invariant_vectors[0] for v in invariant_vectors[1:])
    runtime_reproduction_pass = distinct_models and same_contract and every_runtime_pass and same_invariants
    evidence = {
        "protocol": "matverse.cross-runtime-replay.v1",
        "scope": "EXTERNAL_COMPUTE_DISTINCT_GITHUB_RUNNERS_SAME_HOSTING_PROVIDER",
        "models": model_ids,
        "distinct_models": distinct_models,
        "same_contract": same_contract,
        "all_runtime_pass": every_runtime_pass,
        "same_hard_invariants": same_invariants,
        "runtime_reproduction_status": "REPRODUCTION_PASS" if runtime_reproduction_pass else "REPRODUCTION_FAIL",
        "external_provider_status": "HOLD_SECOND_INDEPENDENT_PROVIDER_REQUIRED",
        "world_real_status": "HOLD",
        "result_hashes": [hashlib.sha256(canonical_json(x)).hexdigest() for x in results],
    }
    evidence["receipt"] = hashlib.sha256(canonical_json(evidence)).hexdigest()
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = sorted(Path(args.input_dir).glob("*.json"))
    results = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    evidence = compare(results)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence["runtime_reproduction_status"] == "REPRODUCTION_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
