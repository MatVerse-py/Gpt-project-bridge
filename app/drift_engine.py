from __future__ import annotations

import math
from dataclasses import dataclass
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


def _validate_distribution(p: Mapping[str, float], name: str) -> None:
    if not p:
        raise ValueError(f"{name} não pode ser vazia")
    if any(not math.isfinite(v) or v < 0.0 for v in p.values()):
        raise ValueError(f"{name} deve conter probabilidades finitas não negativas")
    total = sum(p.values())
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"{name} deve somar 1; soma={total}")


def chernoff_information(
    pa: Mapping[str, float], pb: Mapping[str, float], grid: int = 101
) -> float:
    """Calcula C(pa,pb)=-log(min_s sum_r pa[r]**s pb[r]**(1-s))."""
    _validate_distribution(pa, "pa")
    _validate_distribution(pb, "pb")
    if grid < 2:
        raise ValueError("grid deve ser >= 2")
    classes = set(pa) | set(pb)
    eps = 1e-12
    best = float("inf")
    for i in range(grid):
        s = i / (grid - 1)
        acc = sum(
            (max(pa.get(r, 0.0), eps) ** s)
            * (max(pb.get(r, 0.0), eps) ** (1.0 - s))
            for r in classes
        )
        best = min(best, max(acc, eps))
    return max(0.0, -math.log(best))


@dataclass(frozen=True)
class LensReading:
    lens_id: str
    from_telemetry_window: bool
    per_probe: tuple[float, ...]
    onset: float
    ci_low: float
    ci_high: float

    def __post_init__(self) -> None:
        if not self.lens_id:
            raise ValueError("lens_id não pode ser vazio")
        if not all(math.isfinite(x) and x >= 0 for x in self.per_probe):
            raise ValueError("per_probe deve conter valores finitos não negativos")
        if not all(math.isfinite(x) for x in (self.onset, self.ci_low, self.ci_high)):
            raise ValueError("onset e intervalo de confiança devem ser finitos")
        if self.ci_low > self.ci_high:
            raise ValueError("ci_low não pode ser maior que ci_high")

    def mean_c(self) -> float:
        return sum(self.per_probe) / len(self.per_probe) if self.per_probe else 0.0

    def classify(self) -> str:
        if not self.from_telemetry_window or not self.per_probe:
            return "silence"
        if self.ci_low > self.onset:
            return "drift"
        if self.ci_high <= self.onset:
            return "no_drift"
        return "undecided"


@dataclass(frozen=True)
class AscCertificate:
    kappa: float
    omega_hat: float
    spectral_entropy: float
    null_baseline_passed: bool

    def certifies_preservation(self) -> bool:
        return self.null_baseline_passed


@dataclass(frozen=True)
class ViabilityReading:
    regulator_variety: float
    disturbance_variety: float

    def feasible(self) -> bool:
        return self.regulator_variety >= self.disturbance_variety


class TwoAxisAdjudicator:
    def __init__(self, quorum_k: int = 2, aggregation: str = "exists") -> None:
        if quorum_k < 1:
            raise ValueError("quorum_k deve ser >= 1")
        if aggregation not in ("exists", "forall"):
            raise ValueError("agregação deve ser 'exists' (fail-closed) ou 'forall'")
        self.k = quorum_k
        self.aggregation = aggregation

    def adjudicate(
        self,
        lineage_continuous: bool | None,
        lenses: Sequence[LensReading],
        viability: ViabilityReading | None = None,
        asc: AscCertificate | None = None,
    ) -> tuple[Verdict, dict]:
        if viability is not None and not viability.feasible():
            return Verdict.INFEASIBLE, {"reason": "ashby: variedade do regulador insuficiente"}

        labels = [lens.classify() for lens in lenses]
        detail = {
            lens.lens_id: (label, round(lens.mean_c(), 4), lens.onset)
            for lens, label in zip(lenses, labels)
        }
        if not lenses or all(label == "silence" for label in labels):
            return Verdict.SILENCE, detail
        if lineage_continuous is None:
            return Verdict.ESCALATE, {**detail, "reason": "linhagem indecidível"}
        if asc is not None and not asc.certifies_preservation():
            return Verdict.ESCALATE, {**detail, "reason": "ASC sem certificado de preservação"}

        drifting = [label for label in labels if label == "drift"]
        undecided = [label for label in labels if label == "undecided"]
        if 0 < len(drifting) < self.k:
            return Verdict.DISSENSO, {**detail, "reason": "lentes discordam sob o mesmo par"}
        if undecided and not drifting:
            return Verdict.ESCALATE, {**detail, "reason": "IC cruza o onset"}

        drift = bool(drifting) if self.aggregation == "exists" else all(
            label == "drift" for label in labels
        )
        if lineage_continuous:
            return (Verdict.DRIFT if drift else Verdict.CONTINUITY), detail
        return (Verdict.DISTINCT if drift else Verdict.CLONE), detail


def effective_sample_size(n_cycles: int, icc: float) -> float:
    """DEFF de medidas agrupadas: N_eff=n/(1+(m-1)ICC), com m=1 ciclo médio."""
    if n_cycles < 0:
        raise ValueError("n_cycles deve ser não negativo")
    if not 0.0 <= icc < 1.0:
        raise ValueError("icc deve estar em [0, 1)")
    return float(n_cycles) / (1.0 + icc)


def required_probes(chernoff_c: float, delta: float) -> float:
    """Limite operacional n >= log(1/delta)/C; infinito quando C=0."""
    if chernoff_c < 0 or not math.isfinite(chernoff_c):
        raise ValueError("chernoff_c deve ser finito e não negativo")
    if not 0 < delta < 1:
        raise ValueError("delta deve estar em (0,1)")
    if chernoff_c == 0:
        return math.inf
    return math.log(1.0 / delta) / chernoff_c
