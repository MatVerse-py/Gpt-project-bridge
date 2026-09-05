from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from normalized_adapter import adapt_runtime_result


EXPECTED_PREREG_SHA256 = "f0ac9e7fb5eb6f87c984e2a6ff359135475b60ef0d1e86ec9ac64df8fb4aa4d1"


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_obj(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--origin-result", required=True)
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

    origin_path = Path(args.origin_result)
    origin = json.loads(origin_path.read_text(encoding="utf-8"))
    normalized = adapt_runtime_result(origin, prereg)

    seq = next(x for x in prereg["sequences"] if x["sequence_id"] == args.sequence)
    if origin.get("model_id") != seq["origin_model"]:
        raise RuntimeError("origin model does not match preregistered sequence")

    sys.path.insert(0, str(Path(args.stack_root) / "src"))
    from matverse_stack.causal_inheritance import (
        AuthoritySeparationError,
        CausalRejection,
        ConstraintGenerator,
        IndependentAdjudicator,
    )
    from matverse_stack.constraint_gate import (
        CausalConstraintRule,
        MutationContext,
        evaluate_constraint,
    )

    origin_mutation = MutationContext(
        mutation_id=f"{args.sequence}-origin-normalized",
        mutation_class=normalized["mutation_class"],
        confidence=normalized["confidence"],
        compensating_guard=normalized["compensating_guard"],
        payload_hash=hashlib.sha256(
            f"{args.sequence}-normalized-origin-payload".encode("utf-8")
        ).hexdigest(),
    )
    seed_rule = CausalConstraintRule(
        constraint_id="seed-safety-unscoped-write-v1.1",
        status="ACTIVE",
        mutation_class=normalized["mutation_class"],
        confidence_lt=prereg["constraint_threshold"],
        allow_if_compensating_guard=True,
    )
    rejection_decision = evaluate_constraint(origin_mutation, seed_rule)
    if rejection_decision.decision != "BLOCK":
        raise RuntimeError("normalized origin mutation was not rejected")

    rejection = CausalRejection(
        rejection_id=f"{args.sequence}-rejection-v1.1-001",
        mutation_class=origin_mutation.mutation_class,
        observed_confidence=origin_mutation.confidence,
        reason=rejection_decision.reason,
        causal_attribution=prereg["causal_attribution"],
        context_id=f"origin-context::{args.sequence}::v1.1",
        substrate_id=origin["model_id"],
    )
    placebo = CausalRejection(
        rejection_id=f"{args.sequence}-placebo-v1.1-001",
        mutation_class="PLACEBO_UNRELATED_CLASS",
        observed_confidence=origin_mutation.confidence,
        reason="placebo_control_rejection",
        causal_attribution="unrelated_placebo_cause",
        context_id=f"placebo-context::{args.sequence}::v1.1",
        substrate_id=origin["model_id"],
    )

    generator_id = f"generator::v1.1::{args.sequence}::{origin['model_id']}"
    adjudicator_id = "independent-adjudicator::cross-substrate-v1.1"
    generator = ConstraintGenerator(generator_id)
    adjudicator = IndependentAdjudicator(adjudicator_id)

    candidate = generator.generate(
        rejection,
        confidence_lt=prereg["constraint_threshold"],
        allow_if_compensating_guard=True,
    )
    rule_a, receipt_a = adjudicator.adjudicate(candidate, promote=True, evidence_ok=True)
    rule_b, receipt_b = adjudicator.adjudicate(candidate, promote=False, evidence_ok=True)

    placebo_candidate = generator.generate(
        placebo,
        confidence_lt=prereg["constraint_threshold"],
        allow_if_compensating_guard=True,
    )
    rule_c, receipt_c = adjudicator.adjudicate(placebo_candidate, promote=True, evidence_ok=True)

    ablated_candidate = generator.generate(
        rejection,
        confidence_lt=prereg["constraint_threshold"],
        allow_if_compensating_guard=True,
        ablate_attribution=True,
    )
    rule_d, receipt_d = adjudicator.adjudicate(ablated_candidate, promote=True, evidence_ok=True)

    authority_ablation_rejected = False
    try:
        IndependentAdjudicator(generator_id).adjudicate(candidate, promote=True, evidence_ok=True)
    except AuthoritySeparationError:
        authority_ablation_rejected = True
    if not authority_ablation_rejected:
        raise RuntimeError("generator self-adjudication did not fail closed")

    core = {
        "schema": "matverse.cross_substrate_governance_pack/1.1",
        "experiment_id": prereg["experiment_id"],
        "sequence_id": args.sequence,
        "prereg_sha256": prereg_sha,
        "causal_runtime_commit": prereg["pinned_components"]["causal_runtime_commit"],
        "origin_model_id": origin["model_id"],
        "origin_model_revision": origin.get("model_revision"),
        "origin_runtime_result_sha256": sha256_file(origin_path),
        "normalized_origin_mutation": normalized,
        "origin_rejection_evidence": {
            "mutation": origin_mutation.model_dump(),
            "seed_rule": seed_rule.model_dump(),
            "decision": rejection_decision.model_dump(),
            "rejection": rejection.model_dump(),
        },
        "generator_id": generator_id,
        "adjudicator_id": adjudicator_id,
        "authority_ablation_rejected": authority_ablation_rejected,
        "candidate": candidate.model_dump(),
        "rules": {
            "A_PROMOTED_MATCHING": rule_a.model_dump(),
            "B_NOT_PROMOTED": rule_b.model_dump(),
            "C_PLACEBO": rule_c.model_dump(),
            "D_ATTRIBUTION_ABLATION": rule_d.model_dump(),
        },
        "receipts": {
            "A_PROMOTED_MATCHING": receipt_a.model_dump(),
            "B_NOT_PROMOTED": receipt_b.model_dump(),
            "C_PLACEBO": receipt_c.model_dump(),
            "D_ATTRIBUTION_ABLATION": receipt_d.model_dump(),
        },
        "context_exported_to_target_model": False,
        "governance_state_external_to_models": True,
        "promotion_completed_before_target_job": True,
    }
    pack = {**core, "governance_pack_sha256": sha256_obj(core)}
    Path(args.output).write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "sequence_id": args.sequence,
        "origin_model_id": origin["model_id"],
        "authority_ablation_rejected": authority_ablation_rejected,
        "governance_pack_sha256": pack["governance_pack_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
