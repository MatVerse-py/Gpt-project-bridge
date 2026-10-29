"""The MVKit bridge never changes constitutional authority."""
from types import SimpleNamespace
import pytest
from app.core import Decision
from app.mvkit_shadow import (SCHEMA, ARCHIVE_SHA256, compare_shadow,
                               compare_archive, load_pinned_scorer)


def scorer(polarity):
    def call(*_args, **_kwargs):
        return SimpleNamespace(polarity=SimpleNamespace(value=polarity),
                               omega=0.97, fingerprint="a" * 64)
    return call


@pytest.mark.parametrize("canonical,shadow", [
    (Decision.HOLD, "PASS"), (Decision.BLOCK, "PASS"),
    (Decision.PASS, "BLOCK"), (Decision.PASS, "ESCALATE"),
])
def test_shadow_can_never_authorize(canonical, shadow):
    result = compare_shadow(canonical_decision=canonical, samples=[.97] * 40,
                            theta_hat=.9, cvar=.01, pole=True,
                            evaluator=scorer(shadow), source_digest=ARCHIVE_SHA256)
    assert result["schema"] == SCHEMA
    assert result["canonical_decision"] == canonical.value
    assert result["shadow_polarity"] == shadow
    assert result["divergence"]
    assert result["review_required"]
    assert result["may_authorize_execution"] is False
    assert result["may_promote_policy"] is False
    assert result["authoritative"] is False
    assert len(result["comparison_hash"]) == 64


def test_unavailable_scorer_is_hold():
    result = compare_shadow(canonical_decision=Decision.PASS, samples=[.9],
                            theta_hat=.9, cvar=.0, pole=True,
                            evaluator=None, source_digest=ARCHIVE_SHA256)
    assert result["status"] == "SHADOW_HOLD"
    assert result["review_required"] is True


def test_bad_scorer_is_hold():
    result = compare_shadow(canonical_decision=Decision.PASS, samples=[.9],
                            theta_hat=.9, cvar=.0, pole=True,
                            evaluator=scorer("UNKNOWN"), source_digest=ARCHIVE_SHA256)
    assert result["status"] == "SHADOW_HOLD"


def test_modified_zip_rejected():
    with pytest.raises(ValueError):
        load_pinned_scorer(b"not-a-trusted-archive")
    result = compare_archive(archive_bytes=b"not-a-trusted-archive", canonical_decision=Decision.PASS,
                             samples=[.99] * 40, theta_hat=.95, cvar=.01, pole=True)
    assert result["status"] == "SHADOW_HOLD"


def test_canonical_decision_typed():
    with pytest.raises(TypeError):
        compare_shadow(canonical_decision="PASS", samples=[.95] * 40,
                       theta_hat=.9, cvar=.01, pole=True,
                       evaluator=scorer("PASS"), source_digest=ARCHIVE_SHA256)


def test_deterministic_comparison_receipt():
    kwargs = dict(canonical_decision=Decision.PASS, samples=[.99] * 40,
                  theta_hat=.95, cvar=.01, pole=True,
                  evaluator=scorer("PASS"), source_digest=ARCHIVE_SHA256)
    a, b = compare_shadow(**kwargs), compare_shadow(**kwargs)
    assert a == b
    assert a["divergence"] is False
    assert a["review_required"] is False
