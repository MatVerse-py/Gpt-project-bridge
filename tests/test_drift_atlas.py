from matverse_atlas.claims import ClaimStrength, Priority, build_seed_atlas
from matverse_atlas.drift_audit import (
    HistoricalCase,
    LensEvidence,
    Verdict,
    adjudicate,
    run_historical_audit,
)


def test_claim_16_is_registered_fail_closed():
    atlas = build_seed_atlas()
    claim = atlas.get("MV-016")
    assert claim.priority() is Priority.UNKNOWN
    assert claim.max_strength() is ClaimStrength.DESCRIBED
    assert claim.name.startswith("DRIFT")


def test_historical_cases_do_not_inflate_to_drift_without_telemetry():
    results = run_historical_audit()
    assert [r.verdict for r in results] == [Verdict.SILENCE, Verdict.SILENCE]


def test_drift_requires_legitimate_measurement_and_lineage():
    case = HistoricalCase(
        case_id="MEASURED",
        description="measured",
        lineage_continuous=True,
        lenses=(
            LensEvidence(
                "L1",
                from_telemetry_window=True,
                per_probe_chernoff=(0.2, 0.3),
                onset=0.1,
                ci_low=0.15,
                ci_high=0.35,
            ),
        ),
    )
    assert adjudicate(case).verdict is Verdict.DRIFT


def test_disagreement_is_dissenso_not_average():
    case = HistoricalCase(
        case_id="DISSENSO",
        description="measured",
        lineage_continuous=True,
        lenses=(
            LensEvidence("L1", True, (0.2,), 0.1, 0.15, 0.25),
            LensEvidence("L2", True, (0.01,), 0.1, 0.0, 0.05),
        ),
    )
    assert adjudicate(case).verdict is Verdict.DISSENSO
