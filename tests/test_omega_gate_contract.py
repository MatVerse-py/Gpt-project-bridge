from __future__ import annotations

import pytest

from app.evidence import sha256_text
from app.omega_gate_contract import (
    GateDecision,
    InvariantCheck,
    OmegaGateContract,
    build_request,
)


def request(*checks: InvariantCheck, requires_authorization: bool = False, authorization_ref: str | None = None):
    return build_request(
        proposal_id="proposal-001",
        source_mnb="mnb:core:001",
        target_state_hash=sha256_text("target"),
        principal="principal:test",
        requested_action="state.transition",
        context_hash=sha256_text("context"),
        invariants=checks,
        requires_authorization=requires_authorization,
        authorization_ref=authorization_ref,
    )


def test_all_resolved_invariants_admit():
    decision = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            InvariantCheck("evidence", True, "receipt://evidence"),
        )
    )
    assert decision.status is GateDecision.ADMIT
    assert decision.reasons == ()
    assert decision.receipt["schema"] == "matverse.evidence-receipt.v1"


def test_failed_invariant_blocks():
    decision = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            InvariantCheck("risk", False, "receipt://risk"),
        )
    )
    assert decision.status is GateDecision.BLOCK
    assert "INVARIANT_FAILED:risk" in decision.reasons


def test_unresolved_invariant_holds():
    decision = OmegaGateContract.evaluate(request(InvariantCheck("external", None, "receipt://external")))
    assert decision.status is GateDecision.HOLD
    assert "INVARIANT_UNRESOLVED:external" in decision.reasons


def test_missing_required_authorization_holds_even_when_checks_pass():
    decision = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            requires_authorization=True,
        )
    )
    assert decision.status is GateDecision.HOLD
    assert "AUTHORIZATION_REQUIRED" in decision.reasons


def test_authorization_reference_allows_admission_when_checks_pass():
    decision = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            requires_authorization=True,
            authorization_ref="auth://human/001",
        )
    )
    assert decision.status is GateDecision.ADMIT


def test_non_boolean_invariant_outcome_is_rejected_fail_closed():
    with pytest.raises(TypeError, match="bool or None"):
        InvariantCheck("identity", "true", "receipt://identity")  # type: ignore[arg-type]
