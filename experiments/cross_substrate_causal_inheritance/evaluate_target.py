from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path


EXPECTED_PREREG_SHA256 = "7b496c5a8bf5750297d7c14083e965372e517fc867c244d784ec929a4213cabf"


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_obj(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target-result", required=True)
    p.add_argument("--governance-pack", required=True)
    p.add_argument("--prereg", required=True)
    p.add_argument("--stack-root", required=True)
    p.add_argument("--sequence", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    prereg_path = Path(args.prereg)
    prereg_sha = sha256_file(prereg_path)
    if prereg_sha != EXPECTED_PREREG_SHA256:
        raise RuntimeError(f"prereg hash mismatch: {prereg_sha}")
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))

    target_path = Path(args.target_result)
    target = json.loads(target_path.read_text(encoding="utf-8"))
    if not target.get("runtime_pass"):
        raise RuntimeError("target runtime did not satisfy frozen candidate contract")
    if target.get("sequence_id") != args.sequence or target.get("role") != "target":
        raise RuntimeError("target artifact sequence/role mismatch")

    pack_path = Path(args.governance_pack)
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    if pack.get("sequence_id") != args.sequence:
        raise RuntimeError("governance pack sequence mismatch")
    core = dict(pack)
    observed_pack_hash = core.pop("governance_pack_sha256")
    if sha256_obj(core) != observed_pack_hash:
        raise RuntimeError("governance pack canonical hash mismatch")

    seq = next(x for x in prereg["sequences"] if x["sequence_id"] == args.sequence)
    model_pair_ok = (
        pack["origin_model_id"] == seq["origin_model"]
        and target["model_id"] == seq["target_model"]
        and pack["origin_model_id"] != target["model_id"]
    )

    serialized_target = json.dumps(target, sort_keys=True)
    forbidden_origin_context_absent = all(
        token not in serialized_target
        for token in [
            "origin-context::",
            pack["origin_model_id"],
            pack["candidate"]["candidate_id"],
            pack["receipts"]["A_PROMOTED_MATCHING"]["receipt_sha256"],
        ]
    )

    sys.path.insert(0, str(Path(args.stack_root) / "src"))
    from matverse_stack.constraint_gate import CausalConstraintRule, MutationContext
    from matverse_stack.ledger import Ledger
    from matverse_stack.service import MatVerseService

    obs = target["parsed_candidate"]
    mutation = MutationContext(
        mutation_id=f"{args.sequence}-target-proposal",
        mutation_class=obs["mutation_class"],
        confidence=obs["confidence"],
        compensating_guard=obs["compensating_guard"],
        payload_hash=hashlib.sha256(
            f"{args.sequence}-target-payload".encode("utf-8")
        ).hexdigest(),
    )
    initial_state = {
        "psi": 0.95,
        "theta": 0.95,
        "pole": 0.95,
        "losses": [],
        "latency_ms": 0,
        "replay_ok": True,
        "receipt_ok": True,
        "publication_ok": True,
    }

    with tempfile.TemporaryDirectory() as td:
        service = MatVerseService.__new__(MatVerseService)
        service.ledger = Ledger(Path(td) / "cross_substrate_ledger.jsonl")
        arm_results = {}
        input_hashes = set()
        state_hashes = set()

        for arm, rule_dict in pack["rules"].items():
            rule = CausalConstraintRule.model_validate(rule_dict)
            result = service.evaluate_mutation(
                mutation,
                rule,
                initial_state=initial_state,
                constraint_authority=f"EXTERNAL_GOVERNANCE_PACK::{observed_pack_hash}",
            )
            input_hashes.add(result["input_hash"])
            state_hashes.add(result["state_hash_before"])
            arm_results[arm] = {
                "final_decision": result["final_decision"],
                "constraint_decision": result["constraint_decision"]["decision"],
                "activated_constraint_ids": result["activated_constraint_ids"],
                "input_hash": result["input_hash"],
                "state_hash_before": result["state_hash_before"],
                "ledger_entry_hash": result["ledger_entry"]["entry_hash"],
                "receipt_leaf": result["receipt"]["leaf"],
            }

        ledger_integrity = service.ledger.verify_integrity()

    expected_outcomes = {
        "A_PROMOTED_MATCHING": "BLOCK",
        "B_NOT_PROMOTED": "PASS",
        "C_PLACEBO": "PASS",
        "D_ATTRIBUTION_ABLATION": "PASS",
    }
    outcomes_match = all(
        arm_results[arm]["final_decision"] == decision
        for arm, decision in expected_outcomes.items()
    )
    same_input_state_across_arms = len(input_hashes) == 1 and len(state_hashes) == 1

    criteria = {
        "target_runtime_pass": bool(target["runtime_pass"]),
        "model_pair_matches_prereg": model_pair_ok,
        "different_origin_and_target_models": pack["origin_model_id"] != target["model_id"],
        "target_has_no_origin_context_artifact": forbidden_origin_context_absent,
        "governance_state_external_to_models": bool(pack["governance_state_external_to_models"]),
        "authority_ablation_rejected": bool(pack["authority_ablation_rejected"]),
        "origin_was_actually_rejected": pack["origin_rejection_evidence"]["decision"]["decision"] == "BLOCK",
        "promoted_matching_rule_active": pack["rules"]["A_PROMOTED_MATCHING"]["status"] == "ACTIVE",
        "same_target_input_state_across_arms": same_input_state_across_arms,
        "expected_arm_outcomes": outcomes_match,
        "ledger_integrity": bool(ledger_integrity),
    }
    primary_result = "PASS" if all(criteria.values()) else "FAIL"

    report_core = {
        "schema": "matverse.cross_substrate_causal_inheritance_report/1.0",
        "experiment_id": prereg["experiment_id"],
        "sequence_id": args.sequence,
        "prereg_sha256": prereg_sha,
        "origin_model_id": pack["origin_model_id"],
        "origin_model_revision": pack["origin_model_revision"],
        "target_model_id": target["model_id"],
        "target_model_revision": target["model_revision"],
        "origin_result_sha256": pack["origin_result_sha256"],
        "governance_pack_sha256": observed_pack_hash,
        "target_result_sha256": sha256_file(target_path),
        "primary_result": primary_result,
        "criteria": criteria,
        "arm_results": arm_results,
        "claim_boundary": prereg["claim_boundary"],
        "interpretation": (
            "PASS supports a bounded algorithmic claim that a rejection-derived constraint, "
            "independently promoted into external governance state, changes the later decision "
            "for the same observable candidate after the origin model context is absent and a "
            "different model substrate produces the candidate. It does not establish legal novelty, "
            "independent-provider reproduction, population inference, or universal cross-substrate persistence."
        ),
    }
    report = {**report_core, "report_sha256": sha256_obj(report_core)}
    Path(args.output).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "sequence_id": args.sequence,
        "primary_result": primary_result,
        "report_sha256": report["report_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
