from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import stable_hash
from experiments.tesseu_cross_host_v1 import (
    _read_json,
    _write_json,
    restore_and_continue,
)

SCHEMA = "matverse.tesseu-substrate.v2"
REFERENCE_CAPSULE_PATH = ROOT / "evidence" / "tesseu" / "reference_capsule_v1.json"


def _normalize_arch(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"x64", "x86_64", "amd64"}:
        return "X64"
    if raw in {"arm64", "aarch64"}:
        return "ARM64"
    return raw.upper() or "UNKNOWN"


def _infer_provider() -> tuple[str, dict[str, Any]]:
    if str(os.environ.get("GITHUB_ACTIONS", "")).lower() == "true":
        return "github-actions", {
            "kind": "environment",
            "github_actions": True,
            "runner_name": os.environ.get("RUNNER_NAME"),
            "server_url": os.environ.get("GITHUB_SERVER_URL"),
        }
    if os.environ.get("REPL_ID") or os.environ.get("REPL_SLUG") or os.environ.get("REPL_OWNER"):
        return "replit", {
            "kind": "environment",
            "repl_id_present": bool(os.environ.get("REPL_ID")),
            "repl_slug": os.environ.get("REPL_SLUG"),
            "repl_owner_present": bool(os.environ.get("REPL_OWNER")),
        }
    return "unknown", {"kind": "environment", "provider_signal": "none"}


def restore_substrate(
    capsule: Mapping[str, Any],
    *,
    runtime_id: str,
    arm_id: str,
    declared_provider: str | None = None,
) -> dict[str, Any]:
    result = restore_and_continue(capsule, runtime_id=runtime_id, arm_id=arm_id)
    observed_provider, provider_evidence = _infer_provider()
    declared = declared_provider or observed_provider

    runner_arch = _normalize_arch(result.get("environment", {}).get("runner_arch"))
    machine_arch = _normalize_arch(result.get("environment", {}).get("platform_machine") or platform.machine())
    arch_signal_consistent = (
        runner_arch == machine_arch
        or runner_arch == "UNKNOWN"
        or machine_arch == "UNKNOWN"
    )
    provider_signal_consistent = observed_provider != "unknown" and declared == observed_provider

    result["schema"] = SCHEMA
    result["provider"] = {
        "declared": declared,
        "observed": observed_provider,
        "signal_consistent": provider_signal_consistent,
        "evidence": provider_evidence,
    }
    result["architecture"] = {
        "runner_arch": runner_arch,
        "machine_arch": machine_arch,
        "signal_consistent": arch_signal_consistent,
        "evidence_class": "PROVIDER_REPORTED_AND_OS_OBSERVED",
    }
    result["local_pass"] = bool(result.get("local_pass")) and arch_signal_consistent
    result.pop("result_hash", None)
    result["result_hash"] = stable_hash(result)
    return result


def _common_semantic_invariants(rows: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        "same_transferred_snapshot": len({row.get("snapshot_hash") for row in rows}) == 1,
        "same_pre_state_root": len({row.get("pre_state_root") for row in rows}) == 1,
        "same_organism_identity": len({row.get("organism_id") for row in rows}) == 1,
        "same_constitution": len({row.get("constitutional_contract_hash") for row in rows}) == 1,
        "same_gate_fingerprint": len({row.get("gate_fingerprint") for row in rows}) == 1,
        "distinct_runtime_ids": len({row.get("runtime_id") for row in rows}) == len(rows),
        "same_semantic_lineage": len({row.get("semantic_lineage_hash") for row in rows}) == 1,
        "same_governed_decision": {row.get("decision") for row in rows} == {"PASS"},
        "same_decision_reason": len({row.get("reason") for row in rows}) == 1,
        "all_local_invariants_pass": all(bool(row.get("local_pass")) for row in rows),
    }


def compare_substrates(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(item) for item in results]
    if len(rows) < 2:
        raise ValueError("at least two substrate results are required")
    if len({row.get("arm_id") for row in rows}) != len(rows):
        raise ValueError("arm_id values must be unique")

    invariants = _common_semantic_invariants(rows)
    semantic_pass = all(invariants.values())

    arches = {
        str(row.get("architecture", {}).get("runner_arch") or "UNKNOWN")
        for row in rows
    }
    providers = {
        str(row.get("provider", {}).get("observed") or "unknown")
        for row in rows
    }
    operating_systems = {
        str(row.get("environment", {}).get("runner_os") or "UNKNOWN")
        for row in rows
    }

    architecture_evidence_ok = all(
        bool(row.get("architecture", {}).get("signal_consistent")) for row in rows
    )
    provider_evidence_ok = all(
        bool(row.get("provider", {}).get("signal_consistent")) for row in rows
    )

    cross_arch = semantic_pass and architecture_evidence_ok and len(arches) >= 2 and "UNKNOWN" not in arches
    cross_provider = semantic_pass and provider_evidence_ok and len(providers) >= 2 and "unknown" not in providers

    invariants.update(
        {
            "architecture_signals_consistent": architecture_evidence_ok,
            "distinct_architectures": len(arches) >= 2 and "UNKNOWN" not in arches,
            "provider_signals_consistent": provider_evidence_ok,
            "distinct_providers": len(providers) >= 2 and "unknown" not in providers,
        }
    )

    if cross_arch and cross_provider:
        substrate_status = "PASS_MULTI_PROVIDER_CROSS_ARCH"
        world_real_status = "PARTIAL_PASS_CROSS_PROVIDER_ARCH"
    elif cross_arch:
        substrate_status = "PARTIAL_PASS_CROSS_ARCH_SAME_PROVIDER"
        world_real_status = "PARTIAL_PASS_CROSS_ARCH"
    elif cross_provider:
        substrate_status = "PARTIAL_PASS_CROSS_PROVIDER_SAME_ARCH"
        world_real_status = "PARTIAL_PASS_CROSS_PROVIDER"
    else:
        substrate_status = "HOLD"
        world_real_status = "HOLD"

    report = {
        "schema": SCHEMA,
        "scope": "SUBSTRATE_HETEROGENEITY_WITH_EXPLICIT_CLAIM_BOUNDARIES",
        "reference_capsule_hash": rows[0].get("snapshot_hash"),
        "arms": [
            {
                "arm_id": row.get("arm_id"),
                "runtime_id": row.get("runtime_id"),
                "runner_os": row.get("environment", {}).get("runner_os"),
                "runner_arch": row.get("architecture", {}).get("runner_arch"),
                "machine_arch": row.get("architecture", {}).get("machine_arch"),
                "provider": row.get("provider", {}).get("observed"),
                "host_binding": row.get("host_binding"),
                "result_hash": row.get("result_hash"),
            }
            for row in rows
        ],
        "invariants": invariants,
        "architectures_observed": sorted(arches),
        "providers_observed": sorted(providers),
        "operating_systems_observed": sorted(operating_systems),
        "architecture_heterogeneity_status": "PASS_PROVIDER_REPORTED_ARCH" if cross_arch else "HOLD",
        "hardware_independence_status": (
            "PARTIAL_PASS_CROSS_ARCH_NOT_CRYPTOGRAPHICALLY_ATTESTED" if cross_arch else "HOLD"
        ),
        "external_provider_status": (
            "PASS_ENVIRONMENT_EVIDENCED_MULTI_PROVIDER" if cross_provider else "HOLD_SECOND_INDEPENDENT_COMPUTE_PROVIDER_REQUIRED"
        ),
        "substrate_independence_status": substrate_status,
        "world_real_status": world_real_status,
        "claims_boundary": {
            "architecture": "provider-reported runner architecture plus OS-observed machine architecture; not cryptographic hardware attestation",
            "provider": "environment-evidenced infrastructure operator; not third-party certification",
            "continuity": "bounded authenticated state continuity under the frozen Tesseu contract",
        },
    }
    report["receipt"] = stable_hash(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Tesseu substrate heterogeneity experiment v2")
    sub = parser.add_subparsers(dest="command", required=True)

    restore = sub.add_parser("restore")
    restore.add_argument("--capsule", default=str(REFERENCE_CAPSULE_PATH))
    restore.add_argument("--runtime-id", required=True)
    restore.add_argument("--arm-id", required=True)
    restore.add_argument("--provider-id")
    restore.add_argument("--output", required=True)

    compare = sub.add_parser("compare")
    compare.add_argument("--input-dir", required=True)
    compare.add_argument("--output", required=True)

    args = parser.parse_args()
    if args.command == "restore":
        value = restore_substrate(
            _read_json(args.capsule),
            runtime_id=args.runtime_id,
            arm_id=args.arm_id,
            declared_provider=args.provider_id,
        )
        _write_json(args.output, value)
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value["local_pass"] else 2

    paths = sorted(Path(args.input_dir).glob("*.json"))
    rows = [_read_json(path) for path in paths]
    value = compare_substrates(rows)
    _write_json(args.output, value)
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value["architecture_heterogeneity_status"].startswith("PASS") or value["external_provider_status"].startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
