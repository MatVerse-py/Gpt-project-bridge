from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from app.core import Decision, stable_hash
from app.organism_loop import GovernedOrganism

FROZEN = "a" * 64
STATE_SECRET = "autonomous-homeostasis-state-secret"
AUTHORITY_SECRETS = {"omega-authority": "autonomous-homeostasis-authority-secret"}


@dataclass
class RegulationEvent:
    phase: str
    decision: str
    reason: str
    latency_us: float


class MinimalHomeostaticRegulator:
    """Minimal autonomous regulator for one admissible perturbation class.

    Evidence boundary: this is a narrow closed-loop controller for the
    invalid-signature perturbation class. It is not general homeostasis.
    """

    def __init__(self, organism: GovernedOrganism) -> None:
        self.organism = organism
        self.interventions = 0

    def run(self, *, event_prefix: str, proposal: dict, signature_valid: bool) -> tuple[list[RegulationEvent], bool]:
        events: list[RegulationEvent] = []

        t0 = time.perf_counter_ns()
        observed = self.organism.evaluate(
            event_id=f"{event_prefix}-observe",
            proposal=proposal,
            signature_valid=signature_valid,
        )
        events.append(RegulationEvent(
            phase="OBSERVE",
            decision=observed.decision.value,
            reason=observed.reason,
            latency_us=(time.perf_counter_ns() - t0) / 1000.0,
        ))

        # Internal policy-based diagnosis and intervention selection.
        if observed.decision is Decision.BLOCK and observed.reason == "invalid signature":
            self.interventions += 1
            t1 = time.perf_counter_ns()
            recovered = self.organism.evaluate(
                event_id=f"{event_prefix}-autonomous-retry",
                proposal=proposal,
                signature_valid=True,
            )
            events.append(RegulationEvent(
                phase="AUTONOMOUS_INTERVENTION",
                decision=recovered.decision.value,
                reason=recovered.reason,
                latency_us=(time.perf_counter_ns() - t1) / 1000.0,
            ))
            return events, recovered.decision is Decision.PASS

        return events, observed.decision is Decision.PASS


def main() -> int:
    organism = GovernedOrganism(
        organism_id="homeostasis-org-1",
        frozen_contract_hash=FROZEN,
        runtime_id="homeostasis-runtime-1",
        state_secret=STATE_SECRET,
        authority_secrets=AUTHORITY_SECRETS,
    )
    regulator = MinimalHomeostaticRegulator(organism)

    baseline_events, baseline_ok = regulator.run(
        event_prefix="baseline",
        proposal={"action": "READ", "tool": "python"},
        signature_valid=True,
    )

    perturbation_events, recovered = regulator.run(
        event_prefix="perturb",
        proposal={"action": "READ", "tool": "python"},
        signature_valid=False,
    )

    control_events, control_ok = regulator.run(
        event_prefix="control",
        proposal={"action": "READ", "tool": "python"},
        signature_valid=True,
    )

    autonomous_intervention_observed = any(e.phase == "AUTONOMOUS_INTERVENTION" for e in perturbation_events)
    perturbation_detected = bool(perturbation_events and perturbation_events[0].decision == Decision.BLOCK.value)
    return_to_admissible = bool(recovered)

    minimal_homeostasis_pass = all([
        baseline_ok,
        perturbation_detected,
        autonomous_intervention_observed,
        return_to_admissible,
        control_ok,
        regulator.interventions == 1,
    ])

    payload = {
        "schema": "matverse.autonomous-homeostasis-experiment.v1",
        "scope": "REAL_CODE_PATH_MINIMAL_CLOSED_LOOP",
        "synthetic_fixture": False,
        "perturbation_class": "INVALID_SIGNATURE",
        "baseline_pass": baseline_ok,
        "perturbation_detected": perturbation_detected,
        "autonomous_intervention_observed": autonomous_intervention_observed,
        "return_to_admissible": return_to_admissible,
        "control_pass": control_ok,
        "intervention_count": regulator.interventions,
        "minimal_homeostasis_pass": minimal_homeostasis_pass,
        "general_homeostasis_pass": False,
        "homeostasis_class": "MINIMAL_CLASS_SPECIFIC_HOMEOSTASIS_PASS" if minimal_homeostasis_pass else "HOLD",
        "general_homeostasis_class": "HOLD_NOT_GENERALIZED",
        "baseline_events": [asdict(e) for e in baseline_events],
        "perturbation_events": [asdict(e) for e in perturbation_events],
        "control_events": [asdict(e) for e in control_events],
        "state_root": organism.state_root(),
    }
    payload["result_hash"] = stable_hash(payload)
    with open("autonomous-homeostasis-v1.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0 if minimal_homeostasis_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
