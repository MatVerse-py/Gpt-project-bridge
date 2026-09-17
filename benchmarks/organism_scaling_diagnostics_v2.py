from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from app.core import Decision, stable_hash
from app.evidence import canonical_json, evidence_receipt
from app.organism_loop import GovernedOrganism, sign_authorization_grant
from benchmarks.organism_real_bench import run_worker

FROZEN = "a" * 64
STATE_SECRET = "bench-state-secret"
AUTHORITY_SECRETS = {"omega-authority": "bench-omega-secret"}
TARGET_PROFILE_FUNCTIONS = {"evaluate", "state_root", "canonical_json", "evidence_receipt"}


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * q))))
    return xs[idx]


def _worker_payload(spec: tuple[int, int]) -> dict[str, Any]:
    worker, iterations = spec
    return asdict(run_worker(worker, iterations))


def run_executor_mode(*, mode: str, workers: int, iterations: int) -> dict[str, Any]:
    if workers < 1 or iterations < 1:
        raise ValueError("workers and iterations must be >= 1")
    if mode not in {"sequential", "thread", "process"}:
        raise ValueError(f"unsupported executor mode: {mode}")

    specs = [(worker, iterations) for worker in range(workers)]
    wall0 = time.perf_counter()
    if mode == "sequential":
        rows = [_worker_payload(spec) for spec in specs]
    else:
        executor_cls = ThreadPoolExecutor if mode == "thread" else ProcessPoolExecutor
        with executor_cls(max_workers=workers) as pool:
            rows = list(pool.map(_worker_payload, specs))
    wall = time.perf_counter() - wall0

    total = sum(int(row["iterations"]) for row in rows)
    failures = sum(int(row["failures"]) for row in rows)
    return {
        "mode": mode,
        "workers": workers,
        "iterations_per_worker": iterations,
        "total_evaluations": total,
        "wall_elapsed_s": wall,
        "aggregate_ops_s": total / wall if wall else 0.0,
        "decision_failures": failures,
        "invariant_pass": failures == 0,
        "samples": rows,
    }


def make_profile_organism() -> tuple[GovernedOrganism, str]:
    organism = GovernedOrganism(
        organism_id="scaling-profile-organism",
        frozen_contract_hash=FROZEN,
        runtime_id="scaling-profile-runtime",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )
    seed = organism.evaluate(
        event_id="profile-seed-reject",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=False,
    )
    if seed.decision is not Decision.BLOCK:
        raise RuntimeError("profile seed rejection did not BLOCK")
    candidate = organism.observe_rejection(
        event_id="profile-seed-reject",
        generator_id="profile-generator",
        causal_keys=["action", "tool"],
    )
    grant = sign_authorization_grant(
        secret=AUTHORITY_SECRETS["omega-authority"],
        principal_id="omega-authority",
        candidate_id=candidate.candidate_id,
    )
    constraint = organism.authorize_constraint(candidate, grant=grant)
    return organism, constraint.constraint_id


def run_lineage_profile(*, iterations: int, bucket_size: int) -> dict[str, Any]:
    if iterations < 1 or bucket_size < 1:
        raise ValueError("iterations and bucket_size must be >= 1")

    organism, constraint_id = make_profile_organism()
    buckets: list[dict[str, Any]] = []
    all_failures = 0
    completed = 0

    while completed < iterations:
        end = min(iterations, completed + bucket_size)
        lat_us: list[float] = []
        bucket_failures = 0
        for i in range(completed, end):
            inherited = i % 3 == 0
            proposal = {"action": "EXECUTE", "tool": "shell" if inherited else "python"}
            t0 = time.perf_counter_ns()
            result = organism.evaluate(event_id=f"profile-e{i}", proposal=proposal)
            lat_us.append((time.perf_counter_ns() - t0) / 1000.0)
            expected = Decision.BLOCK if inherited else Decision.PASS
            if result.decision is not expected:
                bucket_failures += 1
            elif inherited and result.matched_constraint_id != constraint_id:
                bucket_failures += 1

        payload = organism.state_payload()

        t0 = time.perf_counter_ns()
        root = organism.state_root()
        state_root_us = (time.perf_counter_ns() - t0) / 1000.0

        t0 = time.perf_counter_ns()
        serialized = canonical_json(payload)
        canonical_json_us = (time.perf_counter_ns() - t0) / 1000.0

        t0 = time.perf_counter_ns()
        receipt = evidence_receipt(
            "SCALING_DIAGNOSTIC",
            {"bucket_end": end, "state_root": root},
            {"serialized_bytes": len(serialized.encode("utf-8"))},
        )
        evidence_receipt_us = (time.perf_counter_ns() - t0) / 1000.0

        buckets.append(
            {
                "start_iteration": completed,
                "end_iteration": end,
                "lineage_entries": len(payload["lineage"]),
                "serialized_state_bytes": len(serialized.encode("utf-8")),
                "evaluate_p50_us": percentile(lat_us, 0.50),
                "evaluate_p95_us": percentile(lat_us, 0.95),
                "evaluate_p99_us": percentile(lat_us, 0.99),
                "state_root_us": state_root_us,
                "canonical_json_us": canonical_json_us,
                "evidence_receipt_us": evidence_receipt_us,
                "diagnostic_receipt_hash": receipt["receipt_hash"],
                "decision_failures": bucket_failures,
            }
        )
        all_failures += bucket_failures
        completed = end

    return {
        "iterations": iterations,
        "bucket_size": bucket_size,
        "decision_failures": all_failures,
        "invariant_pass": all_failures == 0,
        "buckets": buckets,
    }


def _profile_target_calls(*, iterations: int) -> dict[str, Any]:
    organism, _ = make_profile_organism()
    profiler = cProfile.Profile()
    profiler.enable()
    for i in range(iterations):
        organism.evaluate(
            event_id=f"cprofile-e{i}",
            proposal={"action": "OBSERVE", "tool": "python", "index": i},
        )
    profiler.disable()

    stats = pstats.Stats(profiler)
    functions: list[dict[str, Any]] = []
    for (filename, line_no, func_name), values in stats.stats.items():
        if func_name not in TARGET_PROFILE_FUNCTIONS:
            continue
        primitive_calls, total_calls, total_time, cumulative_time, _callers = values
        functions.append(
            {
                "function": func_name,
                "filename": str(Path(filename).name),
                "line": int(line_no),
                "primitive_calls": int(primitive_calls),
                "total_calls": int(total_calls),
                "total_time_s": float(total_time),
                "cumulative_time_s": float(cumulative_time),
            }
        )
    functions.sort(key=lambda row: (row["function"], -row["cumulative_time_s"]))
    return {"iterations": iterations, "functions": functions}


def build_report(
    *,
    modes: Iterable[str],
    workers: int,
    iterations: int,
    profile_iterations: int,
    bucket_size: int,
) -> dict[str, Any]:
    normalized_modes = [item.strip().lower() for item in modes if item.strip()]
    if not normalized_modes:
        raise ValueError("at least one executor mode is required")
    executor_results = [
        run_executor_mode(mode=mode, workers=(1 if mode == "sequential" else workers), iterations=iterations)
        for mode in normalized_modes
    ]
    lineage_profile = run_lineage_profile(iterations=profile_iterations, bucket_size=bucket_size)
    function_profile = _profile_target_calls(iterations=profile_iterations)

    report: dict[str, Any] = {
        "schema": "matverse.organism-scaling-diagnostics.v2",
        "scope": "REAL_CODE_PATH_DIAGNOSTIC",
        "claim_boundary": {
            "correctness": "decision failures and inherited-constraint matching are measured",
            "performance": "diagnostic timings are environment-specific and are not production SLOs",
            "external_pass": "NOT_CLAIMED",
            "world_real_pass": "NOT_CLAIMED",
        },
        "executor_results": executor_results,
        "lineage_profile": lineage_profile,
        "function_profile": function_profile,
    }
    report["result_hash"] = stable_hash(report)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose GovernedOrganism scaling without changing state semantics")
    ap.add_argument("--iterations", type=int, default=400)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--modes", default="sequential,thread,process")
    ap.add_argument("--profile-iterations", type=int, default=600)
    ap.add_argument("--bucket-size", type=int, default=100)
    ap.add_argument("--output", default="organism-scaling-diagnostics-v2.json")
    args = ap.parse_args()

    report = build_report(
        modes=args.modes.split(","),
        workers=args.workers,
        iterations=args.iterations,
        profile_iterations=args.profile_iterations,
        bucket_size=args.bucket_size,
    )
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True, indent=2)
        fh.write("\n")
    print(json.dumps(report, sort_keys=True, indent=2))

    failures = sum(int(row["decision_failures"]) for row in report["executor_results"])
    failures += int(report["lineage_profile"]["decision_failures"])
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
