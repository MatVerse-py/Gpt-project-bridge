from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping


class TemporalRelation(str, Enum):
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    SIMULTANEOUS_WITHIN_BOUND = "SIMULTANEOUS_WITHIN_BOUND"
    INDETERMINATE = "INDETERMINATE"


def _positive_decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"{field} must be decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be finite and > 0")
    return parsed


@dataclass(frozen=True)
class MetrologicalTemporalState:
    measured_time: str
    clock_reference: str
    uncertainty_seconds: str
    reference_precision_seconds: str
    traceability: str
    reference_frame: str | None = None
    spatial_state: Mapping[str, Any] | None = None
    geopotential_m2_s2: str | None = None

    def validate(self) -> None:
        for name in ("measured_time", "clock_reference", "traceability"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        uncertainty = _positive_decimal(self.uncertainty_seconds, "uncertainty_seconds")
        reference_precision = _positive_decimal(
            self.reference_precision_seconds, "reference_precision_seconds"
        )
        if uncertainty < reference_precision:
            raise ValueError("claimed uncertainty exceeds clock/reference precision")
        if self.geopotential_m2_s2 is not None:
            try:
                value = Decimal(self.geopotential_m2_s2)
            except (InvalidOperation, TypeError) as exc:
                raise ValueError("geopotential_m2_s2 must be decimal") from exc
            if not value.is_finite():
                raise ValueError("geopotential_m2_s2 must be finite")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def normalize_metrology(value: Mapping[str, Any] | MetrologicalTemporalState | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, MetrologicalTemporalState):
        return value.as_dict()
    if not isinstance(value, Mapping):
        raise ValueError("metrology must be a mapping or MetrologicalTemporalState")
    allowed = {
        "measured_time", "clock_reference", "uncertainty_seconds",
        "reference_precision_seconds", "traceability", "reference_frame",
        "spatial_state", "geopotential_m2_s2",
    }
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(f"unsupported metrology fields: {', '.join(unexpected)}")
    try:
        state = MetrologicalTemporalState(**dict(value))
    except TypeError as exc:
        raise ValueError(f"invalid metrology state: {exc}") from exc
    return state.as_dict()


def compare_temporal_intervals(
    center_a_seconds: str,
    uncertainty_a_seconds: str,
    center_b_seconds: str,
    uncertainty_b_seconds: str,
) -> TemporalRelation:
    a = Decimal(center_a_seconds)
    ua = _positive_decimal(uncertainty_a_seconds, "uncertainty_a_seconds")
    b = Decimal(center_b_seconds)
    ub = _positive_decimal(uncertainty_b_seconds, "uncertainty_b_seconds")
    a_lo, a_hi = a - ua, a + ua
    b_lo, b_hi = b - ub, b + ub
    if a_hi < b_lo:
        return TemporalRelation.BEFORE
    if b_hi < a_lo:
        return TemporalRelation.AFTER
    if a == b and ua == ub:
        return TemporalRelation.SIMULTANEOUS_WITHIN_BOUND
    return TemporalRelation.INDETERMINATE
