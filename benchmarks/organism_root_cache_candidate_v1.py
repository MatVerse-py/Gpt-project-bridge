from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism, sign_authorization_grant
from app.organism_root_cache import CachedRootGovernedOrganism

FROZEN = "a" * 64
STATE_SECRET = "root-cache-bench-state"
AUTHORITY_SECRETS = {"omega-authority": "root-cache-bench-authority"}


@dataclass
class Sample:
    implementation: str
    worker: int
    iterations: int
    elapsed_s: float
    ops_s: float
    p50_us: float
    p95_us: float
    p99_us: float
    failures: int
    final_state_root: str
    root_recomputations: int | None


def percentile(values: list[float], q: float) -> float:
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * q))))
    return xs[idx]


def _class_for(name: str):
    if name == "base":
        return GovernedOrganism
    if name == "cached":
        return CachedRootGovernedOrganism
    raise ValueError(name)


def run_case(implementation: str, worker: int, iterations: int) -> Sample:
    cls = _class_for(implementation)
    organism = cls(
        organism_id=f"root-cache-bench-{worker}",
        frozen_contract_hash=FROZEN,
        runtime_id=f"root-cache-runtime-{worker}",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )

    seed = organism.evaluate(
        event_id=f"seed-{worker}",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=False,
    )
    if seed.decision is not Decision.BLOCK:
        raise RuntimeError("seed rejection did not BLOCK")
    candidate = organism.observe_rejection(
        event_id=f"seed-{worker}",
        generator_id=f"generator-{worker}",
        causal_keys=["action", "tool"],
    )
    grant = sign_authorization_grant(
        secret=AUTHORITY_SECRETS["omega-authority"],
        principal_id="omega-authority",
        candidate_id=candidate.candidate_id,
    )
    constraint = organism.authorize_constraint(candidate, grant=grant)

    latencies: list[float] = []
    failures = 0
    t0 = time.perf_counter()
    for i in range(iterations):
        inherited = i % 3 == 0
        proposal = {"action": "EXECUTE", "tool": "shell" if inherited else "python"}
        e0 = time.perf_counter_ns()
        result = organism.evaluate(event_id=f"w{worker}-e{i}", proposal=proposal)
        latencies.append((time.perf_counter_ns() - e0) / 1000.0)
        expected = Decision.BLOCK if inherited else Decision.PASS
        if result.decision is not expected:
            failures += 1
        elif inherited and result.matched_constraint_id != constraint.constraint_id:
            failures += 1
    elapsed = time.perf_counter() - t0

    final_root = organism.state_root()
    if final_root != stable_hash(organism.state_payload()):
        raise RuntimeError("state root diverged from canonical payload hash")

    recomputations = getattr(organism, "state_root_recomputations", None)
    return Sample(
        implementation=implementation,
        worker=worker,
        iterations=iterations,
        elapsed_s=elapsed,
        ops_s=iterations / elapsed if elapsed else 0.0,
        p50_us=percentile(latencies, 0.50),
        p95_us=percentile(latencies, 0.95),
        p99_us=percentile(latencies, 0.99),
        failures=failures,
        final_state_root=final_root,
        root_recomputations=recomputations,
    )


def _parallel_worker(spec: tuple[str, int, int]) -> dict[str, Any]:
    return asdict(run_case(*spec))


def run_parallel(*, implementation: str, executor: str, workers: int, iterations: int) -> dict[str, Any]:
    executor_cls = ThreadPoolExecutor if executor == "thread" else ProcessPoolExecutor
    specs = [(implementation, worker, iterations) for worker in range(workers)]
    t0 = time.perf_counter()
    with executor_cls(max_workers=workers) as pool:
        samples = list(pool.map(_parallel_worker, specs))
    elapsed = time.perf_counter() - t0
    total = workers * iterations
    return {
        "implementation": implementation,
        "executor": executor,
        "workers": workers,
        "iterations_per_worker": iterations,
        "total_evaluations": total,
        "wall_elapsed_s": elapsed,
        "aggregate_ops_s": total / elapsed if elapsed else 0.0,
        "failures": sum(int(row["failures"]) for row in samples),
        "samples": samples,
    }


def _ratio(new: float, old: float) -> float | None:
    if not old:
        return None
    return new / old


def main() -> int:
    ap = argparse.ArgumentParser(description="Test exact state-root cache candidate against canonical GovernedOrganism")
    ap.add_argument("--sequential-iterations", type=int, default=800)
    ap.add_argument("--parallel-iterations", type=int, default=400)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", default="organism-root-cache-candidate-v1.json")
    args = ap.parse_args()

    base_seq = asdict(run_case("base", 0, args.sequential_iterations))
    cached_seq = asdict(run_case("cached", 0, args.sequential_iterations))
    if base_seq["final_state_root"] != cached_seq["final_state_root"]:
        raise RuntimeError("cached implementation changed final state root")

    base_thread = run_parallel(
        implementation="base", executor="thread", workers=args.workers, iterations=args.parallel_iterations
    )
    cached_thread = run_parallel(
        implementation="cached", executor="thread", workers=args.workers, iterations=args.parallel_iterations
    )
    base_process = run_parallel(
        implementation="base", executor="process", workers=args.workers, iterations=args.parallel_iterations
    )
    cached_process = run_parallel(
        implementation="cached", executor="process", workers=args.workers, iterations=args.parallel_iterations
    )

    for base_group, cached_group in ((base_thread, cached_thread), (base_process, cached_process)):
        base_roots = [row["final_state_root"] for row in base_group["samples"]]
        cached_roots = [row["final_state_root"] for row in cached_group["samples"]]
        if base_roots != cached_roots:
            raise RuntimeError(f"cached implementation changed roots under {base_group['executor']}")

    report: dict[str, Any] = {
        "schema": "matverse.organism-root-cache-candidate.v1",
        "claim_boundary": {
            "semantic_equivalence": "requires exact final state-root equality and zero decision failures",
            "performance": "environment-specific diagnostic only",
            "canonical_runtime_change": "NOT_YET",
            "external_pass": "NOT_CLAIMED",
            "world_real_pass": "NOT_CLAIMED",
        },
        "sequential": {"base": base_seq, "cached": cached_seq},
        "thread": {"base": base_thread, "cached": cached_thread},
        "process": {"base": base_process, "cached": cached_process},
        "speedup_ratio": {
            "sequential_ops": _ratio(cached_seq["ops_s"], base_seq["ops_s"]),
            "thread_aggregate_ops": _ratio(cached_thread["aggregate_ops_s"], base_thread["aggregate_ops_s"]),
            "process_aggregate_ops": _ratio(cached_process["aggregate_ops_s"], base_process["aggregate_ops_s"]),
        },
        "semantic_equivalence": {
            "sequential_root_equal": base_seq["final_state_root"] == cached_seq["final_state_root"],
            "thread_roots_equal": [row["final_state_root"] for row in base_thread["samples"]]
            == [row["final_state_root"] for row in cached_thread["samples"]],
            "process_roots_equal": [row["final_state_root"] for row in base_process["samples"]]
            == [row["final_state_root"] for row in cached_process["samples"]],
            "all_failures": sum(
                [
                    int(base_seq["failures"]),
                    int(cached_seq["failures"]),
                    int(base_thread["failures"]),
                    int(cached_thread["failures"]),
                    int(base_process["failures"]),
                    int(cached_process["failures"]),
                ]
            ),
        },
    }
    report["result_hash"] = stable_hash(report)

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(report, indent=2, sort_keys=True))

    equivalent = all(
        [
            report["semantic_equivalence"]["sequential_root_equal"],
            report["semantic_equivalence"]["thread_roots_equal"],
            report["semantic_equivalence"]["process_roots_equal"],
            report["semantic_equivalence"]["all_failures"] == 0,
        ]
    )
    return 0 if equivalent else 2


if __name__ == "__main__":
    raise SystemExit(main())
