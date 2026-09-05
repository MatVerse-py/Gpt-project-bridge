import math
import pytest

from app.drift_engine import (
    AscCertificate,
    LensReading,
    TwoAxisAdjudicator,
    Verdict,
    ViabilityReading,
    chernoff_information,
    effective_sample_size,
    required_probes,
)


def lens(lens_id="L1", label="no_drift"):
    values = {
        "drift": (1.0, 1.2, 1.1),
        "no_drift": (0.05, 0.08, 0.06),
        "undecided": (0.2, 0.3, 0.4),
    }
    low_high = {
        "drift": (0.9, 1.3),
        "no_drift": (0.0, 0.1),
        "undecided": (0.2, 0.7),
    }
    lo, hi = low_high[label]
    return LensReading(lens_id, True, values[label], 0.5, lo, hi)


def test_chernoff_identical_distributions_is_zero():
    assert chernoff_information({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}) == pytest.approx(0.0)


def test_chernoff_is_symmetric_and_positive_for_distinct_distributions():
    pa = {"a": 0.9, "b": 0.1}
    pb = {"a": 0.1, "b": 0.9}
    assert chernoff_information(pa, pb) == pytest.approx(chernoff_information(pb, pa))
    assert chernoff_information(pa, pb) > 0


@pytest.mark.parametrize("pa,pb", [({}, {"a": 1}), ({"a": 0.5}, {"a": 0.5}), ({"a": -0.1, "b": 1.1}, {"a": 1})])
def test_chernoff_rejects_invalid_distributions(pa, pb):
    with pytest.raises(ValueError):
        chernoff_information(pa, pb)


def test_chernoff_rejects_grid_below_two():
    with pytest.raises(ValueError):
        chernoff_information({"a": 1.0}, {"a": 1.0}, grid=1)


@pytest.mark.parametrize(
    "reading,expected",
    [(lens(label="drift"), "drift"), (lens(label="no_drift"), "no_drift"), (lens(label="undecided"), "undecided")],
)
def test_lens_classification(reading, expected):
    assert reading.classify() == expected


def test_lens_outside_telemetry_or_without_probes_is_silence():
    assert LensReading("L1", False, (1.0,), 0.5, 1.0, 1.2).classify() == "silence"
    assert LensReading("L1", True, (), 0.5, 0.0, 0.0).classify() == "silence"


def test_adjudicator_historical_case_weights_is_drift():
    verdict, detail = TwoAxisAdjudicator().adjudicate(True, [lens("L1", "drift"), lens("L2", "drift")])
    assert verdict is Verdict.DRIFT
    assert detail["L1"][0] == "drift"


def test_adjudicator_certified_adapter_is_continuity():
    asc = AscCertificate(0.99, 0.01, 0.8, True)
    verdict, _ = TwoAxisAdjudicator().adjudicate(True, [lens("L1"), lens("L2")], asc=asc)
    assert verdict is Verdict.CONTINUITY


def test_adjudicator_tampered_lineage_is_distinct_or_clone():
    verdict, _ = TwoAxisAdjudicator().adjudicate(False, [lens("L1", "drift"), lens("L2", "drift")])
    assert verdict is Verdict.DISTINCT
    verdict, _ = TwoAxisAdjudicator().adjudicate(False, [lens("L1"), lens("L2")])
    assert verdict is Verdict.CLONE


def test_adjudicator_outside_window_is_silence():
    reading = LensReading("L1", False, (1.0,), 0.5, 0.9, 1.1)
    assert TwoAxisAdjudicator().adjudicate(True, [reading])[0] is Verdict.SILENCE


def test_adjudicator_partial_drift_is_dissent():
    verdict, detail = TwoAxisAdjudicator(quorum_k=2).adjudicate(True, [lens("L1", "drift"), lens("L2")])
    assert verdict is Verdict.DISSENSO
    assert "discordam" in detail["reason"]


def test_adjudicator_undecided_is_escalated():
    assert TwoAxisAdjudicator().adjudicate(True, [lens("L1", "undecided")])[0] is Verdict.ESCALATE


def test_precedence_of_infeasible_and_invalid_asc():
    viability = ViabilityReading(1.0, 2.0)
    assert TwoAxisAdjudicator().adjudicate(True, [lens("L1")], viability=viability)[0] is Verdict.INFEASIBLE
    bad_asc = AscCertificate(0.1, 0.9, 0.2, False)
    assert TwoAxisAdjudicator().adjudicate(True, [lens("L1")], asc=bad_asc)[0] is Verdict.ESCALATE


def test_forall_aggregation_requires_all_lenses_to_drift():
    adjudicator = TwoAxisAdjudicator(quorum_k=1, aggregation="forall")
    assert adjudicator.adjudicate(True, [lens("L1", "drift"), lens("L2")])[0] is Verdict.CONTINUITY


def test_validation_of_configuration_and_readings():
    with pytest.raises(ValueError):
        TwoAxisAdjudicator(quorum_k=0)
    with pytest.raises(ValueError):
        TwoAxisAdjudicator(aggregation="mean")
    with pytest.raises(ValueError):
        LensReading("L1", True, (1.0,), 0.5, 1.0, 0.0)


def test_effective_sample_size_and_probe_cost():
    assert effective_sample_size(100, 0.0) == pytest.approx(100.0)
    assert effective_sample_size(100, 0.5) == pytest.approx(66.6666667)
    assert required_probes(0.0, 0.05) is math.inf
    assert required_probes(1.0, 0.05) == pytest.approx(math.log(20.0))


@pytest.mark.parametrize("n,icc", [(-1, 0.1), (10, -0.1), (10, 1.0)])
def test_effective_sample_size_rejects_invalid_inputs(n, icc):
    with pytest.raises(ValueError):
        effective_sample_size(n, icc)
