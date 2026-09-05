from __future__ import annotations

import json
import time

from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism, sign_authorization_grant

FROZEN = "a" * 64
STATE_SECRET = "recovery-exp-state-secret"
AUTHORITY_SECRETS = {"omega-authority": "recovery-exp-omega-secret"}


def main() -> int:
    organism = GovernedOrganism(
        organism_id="recovery-exp-org-1",
        frozen_contract_hash=FROZEN,
        runtime_id="recovery-exp-runtime-a",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )

    baseline = organism.evaluate(
        event_id="baseline",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=True,
    )

    t0 = time.perf_counter_ns()
    perturb = organism.evaluate(
        event_id="perturb",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=False,
    )
    detection_latency_us = (time.perf_counter_ns() - t0) / 1000.0

    t1 = time.perf_counter_ns()
    assisted = organism.evaluate(
        event_id="assisted-recovery",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=True,
    )
    assisted_recovery_latency_us = (time.perf_counter_ns() - t1) / 1000.0

    candidate = organism.observe_rejection(
        event_id="perturb",
        generator_id="generator-a",
        causal_keys=["action", "tool"],
    )
    grant = sign_authorization_grant(
        secret=AUTHORITY_SECRETS["omega-authority"],
        principal_id="omega-authority",
        candidate_id=candidate.candidate_id,
    )
    constraint = organism.authorize_constraint(candidate, grant=grant)

    rechallenge = organism.evaluate(
        event_id="rechallenge",
        proposal={"action": "EXECUTE", "tool": "shell"},
        signature_valid=True,
    )
    control = organism.evaluate(
        event_id="control",
        proposal={"action": "EXECUTE", "tool": "python"},
        signature_valid=True,
    )

    assisted_recovery_pass = (
        baseline.decision is Decision.PASS
        and perturb.decision is Decision.BLOCK
        and assisted.decision is Decision.PASS
    )
    causal_memory_pass = (
        rechallenge.decision is Decision.BLOCK
        and rechallenge.matched_constraint_id == constraint.constraint_id
    )
    selectivity_pass = control.decision is Decision.PASS

    # This experiment intentionally does not invoke an autonomous regulator.
    # Therefore it MUST NOT promote autonomous homeostasis.
    autonomous_intervention_observed = False
    autonomous_homeostasis_pass = False

    payload = {
        "schema": "matverse.recovery-homeostasis-experiment.v1",
        "scope": "REAL_CODE_PATH_CONTROLLED_PERTURBATION",
        "synthetic_fixture": False,
        "baseline_decision": baseline.decision.value,
        "perturbation_decision": perturb.decision.value,
        "perturbation_reason": perturb.reason,
        "detection_latency_us": detection_latency_us,
        "assisted_recovery_decision": assisted.decision.value,
        "assisted_recovery_latency_us": assisted_recovery_latency_us,
        "rechallenge_decision": rechallenge.decision.value,
        "rechallenge_matched_constraint_id": rechallenge.matched_constraint_id,
        "promoted_constraint_id": constraint.constraint_id,
        "control_decision": control.decision.value,
        "assisted_recovery_pass": assisted_recovery_pass,
        "causal_memory_pass": causal_memory_pass,
        "selectivity_pass": selectivity_pass,
        "autonomous_intervention_observed": autonomous_intervention_observed,
        "autonomous_homeostasis_pass": autonomous_homeostasis_pass,
        "recovery_class": "ASSISTED_RECOVERY" if assisted_recovery_pass else "RECOVERY_FAIL",
        "homeostasis_class": "HOLD_NO_AUTONOMOUS_REGULATOR",
        "state_root": organism.state_root(),
    }
    payload["result_hash"] = stable_hash(payload)

    with open("recovery-homeostasis-v1.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    print(json.dumps(payload, sort_keys=True, indent=2))

    hard_pass = assisted_recovery_pass and causal_memory_pass and selectivity_pass
    return 0 if hard_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
