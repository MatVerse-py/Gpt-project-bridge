"""Fail-closed audit for historical identity-drift evidence.

Narrative/documentary evidence is not promoted to DRIFT. The minimum measurement
contract is: causal-lineage status + legitimate TelemetryWindow observations +
per-lens Chernoff measurements + lens-specific onset and confidence interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence


class Verdict(str, Enum):
    CONTINUITY = "CONTINUITY"
    DRIFT = "DRIFT"
    DISSENSO = "DISSENSO"
    CLONE = "CLONE"
    DISTINCT = "DISTINCT"
    SILENCE = "SILENCE"
    ESCALATE = "ESCALATE"
    INFEASIBLE = "INFEASIBLE"


@dataclass(frozen=True)
class LensEvidence:
    lens_id: str
    from_telemetry_window: bool
    per_probe_chernoff: tuple[float, ...] = ()
    onset: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None

    def classify(self) -> str:
        if (
            not self.from_telemetry_window
            or not self.per_probe_chernoff
            or self.onset is None
            or self.ci_low is None
            or self.ci_high is None
        ):
            return "silence"
        if self.ci_low > self.onset:
            return "drift"
        if self.ci_high <= self.onset:
            return "no_drift"
        return "undecided"


@dataclass(frozen=True)
class HistoricalCase:
    case_id: str
    description: str
    lineage_continuous: bool | None
    lenses: tuple[LensEvidence, ...] = ()
    documentary_facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditResult:
    case_id: str
    verdict: Verdict
    reason: str
    documentary_facts: Mapping[str, object]


def adjudicate(case: HistoricalCase) -> AuditResult:
    labels = [lens.classify() for lens in case.lenses]

    if not case.lenses or all(label == "silence" for label in labels):
        return AuditResult(
            case.case_id,
            Verdict.SILENCE,
            "No legitimate TelemetryWindow/Chernoff/onset evidence survives in the historical artifact.",
            case.documentary_facts,
        )

    if case.lineage_continuous is None:
        return AuditResult(
            case.case_id,
            Verdict.ESCALATE,
            "Behavioral evidence exists but causal-lineage continuity is not independently established.",
            case.documentary_facts,
        )

    if "undecided" in labels and "drift" not in labels:
        return AuditResult(
            case.case_id,
            Verdict.ESCALATE,
            "At least one lens confidence interval crosses its onset.",
            case.documentary_facts,
        )

    drifting = sum(label == "drift" for label in labels)
    no_drift = sum(label == "no_drift" for label in labels)

    if drifting and no_drift:
        return AuditResult(
            case.case_id,
            Verdict.DISSENSO,
            "Legitimate observational lenses disagree; averaging is prohibited.",
            case.documentary_facts,
        )

    drift = drifting > 0
    if case.lineage_continuous:
        verdict = Verdict.DRIFT if drift else Verdict.CONTINUITY
    else:
        verdict = Verdict.DISTINCT if drift else Verdict.CLONE
    return AuditResult(case.case_id, verdict, "Two-axis adjudication completed.", case.documentary_facts)


def historical_cases_from_corpus() -> Sequence[HistoricalCase]:
    """Documentary facts preserved by the corpus; no measurements are invented."""
    return (
        HistoricalCase(
            case_id="HIST-OMEGA-WEIGHTS",
            description="Divergent Omega weights between two MatVerse skills.",
            lineage_continuous=None,
            documentary_facts={
                "weights_a": [0.40, 0.30, 0.20, 0.10],
                "weights_b": [0.40, 0.25, 0.25, 0.10],
                "mitigation": "weights injected into gate_fingerprint",
                "evidence_scope": "documentary",
            },
        ),
        HistoricalCase(
            case_id="HIST-SKILL-CUTOFF",
            description="Post-2026-06-06 skill catalog/runtime coverage gap.",
            lineage_continuous=None,
            documentary_facts={
                "catalog_count": 238,
                "active_count": 58,
                "absent_approx": 180,
                "cutoff": "2026-06-06",
                "evidence_scope": "documentary",
            },
        ),
    )


def run_historical_audit() -> list[AuditResult]:
    return [adjudicate(case) for case in historical_cases_from_corpus()]


if __name__ == "__main__":
    for result in run_historical_audit():
        print(f"{result.case_id}: {result.verdict.value} — {result.reason}")
