from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict

from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism, sign_authorization_grant

FROZEN = "a" * 64
STATE_SECRET = "bench-state-secret"
AUTHORITY_SECRETS = {"omega-authority": "bench-omega-secret"}


@dataclass
class Sample:
    worker: int
    iterations: int
    pass_count: int
    block_count: int
    inherited_block_count: int
    failures: int
    elapsed_s: float
    ops_s: float
    p50_us: float
    p95_us: float
    p99_us: float
    final_state_root: str


def make_organism(worker: int) -> GovernedOrganism:
    return GovernedOrganism(
        organism_id=f"bench-org-{worker}",
        frozen_contract_hash=FROZEN,
        runtime_id=f"bench-runtime-{worker}",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * q))))
    return xs[idx]


def run_worker(worker: int, iterations: int) -> Sample:
    organism = make_organism(worker)
    # Establish a real causal chain: verified rejection -> candidate -> independent authorization -> inherited constraint.
    seed = organism.evaluate(
        event_id=f"w{worker}-seed-reject",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=False,
    )
    if seed.decision is not Decision.BLOCK:
        raise RuntimeError("seed rejection did not BLOCK")
    candidate = organism.observe_rejection(
        event_id=f"w{worker}-seed-reject",
        generator_id=f"generator-{worker}",
        causal_keys=["action", "tool"],
    )
    grant = sign_authorization_grant(
        secret=AUTHORITY_SECRETS["omega-authority"],
        principal_id="omega-authority",
        candidate_id=candidate.candidate_id,
    )
    constraint = organism.authorize_constraint(candidate, grant=grant)

    lat_us: list[float] = []
    pass_count = block_count = inherited_block_count = failures = 0
    start = time.perf_counter()
    for i in range(iterations):
        inherited = i % 3 == 0
        proposal = {"action": "EXECUTE", "tool": "shell" if inherited else "python"}
        t0 = time.perf_counter_ns()
        result = organism.evaluate(event_id=f"w{worker}-e{i}", proposal=proposal)
        lat_us.append((time.perf_counter_ns() - t0) / 1000.0)
        expected = Decision.BLOCK if inherited else Decision.PASS
        if result.decision is expected:
            if expected is Decision.PASS:
                pass_count += 1
            else:
                block_count += 1
                if result.matched_constraint_id == constraint.constraint_id:
                    inherited_block_count += 1
                else:
                    failures += 1
        else:
            failures += 1
    elapsed = time.perf_counter() - start
    return Sample(
        worker=worker,
        iterations=iterations,
        pass_count=pass_count,
        block_count=block_count,
        inherited_block_count=inherited_block_count,
        failures=failures,
        elapsed_s=elapsed,
        ops_s=(iterations / elapsed) if elapsed else 0.0,
        p50_us=percentile(lat_us, 0.50),
        p95_us=percentile(lat_us, 0.95),
        p99_us=percentile(lat_us, 0.99),
        final_state_root=organism.state_root(),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--output", default="organism-real-bench.json")
    args = ap.parse_args()
    if args.iterations < 1 or args.workers < 1:
        raise SystemExit("iterations/workers must be >= 1")

    wall0 = time.perf_counter()
    if args.workers == 1:
        samples = [run_worker(0, args.iterations)]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            samples = list(pool.map(lambda w: run_worker(w, args.iterations), range(args.workers)))
    wall = time.perf_counter() - wall0
    total = sum(s.iterations for s in samples)
    failures = sum(s.failures for s in samples)
    payload = {
        "schema": "matverse.organism-real-bench.v1",
        "scope": "REAL_CODE_PATH_GITHUB_RUNNER",
        "synthetic_fixture": False,
        "workers": args.workers,
        "iterations_per_worker": args.iterations,
        "total_evaluations": total,
        "wall_elapsed_s": wall,
        "aggregate_ops_s": total / wall if wall else 0.0,
        "decision_failures": failures,
        "invariant_pass": failures == 0,
        "samples": [asdict(s) for s in samples],
    }
    payload["result_hash"] = stable_hash(payload)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
