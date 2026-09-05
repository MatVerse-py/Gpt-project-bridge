from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path


def sha256_obj(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pattern", default="eval-v1.1-*.json")
    p.add_argument("--output", required=True)
    args = p.parse_args()

    reports = [json.loads(Path(f).read_text(encoding="utf-8")) for f in sorted(glob.glob(args.pattern))]
    by_seq = {r["sequence_id"]: r for r in reports}
    required = {"QWEN_TO_SMOL", "SMOL_TO_QWEN"}
    criteria = {
        "exactly_two_sequences": set(by_seq) == required and len(reports) == 2,
        "all_sequences_pass": len(reports) == 2 and all(r["primary_result"] == "PASS" for r in reports),
        "qwen_to_smol_pair": (
            by_seq.get("QWEN_TO_SMOL", {}).get("origin_model_id") == "Qwen/Qwen2.5-0.5B-Instruct"
            and by_seq.get("QWEN_TO_SMOL", {}).get("target_model_id") == "HuggingFaceTB/SmolLM2-360M-Instruct"
        ),
        "smol_to_qwen_pair": (
            by_seq.get("SMOL_TO_QWEN", {}).get("origin_model_id") == "HuggingFaceTB/SmolLM2-360M-Instruct"
            and by_seq.get("SMOL_TO_QWEN", {}).get("target_model_id") == "Qwen/Qwen2.5-0.5B-Instruct"
        ),
        "same_prereg": len({r.get("prereg_sha256") for r in reports}) == 1,
        "all_context_flush_checks_pass": len(reports) == 2 and all(r["criteria"]["target_has_no_origin_causal_context"] for r in reports),
        "all_authority_ablations_pass": len(reports) == 2 and all(r["criteria"]["authority_ablation_rejected"] for r in reports),
        "all_normalized_states_equal": len(reports) == 2 and all(r["criteria"]["normalized_state_equal_across_models"] for r in reports),
    }
    primary_result = "PASS" if all(criteria.values()) else "FAIL"

    core = {
        "schema": "matverse.cross_substrate_causal_inheritance_evidence_pack/1.1",
        "experiment_id": "PATENTE-GCI-CROSS-SUBSTRATE-2026-09-04-V1.1",
        "primary_result": primary_result,
        "criteria": criteria,
        "sequence_reports": [
            {
                "sequence_id": r["sequence_id"],
                "origin_model_id": r["origin_model_id"],
                "origin_model_revision": r.get("origin_model_revision"),
                "target_model_id": r["target_model_id"],
                "target_model_revision": r.get("target_model_revision"),
                "report_sha256": r["report_sha256"],
                "governance_pack_sha256": r["governance_pack_sha256"],
                "arm_results": {k: v["final_decision"] for k, v in r["arm_results"].items()},
            }
            for r in reports
        ],
        "promotion": {
            "NORMALIZED_OBSERVABLE_STATE_CROSS_MODEL": "PASS" if primary_result == "PASS" else "HOLD",
            "REJECTION_SPECIFIC_CAUSAL_ATTRIBUTION": "PASS" if primary_result == "PASS" else "HOLD",
            "INDEPENDENT_CONSTRAINT_PROMOTION": "PASS" if primary_result == "PASS" else "HOLD",
            "CONTEXT_FLUSH_PERSISTENCE": "PASS" if primary_result == "PASS" else "HOLD",
            "CROSS_MODEL_GOVERNANCE_PERSISTENCE": "PASS" if primary_result == "PASS" else "HOLD",
            "MODEL_INDEPENDENT_SEMANTIC_DISCOVERY": "HOLD",
            "INDEPENDENT_PROVIDER_REPRODUCTION": "HOLD",
            "PATENTABILITY": "HOLD",
            "WORLD_REAL": "HOLD"
        },
        "scope": "TWO_MODEL_GITHUB_ACTIONS_LAB / SAME_HOSTING_PROVIDER / FROZEN_NORMALIZED_CARRIER / EXTERNAL_GOVERNANCE_STATE",
        "negative_result_lineage": "v1 remains preserved as PILOT_NEGATIVE_RESULT_CROSS_SUBSTRATE_001 and is not overwritten by this protocol."
    }
    pack = {**core, "evidence_pack_sha256": sha256_obj(core)}
    Path(args.output).write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"primary_result": primary_result, "evidence_pack_sha256": pack["evidence_pack_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
