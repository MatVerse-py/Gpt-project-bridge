from __future__ import annotations

import math
from typing import Any, Callable, Mapping

from app.evidence import evidence_receipt


class TSCAdapter:
    """Semantic transport comparator for QeX L0; not a statistical test."""

    HARD_FIELDS = (
        "experiment_id",
        "contract_hash",
        "problem_hash",
        "metric_schema_hash",
        "observable_schema_hash",
        "evidence_policy_hash",
        "result",
    )
    NUMERIC_FIELDS = ("probability_0", "probability_1")
    TOLERANCES = {"probability_0": 1e-12, "probability_1": 1e-12}

    def _with_receipt(
        self,
        report: dict[str, Any],
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> dict[str, Any]:
        policy = {
            "hard_fields": list(self.HARD_FIELDS),
            "numeric_fields": list(self.NUMERIC_FIELDS),
            "numeric_tolerances": dict(self.TOLERANCES),
            "statistical_equivalence": "NOT_ASSESSED",
        }
        report["receipt"] = evidence_receipt(
            "QEX_SUBSTRATE_COMPARISON",
            {"left": dict(left), "right": dict(right), "policy": policy},
            dict(report),
        )
        return report

    def compare(
        self,
        *,
        left: Mapping[str, Any],
        right: Mapping[str, Any],
        comparator: Callable[[Mapping[str, Any], Mapping[str, Any]], str],
    ) -> dict[str, Any]:
        missing = [key for key in self.HARD_FIELDS + self.NUMERIC_FIELDS if key not in left or key not in right]
        if missing:
            return self._with_receipt({
                "status": "INCOMPARABLE",
                "preserved": [],
                "approximated": [],
                "lost": sorted(set(missing)),
                "statistical_equivalence": "NOT_ASSESSED",
            }, left, right)

        preserved = [key for key in self.HARD_FIELDS if left[key] == right[key]]
        lost = [key for key in self.HARD_FIELDS if left[key] != right[key]]
        approximated: list[str] = []
        deltas: dict[str, float] = {}
        for key in self.NUMERIC_FIELDS:
            try:
                a, b = float(left[key]), float(right[key])
            except (TypeError, ValueError, OverflowError):
                lost.append(key)
                continue
            if not math.isfinite(a) or not math.isfinite(b) or not (0 <= a <= 1 and 0 <= b <= 1):
                lost.append(key)
                continue
            delta = abs(a - b)
            deltas[key] = delta
            if delta == 0:
                preserved.append(key)
            else:
                approximated.append(key)

        if lost:
            status = "DIVERGENT" if any(key in self.HARD_FIELDS for key in lost) else "INCOMPARABLE"
        else:
            status = comparator(left, right)
        if status == "STATISTICALLY_EQUIVALENT":
            status = "INCOMPARABLE"
        return self._with_receipt({
            "status": status,
            "preserved": sorted(set(preserved)),
            "approximated": sorted(set(approximated)),
            "lost": sorted(set(lost)),
            "statistical_equivalence": "NOT_ASSESSED",
            "numeric_deltas": deltas,
            "numeric_tolerances": dict(self.TOLERANCES),
            "comparison_scope": "L0_CLASSICAL_VS_IDEAL_STATEVECTOR",
        }, left, right)
