from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism

SCHEMA = "matverse.tesseu-cross-host.v1"
ORGANISM_ID = "tesseu-organism-001"
STATE_SECRET = "tesseu-ci-state-secret-v1"  # experiment-only; never a production credential
AUTHORITY_SECRETS = {"tesseu-ci-authority": "tesseu-ci-authority-secret-v1"}
FROZEN_CONTRACT_HASH = stable_hash(
    {"experiment": SCHEMA, "constitution": "frozen-cross-host-continuity-contract"}
)
SEED_EVENT_ID = "TESSEU-SEED-001"
CONTINUATION_EVENT_ID = "TESSEU-CONTINUE-001"
SEED_PROPOSAL = {"action": "OBSERVE", "phase": "seed", "payload": "portable-organism-state"}
CONTINUATION_PROPOSAL = {"action": "OBSERVE", "phase": "continuation", "payload": "same-causal-task"}


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _semantic_lineage(lineage: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project lineage onto causal semantics while preserving runtime provenance separately.

    runtime_id and receipt_hash are deliberately excluded from this projection because the
    production Organism records runtime provenance inside each evaluation. Cross-runtime
    continuity should preserve the governed causal meaning, not erase where execution occurred.
    """
    projected: list[dict[str, Any]] = []
    for raw in lineage:
        item = dict(raw)
        item.pop("runtime_id", None)
        item.pop("receipt_hash", None)
        projected.append(item)
    return projected


def semantic_lineage_hash(state: Mapping[str, Any]) -> str:
    lineage = state.get("lineage", [])
    if not isinstance(lineage, list):
        raise ValueError("state lineage must be a list")
    return stable_hash(_semantic_lineage(lineage))


def seed_capsule() -> dict[str, Any]:
    organism = GovernedOrganism(
        organism_id=ORGANISM_ID,
        frozen_contract_hash=FROZEN_CONTRACT_HASH,
        runtime_id="tesseu-seed-runtime",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )
    seed_result = organism.evaluate(event_id=SEED_EVENT_ID, proposal=SEED_PROPOSAL)
    if seed_result.decision is not Decision.PASS:
        raise RuntimeError(f"seed evaluation did not PASS: {seed_result.decision.value}")
    snapshot = organism.export_state()
    capsule = {
        "schema": SCHEMA,
        "organism_id": ORGANISM_ID,
        "frozen_contract_hash": FROZEN_CONTRACT_HASH,
        "gate_fingerprint": organism.gate_fingerprint,
        "constitutional_contract_hash": organism.constitutional_contract_hash,
        "snapshot": snapshot,
        "snapshot_hash": stable_hash(snapshot),
        "pre_continuation_state_root": organism.state_root(),
        "semantic_lineage_hash": semantic_lineage_hash(snapshot),
        "seed_event_id": SEED_EVENT_ID,
        "continuation_event_id": CONTINUATION_EVENT_ID,
        "claims_boundary": {
            "cross_host": "testable by distinct CI host jobs",
            "cross_os": "testable when at least two operating systems complete",
            "hardware_independence": "HOLD unless independently attested heterogeneous hardware is present",
            "external_provider": "HOLD until an independently operated compute provider reproduces the capsule",
        },
    }
    capsule["capsule_hash"] = stable_hash(capsule)
    return capsule


def restore_and_continue(capsule: Mapping[str, Any], *, runtime_id: str, arm_id: str) -> dict[str, Any]:
    if capsule.get("schema") != SCHEMA:
        raise ValueError("unsupported Tesseu capsule schema")
    if stable_hash(capsule.get("snapshot")) != capsule.get("snapshot_hash"):
        raise ValueError("snapshot hash mismatch")
    expected_capsule_hash = capsule.get("capsule_hash")
    core = dict(capsule)
    core.pop("capsule_hash", None)
    if stable_hash(core) != expected_capsule_hash:
        raise ValueError("capsule hash mismatch")

    snapshot = capsule["snapshot"]
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot must be an object")

    organism = GovernedOrganism(
        organism_id=str(capsule["organism_id"]),
        frozen_contract_hash=str(capsule["frozen_contract_hash"]),
        runtime_id=runtime_id,
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
        state=snapshot,
    )
    pre_root = organism.state_root()
    pre_payload = organism.state_payload()
    result = organism.evaluate(event_id=CONTINUATION_EVENT_ID, proposal=CONTINUATION_PROPOSAL)
    post_state = organism.export_state()
    post_payload = organism.state_payload()

    constraints_before = stable_hash(pre_payload.get("constraints", []))
    constraints_after = stable_hash(post_payload.get("constraints", []))
    lineage = post_payload.get("lineage", [])
    event_ids = [item.get("event_id") for item in lineage if isinstance(item, Mapping) and item.get("type") == "EVALUATION"]

    invariants = {
        "snapshot_root_restored": pre_root == capsule["pre_continuation_state_root"],
        "organism_identity_preserved": organism.organism_id == capsule["organism_id"],
        "constitution_preserved": organism.constitutional_contract_hash == capsule["constitutional_contract_hash"],
        "gate_fingerprint_preserved": organism.gate_fingerprint == capsule["gate_fingerprint"],
        "constraints_preserved": constraints_before == constraints_after,
        "seed_lineage_preserved": SEED_EVENT_ID in event_ids,
        "continuation_appended": CONTINUATION_EVENT_ID in event_ids,
        "continuation_decision_pass": result.decision is Decision.PASS,
    }

    environment = {
        "arm_id": arm_id,
        "runtime_id": runtime_id,
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_machine": platform.machine(),
        "python": platform.python_version(),
        "runner_os": os.environ.get("RUNNER_OS") or platform.system(),
        "runner_arch": os.environ.get("RUNNER_ARCH") or platform.machine(),
        "runner_name": os.environ.get("RUNNER_NAME"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_job": os.environ.get("GITHUB_JOB"),
    }
    host_binding = stable_hash({
        "arm_id": arm_id,
        "runtime_id": runtime_id,
        "runner_os": environment["runner_os"],
        "runner_arch": environment["runner_arch"],
        "github_run_id": environment["github_run_id"],
        "github_job": environment["github_job"],
    })

    output = {
        "schema": SCHEMA,
        "arm_id": arm_id,
        "runtime_id": runtime_id,
        "snapshot_hash": capsule["snapshot_hash"],
        "pre_state_root": pre_root,
        "post_state_root": organism.state_root(),
        "organism_id": organism.organism_id,
        "constitutional_contract_hash": organism.constitutional_contract_hash,
        "gate_fingerprint": organism.gate_fingerprint,
        "decision": result.decision.value,
        "reason": result.reason,
        "semantic_lineage_hash": semantic_lineage_hash(post_state),
        "invariants": invariants,
        "local_pass": all(invariants.values()),
        "environment": environment,
        "host_binding": host_binding,
    }
    output["result_hash"] = stable_hash(output)
    return output


def compare_host_results(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(item) for item in results]
    if len(rows) < 2:
        raise ValueError("at least two host results are required")
    if len({row.get("arm_id") for row in rows}) != len(rows):
        raise ValueError("arm_id values must be unique")

    snapshot_hashes = {row.get("snapshot_hash") for row in rows}
    organism_ids = {row.get("organism_id") for row in rows}
    contracts = {row.get("constitutional_contract_hash") for row in rows}
    gates = {row.get("gate_fingerprint") for row in rows}
    pre_roots = {row.get("pre_state_root") for row in rows}
    semantic_lineages = {row.get("semantic_lineage_hash") for row in rows}
    decisions = {row.get("decision") for row in rows}
    reasons = {row.get("reason") for row in rows}
    runtime_ids = {row.get("runtime_id") for row in rows}
    os_values = {str(row.get("environment", {}).get("runner_os")) for row in rows}
    arches = {str(row.get("environment", {}).get("runner_arch")) for row in rows}

    invariants = {
        "same_transferred_snapshot": len(snapshot_hashes) == 1,
        "same_pre_state_root": len(pre_roots) == 1,
        "same_organism_identity": len(organism_ids) == 1,
        "same_constitution": len(contracts) == 1,
        "same_gate_fingerprint": len(gates) == 1,
        "distinct_runtime_ids": len(runtime_ids) == len(rows),
        "same_semantic_lineage": len(semantic_lineages) == 1,
        "same_governed_decision": len(decisions) == 1 and decisions == {"PASS"},
        "same_decision_reason": len(reasons) == 1,
        "all_local_invariants_pass": all(bool(row.get("local_pass")) for row in rows),
        "distinct_operating_systems": len(os_values) >= 2,
    }
    cross_host_os_pass = all(invariants.values())
    raw_post_state_equal = len({row.get("post_state_root") for row in rows}) == 1

    report = {
        "schema": SCHEMA,
        "scope": "DISTINCT_GITHUB_HOSTED_RUNNERS_CROSS_OS_SAME_HOSTING_PROVIDER",
        "arms": [
            {
                "arm_id": row.get("arm_id"),
                "runtime_id": row.get("runtime_id"),
                "runner_os": row.get("environment", {}).get("runner_os"),
                "runner_arch": row.get("environment", {}).get("runner_arch"),
                "host_binding": row.get("host_binding"),
                "result_hash": row.get("result_hash"),
            }
            for row in rows
        ],
        "invariants": invariants,
        "cross_host_os_status": "PASS" if cross_host_os_pass else "FAIL",
        "raw_post_state_roots_equal": raw_post_state_equal,
        "raw_root_note": (
            "Raw roots may differ because runtime_id and receipt provenance are intentionally recorded in lineage; "
            "semantic lineage equality is the cross-runtime invariant."
        ),
        "hardware_arches_observed": sorted(arches),
        "hardware_independence_status": (
            "PARTIAL_PASS_DISTINCT_ARCH" if len(arches) >= 2 and cross_host_os_pass else "HOLD_ATTESTED_HETEROGENEOUS_HARDWARE_REQUIRED"
        ),
        "external_provider_status": "HOLD_SECOND_INDEPENDENT_COMPUTE_PROVIDER_REQUIRED",
        "world_real_status": "PARTIAL_PASS_CROSS_HOST_OS" if cross_host_os_pass else "HOLD",
    }
    report["receipt"] = stable_hash(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Tesseu cross-host causal continuity experiment")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed")
    seed.add_argument("--output", required=True)

    restore = sub.add_parser("restore")
    restore.add_argument("--capsule", required=True)
    restore.add_argument("--runtime-id", required=True)
    restore.add_argument("--arm-id", required=True)
    restore.add_argument("--output", required=True)

    compare = sub.add_parser("compare")
    compare.add_argument("--input-dir", required=True)
    compare.add_argument("--output", required=True)

    args = parser.parse_args()
    if args.command == "seed":
        value = seed_capsule()
        _write_json(args.output, value)
        print(json.dumps({"status": "PASS", "snapshot_hash": value["snapshot_hash"], "output": args.output}, sort_keys=True))
        return 0
    if args.command == "restore":
        value = restore_and_continue(_read_json(args.capsule), runtime_id=args.runtime_id, arm_id=args.arm_id)
        _write_json(args.output, value)
        print(json.dumps({"status": "PASS" if value["local_pass"] else "FAIL", "arm_id": args.arm_id, "output": args.output}, sort_keys=True))
        return 0 if value["local_pass"] else 2

    paths = sorted(Path(args.input_dir).glob("*.json"))
    rows = [_read_json(path) for path in paths]
    value = compare_host_results(rows)
    _write_json(args.output, value)
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value["cross_host_os_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
