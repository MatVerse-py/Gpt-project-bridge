from __future__ import annotations

import hashlib
import heapq
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from statistics import NormalDist
from typing import Any, Callable, Iterable, Mapping, Sequence


class Direction(str, Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


@dataclass(frozen=True)
class Criterion:
    """Normalize a raw criterion into dimensionless goodness g in [0, 1]."""

    name: str
    direction: Direction
    lo: float
    hi: float
    scale: str = "linear"

    def __post_init__(self) -> None:
        if self.hi <= self.lo:
            raise ValueError(f"{self.name}: hi must be > lo")
        if self.scale not in {"linear", "log"}:
            raise ValueError(f"{self.name}: unsupported scale {self.scale!r}")
        if self.scale == "log" and self.lo <= 0:
            raise ValueError(f"{self.name}: log scale requires lo > 0")

    def goodness(self, raw: float) -> float:
        lo, hi, value = self.lo, self.hi, float(raw)
        if self.scale == "log":
            lo = math.log10(lo)
            hi = math.log10(hi)
            value = math.log10(max(value, 1e-12))
        t = min(1.0, max(0.0, (value - lo) / (hi - lo)))
        return t if self.direction is Direction.HIGHER_IS_BETTER else 1.0 - t


@dataclass(frozen=True)
class CapabilityNode:
    node_id: str
    layer: str
    raw: Mapping[str, float]
    attrs: Mapping[str, Any] = field(default_factory=dict)


HardConstraint = Callable[[CapabilityNode], str | None]


def metric_floor(metric: str, minimum: float) -> HardConstraint:
    def check(node: CapabilityNode) -> str | None:
        value = node.raw.get(metric)
        if value is None:
            return f"missing:{metric}"
        value = float(value)
        return None if value >= minimum else f"{metric}={value:.6g}<{minimum:.6g}"
    return check


def metric_ceiling(metric: str, maximum: float) -> HardConstraint:
    def check(node: CapabilityNode) -> str | None:
        value = node.raw.get(metric)
        if value is None:
            return f"missing:{metric}"
        value = float(value)
        return None if value <= maximum else f"{metric}={value:.6g}>{maximum:.6g}"
    return check


def requires_attr(key: str, expected: Any) -> HardConstraint:
    def check(node: CapabilityNode) -> str | None:
        actual = node.attrs.get(key)
        return None if actual == expected else f"{key}={actual!r}!={expected!r}"
    return check


@dataclass(frozen=True)
class GateDecision:
    node_id: str
    admissible: bool
    reasons: tuple[str, ...]


@dataclass
class AdmissibilityGate:
    """Fail closed: constraints are predicates, never weighted preferences."""

    constraints: Sequence[HardConstraint]

    def evaluate(self, node: CapabilityNode) -> GateDecision:
        reasons = tuple(reason for check in self.constraints if (reason := check(node)) is not None)
        return GateDecision(node.node_id, not reasons, reasons)

    def filter(self, nodes: Iterable[CapabilityNode]) -> tuple[list[CapabilityNode], dict[str, list[str]]]:
        admissible: list[CapabilityNode] = []
        blocked: dict[str, list[str]] = {}
        for node in nodes:
            decision = self.evaluate(node)
            if decision.admissible:
                admissible.append(node)
            else:
                blocked[node.node_id] = list(decision.reasons)
        return admissible, blocked


@dataclass
class PreferenceModel:
    """Multi-attribute preference over already-admissible candidates."""

    criteria: Mapping[str, Criterion]
    weights: Mapping[str, float]

    def __post_init__(self) -> None:
        if set(self.weights) - set(self.criteria):
            raise ValueError("every weight must have a criterion")
        if any(weight < 0 for weight in self.weights.values()):
            raise ValueError("negative weights are forbidden")
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0, got {total:.12f}")

    def goodness_vector(self, node: CapabilityNode) -> dict[str, float]:
        missing = set(self.weights) - set(node.raw)
        if missing:
            raise KeyError(f"{node.node_id}: missing criteria {sorted(missing)}")
        return {name: self.criteria[name].goodness(float(node.raw[name])) for name in self.weights}

    def score(self, node: CapabilityNode) -> float:
        vector = self.goodness_vector(node)
        return sum(self.weights[name] * vector[name] for name in self.weights)

    def potential(self, node: CapabilityNode) -> float:
        return 1.0 - self.score(node)

    def sensitivity(self, nodes: Sequence[CapabilityNode], delta: float = 0.10) -> dict[str, Any]:
        if not nodes:
            raise ValueError("sensitivity requires at least one admissible node")
        baseline = max(nodes, key=self.score).node_id
        trials = 0
        flips: list[dict[str, Any]] = []
        for key in self.weights:
            for sign in (1.0, -1.0):
                candidate = dict(self.weights)
                candidate[key] = max(0.0, candidate[key] + sign * delta)
                total = sum(candidate.values())
                if total <= 0:
                    continue
                candidate = {k: v / total for k, v in candidate.items()}
                trials += 1
                winner = max(nodes, key=PreferenceModel(self.criteria, candidate).score).node_id
                if winner != baseline:
                    flips.append({"weight": key, "shift": sign * delta, "winner": winner})
        return {
            "baseline_winner": baseline,
            "trials": trials,
            "flips": flips,
            "fragility": len(flips) / trials if trials else 0.0,
        }


@dataclass(frozen=True)
class Crossing:
    src: str
    dst: str
    cost: float
    reason: str = ""

    def __post_init__(self) -> None:
        if self.cost < 0:
            raise ValueError("crossing cost must be non-negative")


@dataclass(frozen=True)
class RoutingResult:
    path: tuple[str, ...]
    total_cost: float
    crossing_cost: float
    terminal_potential: float
    blocked: Mapping[str, Sequence[str]]
    receipt_sha256: str


class CapabilityGraph:
    """Constrained shortest-path routing on the admissible subgraph."""

    def __init__(
        self,
        nodes: Sequence[CapabilityNode],
        crossings: Sequence[Crossing],
        gate: AdmissibilityGate,
        preference: PreferenceModel,
    ) -> None:
        self.nodes = {node.node_id: node for node in nodes}
        self.crossings = tuple(crossings)
        self.gate = gate
        self.preference = preference
        self.admissible, self.blocked = gate.filter(nodes)
        self._admissible_ids = {node.node_id for node in self.admissible}
        self._adj: dict[str, list[Crossing]] = {node_id: [] for node_id in self._admissible_ids}
        for crossing in crossings:
            if crossing.src in self._admissible_ids and crossing.dst in self._admissible_ids:
                self._adj[crossing.src].append(crossing)

    def route(self, origin: str, targets: Sequence[str] | None = None) -> RoutingResult:
        if origin not in self._admissible_ids:
            raise ValueError(f"origin {origin!r} is missing or inadmissible")
        candidates = self._admissible_ids if targets is None else self._admissible_ids.intersection(targets)
        if not candidates:
            raise ValueError("no admissible targets")

        distance: dict[str, float] = {origin: 0.0}
        previous: dict[str, str] = {}
        queue: list[tuple[float, str]] = [(0.0, origin)]
        while queue:
            current_distance, current = heapq.heappop(queue)
            if current_distance != distance.get(current):
                continue
            for crossing in self._adj[current]:
                candidate = current_distance + crossing.cost
                if candidate < distance.get(crossing.dst, math.inf):
                    distance[crossing.dst] = candidate
                    previous[crossing.dst] = current
                    heapq.heappush(queue, (candidate, crossing.dst))

        reachable = [node_id for node_id in candidates if node_id in distance]
        if not reachable:
            raise ValueError("no admissible target is reachable")
        terminal = min(
            reachable,
            key=lambda node_id: distance[node_id] + self.preference.potential(self.nodes[node_id]),
        )
        path = [terminal]
        while path[-1] in previous:
            path.append(previous[path[-1]])
        path.reverse()
        crossing_cost = distance[terminal]
        terminal_potential = self.preference.potential(self.nodes[terminal])
        total_cost = crossing_cost + terminal_potential
        receipt_payload = {
            "path": path,
            "crossing_cost": round(crossing_cost, 12),
            "terminal_potential": round(terminal_potential, 12),
            "total_cost": round(total_cost, 12),
            "blocked": {key: sorted(value) for key, value in sorted(self.blocked.items())},
        }
        receipt = hashlib.sha256(
            json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return RoutingResult(
            path=tuple(path),
            total_cost=total_cost,
            crossing_cost=crossing_cost,
            terminal_potential=terminal_potential,
            blocked=self.blocked,
            receipt_sha256=receipt,
        )

    def conservativity_report(self, tolerance: float = 1e-9) -> dict[str, Any]:
        """Test whether edge costs admit a scalar potential on the admissible graph."""
        phi: dict[str, float] = {}
        for root in sorted(self._admissible_ids):
            if root in phi:
                continue
            phi[root] = 0.0
            stack = [root]
            while stack:
                current = stack.pop()
                for crossing in self._adj[current]:
                    candidate = phi[current] + crossing.cost
                    if crossing.dst not in phi:
                        phi[crossing.dst] = candidate
                        stack.append(crossing.dst)
        violations: list[dict[str, Any]] = []
        for crossing in self.crossings:
            if crossing.src in phi and crossing.dst in phi:
                residual = crossing.cost - (phi[crossing.dst] - phi[crossing.src])
                if abs(residual) > tolerance:
                    violations.append({"edge": f"{crossing.src}->{crossing.dst}", "residual": residual})
        return {"conservative": not violations, "violations": violations}


def informational_mass(
    artifact_id: str,
    decisions: Sequence[Callable[[set[str]], str]],
    evidence_set: set[str],
) -> dict[str, Any]:
    if artifact_id not in evidence_set:
        raise ValueError(f"{artifact_id!r} not present in evidence set")
    without = evidence_set - {artifact_id}
    flipped = [index for index, decision in enumerate(decisions) if decision(evidence_set) != decision(without)]
    return {
        "artifact": artifact_id,
        "decisions_total": len(decisions),
        "decisions_flipped": flipped,
        "mass": len(flipped) / len(decisions) if decisions else 0.0,
    }


class EquivalenceVerdict(str, Enum):
    EQUIVALENT = "EQUIVALENT"
    INCONCLUSIVE = "INCONCLUSIVE"
    DIVERGENT = "DIVERGENT"


def tost_equivalence(
    mean_a: float,
    sd_a: float,
    n_a: int,
    mean_b: float,
    sd_b: float,
    n_b: int,
    *,
    margin: float,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Two one-sided equivalence tests using the normal approximation."""
    if margin <= 0:
        raise ValueError("equivalence margin must be > 0")
    if n_a < 2 or n_b < 2:
        raise ValueError("n_a and n_b must be >= 2")
    standard_error = math.sqrt(sd_a**2 / n_a + sd_b**2 / n_b)
    difference = mean_a - mean_b
    if standard_error == 0:
        equivalent = abs(difference) < margin
        return {
            "diff": difference,
            "se": 0.0,
            "p_value": 0.0 if equivalent else 1.0,
            "margin": margin,
            "verdict": EquivalenceVerdict.EQUIVALENT.value if equivalent else EquivalenceVerdict.DIVERGENT.value,
        }
    normal = NormalDist()
    p_lower = 1.0 - normal.cdf((difference + margin) / standard_error)
    p_upper = normal.cdf((difference - margin) / standard_error)
    p_value = max(p_lower, p_upper)
    z = normal.inv_cdf(1.0 - alpha)
    ci = (difference - z * standard_error, difference + z * standard_error)
    if p_value < alpha:
        verdict = EquivalenceVerdict.EQUIVALENT
    elif ci[0] > margin or ci[1] < -margin:
        verdict = EquivalenceVerdict.DIVERGENT
    else:
        verdict = EquivalenceVerdict.INCONCLUSIVE
    return {
        "diff": difference,
        "se": standard_error,
        "p_value": p_value,
        "ci90": ci,
        "margin": margin,
        "verdict": verdict.value,
    }
