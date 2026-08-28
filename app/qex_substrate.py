from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from app.core import Decision, evaluate_hdb, omega_gate, stable_hash
from app.evidence import evidence_receipt


class ComputeRegime(str, Enum):
    CLASSICAL = "CLASSICAL"
    HYBRID = "HYBRID"
    QUANTUM_GATE = "QUANTUM_GATE"
    QUANTUM_ANNEALING = "QUANTUM_ANNEALING"


class QuantumModality(str, Enum):
    SUPERCONDUCTING = "SUPERCONDUCTING"
    TRAPPED_ION = "TRAPPED_ION"
    PHOTONIC = "PHOTONIC"
    NEUTRAL_ATOM = "NEUTRAL_ATOM"
    TOPOLOGICAL = "TOPOLOGICAL"
    ANNEALING = "ANNEALING"


class ComparisonStatus(str, Enum):
    EXACT = "EXACT"
    FUNCTIONALLY_EQUIVALENT = "FUNCTIONALLY_EQUIVALENT"
    STATISTICALLY_EQUIVALENT = "STATISTICALLY_EQUIVALENT"
    DIVERGENT = "DIVERGENT"
    INCOMPARABLE = "INCOMPARABLE"


@dataclass(frozen=True)
class CapabilityProfile:
    backend_id: str
    regime: ComputeRegime
    modality: QuantumModality | None = None
    capabilities: tuple[str, ...] = ()
    maturity: str = "UNKNOWN"
    available: bool = True
    estimated_cost: float | None = None
    estimated_latency_ms: float | None = None
    error_rate: float | None = None
    coherence_us: float | None = None
    connectivity: str | None = None
    logical_qubits: int | None = None
    physical_qubits: int | None = None

    def __post_init__(self) -> None:
        if self.regime is ComputeRegime.QUANTUM_ANNEALING and self.modality not in (None, QuantumModality.ANNEALING):
            raise ValueError("annealing regime requires ANNEALING modality")
        if self.regime is ComputeRegime.CLASSICAL and self.modality is not None:
            raise ValueError("classical substrate cannot declare quantum modality")
        if self.error_rate is not None and not 0 <= self.error_rate <= 1:
            raise ValueError("error_rate must be in [0,1]")

    @property
    def profile_hash(self) -> str:
        return stable_hash(asdict(self))


@dataclass(frozen=True)
class ExperimentContract:
    experiment_id: str
    problem_hash: str
    objective: str
    required_capabilities: tuple[str, ...]
    metric_schema_hash: str
    observable_schema_hash: str
    evidence_policy_hash: str
    budget_max: float | None = None
    latency_max_ms: float | None = None
    allowed_regimes: tuple[ComputeRegime, ...] = tuple(ComputeRegime)
    require_classical_baseline: bool = True

    @property
    def contract_hash(self) -> str:
        return stable_hash(asdict(self))


@dataclass(frozen=True)
class CandidateAssessment:
    backend_id: str
    decision: Decision
    reasons: tuple[str, ...]
    preference_score: float | None


@dataclass(frozen=True)
class SelectionResult:
    contract_hash: str
    selected_backend_id: str | None
    assessments: tuple[CandidateAssessment, ...]
    receipt: Mapping[str, Any]


def assess_candidate(contract: ExperimentContract, profile: CapabilityProfile) -> CandidateAssessment:
    reasons: list[str] = []
    if not profile.available:
        reasons.append("backend unavailable")
    if profile.regime not in contract.allowed_regimes:
        reasons.append("regime not allowed by experiment contract")
    missing = sorted(set(contract.required_capabilities) - set(profile.capabilities))
    if missing:
        reasons.append("missing capabilities: " + ",".join(missing))
    if contract.budget_max is not None and (profile.estimated_cost is None or profile.estimated_cost > contract.budget_max):
        reasons.append("cost unknown or exceeds budget")
    if contract.latency_max_ms is not None and (
        profile.estimated_latency_ms is None or profile.estimated_latency_ms > contract.latency_max_ms
    ):
        reasons.append("latency unknown or exceeds limit")
    if profile.modality is QuantumModality.TOPOLOGICAL and profile.maturity.upper() not in {"VERIFIED", "PRODUCTION"}:
        reasons.append("topological modality is experimental/contested")

    if reasons:
        return CandidateAssessment(profile.backend_id, Decision.BLOCK, tuple(reasons), None)

    # Preference is only evaluated after hard admissibility. Lower is better.
    score = 0.0
    score += profile.estimated_cost or 0.0
    score += (profile.estimated_latency_ms or 0.0) / 1000.0
    score += (profile.error_rate or 0.0) * 1000.0
    return CandidateAssessment(profile.backend_id, Decision.PASS, (), score)


def select_substrate(
    contract: ExperimentContract,
    profiles: Iterable[CapabilityProfile],
    *,
    human: dict[str, Any] | None = None,
    ontology_ok: bool = True,
    signature_valid: bool = True,
    transition_valid: bool = True,
) -> SelectionResult:
    hdb = evaluate_hdb(human)
    gate, reason = omega_gate(
        hdb=hdb,
        action="EXECUTE",
        ontology_ok=ontology_ok,
        signature_valid=signature_valid,
        transition_valid=transition_valid,
    )
    profiles = tuple(profiles)
    if gate is not Decision.PASS:
        assessments = tuple(CandidateAssessment(p.backend_id, gate, (reason,), None) for p in profiles)
        receipt = evidence_receipt(
            "QEX_SUBSTRATE_SELECTION",
            {"contract_hash": contract.contract_hash},
            {"gate": gate.value, "selected_backend_id": None},
        )
        return SelectionResult(contract.contract_hash, None, assessments, receipt)

    assessments = tuple(assess_candidate(contract, p) for p in profiles)
    admissible = [a for a in assessments if a.decision is Decision.PASS]

    if contract.require_classical_baseline and not any(
        p.regime is ComputeRegime.CLASSICAL and a.decision is Decision.PASS
        for p, a in zip(profiles, assessments)
    ):
        receipt = evidence_receipt(
            "QEX_SUBSTRATE_SELECTION",
            {"contract_hash": contract.contract_hash},
            {"gate": "HOLD", "reason": "classical baseline required", "selected_backend_id": None},
        )
        return SelectionResult(contract.contract_hash, None, assessments, receipt)

    selected = min(admissible, key=lambda a: (a.preference_score if a.preference_score is not None else float("inf"), a.backend_id)) if admissible else None
    receipt = evidence_receipt(
        "QEX_SUBSTRATE_SELECTION",
        {"contract_hash": contract.contract_hash, "profile_hashes": [p.profile_hash for p in profiles]},
        {"selected_backend_id": selected.backend_id if selected else None, "assessments": [asdict(a) for a in assessments]},
    )
    return SelectionResult(contract.contract_hash, selected.backend_id if selected else None, assessments, receipt)


def compare_substrate_results(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    hard_invariants: tuple[str, ...],
    numeric_tolerances: Mapping[str, float] | None = None,
) -> ComparisonStatus:
    numeric_tolerances = numeric_tolerances or {}
    for key in hard_invariants:
        if key not in left or key not in right:
            return ComparisonStatus.INCOMPARABLE
        if left[key] != right[key]:
            return ComparisonStatus.DIVERGENT

    shared = set(left) & set(right)
    compared = False
    statistically_equal = False
    for key, tolerance in numeric_tolerances.items():
        if key not in shared:
            return ComparisonStatus.INCOMPARABLE
        try:
            delta = abs(float(left[key]) - float(right[key]))
        except (TypeError, ValueError):
            return ComparisonStatus.INCOMPARABLE
        compared = True
        if delta > tolerance:
            return ComparisonStatus.DIVERGENT
        if delta > 0:
            statistically_equal = True

    if left == right:
        return ComparisonStatus.EXACT
    if compared and statistically_equal:
        return ComparisonStatus.STATISTICALLY_EQUIVALENT
    return ComparisonStatus.FUNCTIONALLY_EQUIVALENT
