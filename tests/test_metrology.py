from __future__ import annotations

import pytest

from app.bridge_core import AnalyticStatus, EpistemicNature, build_envelope
from app.metrology import (
    MetrologicalTemporalState,
    TemporalRelation,
    compare_temporal_intervals,
)


def _base_kwargs() -> dict:
    return {
        "source": "sensor",
        "destination": "organism",
        "actor": "metrology-adapter",
        "objective_id": "temporal-reference",
        "capability": "observe.time",
        "payload": {"value": 1},
        "schema": "matverse.measurement.v1",
        "authority_ref": "authority:test",
        "policy_ref": "policy:test",
        "correlation_id": "corr-1",
        "epistemic_nature": EpistemicNature.RUNTIME,
        "analytic_status": AnalyticStatus.FACT,
        "timestamp": "2026-09-27T07:00:00+00:00",
    }


def test_legacy_envelope_remains_valid_without_metrology() -> None:
    envelope = build_envelope(**_base_kwargs())
    assert envelope.metrology is None
    assert envelope.as_dict()["metrology"] is None


def test_metrology_is_canonical_and_part_of_identity() -> None:
    kwargs = _base_kwargs()
    plain = build_envelope(**kwargs)
    measured = build_envelope(
        **kwargs,
        metrology=MetrologicalTemporalState(
            measured_time="2026-09-27T07:00:00Z",
            clock_reference="Lu176-optical-clock/example",
            uncertainty_seconds="5.7e-19",
            reference_precision_seconds="1e-19",
            traceability="experimental-reference",
            reference_frame="local-lab",
        ),
    )
    assert measured.metrology is not None
    assert measured.as_dict()["metrology"]["uncertainty_seconds"] == "5.7e-19"
    assert measured.envelope_id != plain.envelope_id


def test_precision_overclaim_fails_closed() -> None:
    with pytest.raises(ValueError, match="claimed uncertainty"):
        build_envelope(
            **_base_kwargs(),
            metrology={
                "measured_time": "2026-09-27T07:00:00Z",
                "clock_reference": "reference",
                "uncertainty_seconds": "1e-20",
                "reference_precision_seconds": "1e-19",
                "traceability": "test",
            },
        )


def test_unknown_metrology_field_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported metrology fields"):
        build_envelope(
            **_base_kwargs(),
            metrology={
                "measured_time": "2026-09-27T07:00:00Z",
                "clock_reference": "reference",
                "uncertainty_seconds": "1e-19",
                "reference_precision_seconds": "1e-19",
                "traceability": "test",
                "invented_precision": "0",
            },
        )


def test_temporal_interval_relation_is_conservative() -> None:
    assert compare_temporal_intervals("1.0", "0.2", "1.1", "0.2") is TemporalRelation.INDETERMINATE
    assert compare_temporal_intervals("1.0", "0.1", "2.0", "0.1") is TemporalRelation.BEFORE
