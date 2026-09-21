from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from typing import Any

from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism, sign_authorization_grant

FROZEN = "a" * 64
STATE_SECRET = "hash-view-bench-state"
AUTHORITY_SECRETS = {"omega-authority": "hash-view-bench-authority"}


def percentile(values: list[float], q: float) -> float:
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * q))))
    return xs[idx]


def make_organism() -> GovernedOrganism:
    organism = GovernedOrganism(
        organism_id="hash-view-organism",
        frozen_contract_hash=FROZEN,
        runtime_id="hash-view-runtime",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )
    seed = organism.evaluate(
        event_id="hash-view-seed",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=False,
    )
    if seed.decision is not Decision.BLOCK:
        raise RuntimeError("seed rejection did not BLOCK")
    candidate = organism.observe_rejection(
        event_id="hash-view-seed",
        generator_id="hash-view-generator",
        causal_keys=["action", "tool"],
    )
    grant = sign_authorization_grant(
        secret=AUTHORITY_SECRETS["omega-authority"],
        principal_id="omega-authority",
        candidate_id=candidate.candidate_id,
    )
    organism.authorize_constraint(candidate, grant=grant)
    return organism


def internal_hash_view(organism: GovernedOrganism) -> dict[str, Any]:
    return {
        "schema": "matverse.organism-loop.v1",
        "organism_id": organism.organism_id,
        "constitutional_contract_hash": organism.constitutional_contract_hash,
        "gate_fingerprint": organism.gate_fingerprint,
        "constraints": [asdict(organism._constraints[key]) for key in sorted(organism._constraints)],
        "lineage": organism._lineage,
    }


def timed_hash(obj: Any, repeats: int) -> dict[str, float]:
    values: list[float] = []
    digest: str | None = None
    for _ in range(repeats):
        t0 = time.perf_counter_ns()
        current = stable_hash(obj() if callable(obj) else obj)
        values.append((time.perf_counter_ns() - t0) / 1000.0)
        if digest is None:
            digest = current
        elif digest != current:
            raise RuntimeError("hash changed without state mutation")
    return {
        "p50_us": percentile(values, 0.50),
        "p95_us": percentile(values, 0.95),
        "mean_us": sum(values) / len(values),
        "digest": str(digest),
    }


def run(*, iterations: int, bucket_size: int, repeats: int) -> dict[str, Any]:
    organism = make_organism()
    buckets: list[dict[str, Any]] = []
    completed = 0

    while completed < iterations:
        end = min(iterations, completed + bucket_size)
        for i in range(completed, end):
            inherited = i % 3 == 0
            result = organism.evaluate(
                event_id=f"hash-view-e{i}",
                proposal={"action": "EXECUTE", "tool": "shell" if inherited else "python", "i": i},
            )
            expected = Decision.BLOCK if inherited else Decision.PASS
            if result.decision is not expected:
                raise RuntimeError("decision invariant failure")

        public_root = stable_hash(organism.state_payload())
        view_root = stable_hash(internal_hash_view(organism))
        if public_root != view_root:
            raise RuntimeError("internal hash view is not byte-equivalent to public payload hash")
        if organism.state_root() != public_root:
            raise RuntimeError("canonical cached root diverged from public payload hash")

        public_timing = timed_hash(lambda: organism.state_payload(), repeats)
        view_timing = timed_hash(lambda: internal_hash_view(organism), repeats)
        if public_timing["digest"] != view_timing["digest"]:
            raise RuntimeError("timed roots diverged")

        buckets.append(
            {
                "end_iteration": end,
                "lineage_entries": len(organism._lineage),
                "public_payload_hash": public_timing,
                "internal_view_hash": view_timing,
                "speedup_mean": public_timing["mean_us"] / view_timing["mean_us"],
                "exact_root_equal": True,
            }
        )
        completed = end

    report: dict[str, Any] = {
        "schema": "matverse.organism-hash-view-candidate.v1",
        "claim_boundary": {
            "semantic_equivalence": "exact SHA-256 equality against stable_hash(state_payload()) at every measured bucket",
            "performance": "environment-specific diagnostic only",
            "canonical_change": "NOT_YET",
        },
        "iterations": iterations,
        "bucket_size": bucket_size,
        "repeats": repeats,
        "buckets": buckets,
    }
    report["result_hash"] = stable_hash(report)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=600)
    ap.add_argument("--bucket-size", type=int, default=100)
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--output", default="organism-hash-view-candidate-v1.json")
    args = ap.parse_args()
    report = run(iterations=args.iterations, bucket_size=args.bucket_size, repeats=args.repeats)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True, indent=2)
        fh.write("\n")
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
