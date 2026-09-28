from __future__ import annotations

import pytest

from app.evidence import sha256_text
from app.omega_gate_contract import (
    AuthorizationRecord,
    GateDecision,
    InvariantCheck,
    OmegaGateContract,
    build_request,
)


def request(
    *checks: InvariantCheck,
    requires_authorization: bool = False,
    authorization_ref: str | None = None,
    target_state_hash: str | None = None,
):
    return build_request(
        proposal_id="proposal-001",
        source_mnb="mnb:core:001",
        target_state_hash=target_state_hash or sha256_text("target"),
        principal="principal:test",
        requested_action="state.transition",
        context_hash=sha256_text("context"),
        invariants=checks,
        requires_authorization=requires_authorization,
        authorization_ref=authorization_ref,
    )


def valid_authorization(ref: str = "auth://human/001") -> AuthorizationRecord:
    return AuthorizationRecord(
        authorization_ref=ref,
        principal="principal:test",
        requested_action="state.transition",
        target_state_hash=sha256_text("target"),
        evidence_ref="receipt://authorization/001",
        active=True,
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
    decision = OmegaGateContract.evaluate(
        request(InvariantCheck("external", None, "receipt://external"))
    )
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


def test_bare_authorization_reference_is_not_authority():
    decision = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            requires_authorization=True,
            authorization_ref="auth://human/001",
        )
    )
    assert decision.status is GateDecision.HOLD
    assert "AUTHORIZATION_UNRESOLVED" in decision.reasons


def test_resolved_and_scoped_authorization_allows_admission():
    authorization = valid_authorization()
    decision = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            requires_authorization=True,
            authorization_ref=authorization.authorization_ref,
        ),
        authorization_resolver=lambda ref: authorization if ref == authorization.authorization_ref else None,
    )
    assert decision.status is GateDecision.ADMIT
    assert decision.reasons == ()
    assert decision.resolved_authorization is not None
    assert decision.resolved_authorization["evidence_ref"] == "receipt://authorization/001"


def test_authorization_scope_mismatch_blocks():
    mismatched = AuthorizationRecord(
        authorization_ref="auth://human/001",
        principal="different-principal",
        requested_action="state.transition",
        target_state_hash=sha256_text("target"),
        evidence_ref="receipt://authorization/mismatch",
    )
    decision = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            requires_authorization=True,
            authorization_ref="auth://human/001",
        ),
        authorization_resolver=lambda _: mismatched,
    )
    assert decision.status is GateDecision.BLOCK
    assert any(reason.startswith("AUTHORIZATION_SCOPE_MISMATCH") for reason in decision.reasons)


def test_inactive_authorization_blocks():
    inactive = AuthorizationRecord(
        authorization_ref="auth://human/001",
        principal="principal:test",
        requested_action="state.transition",
        target_state_hash=sha256_text("target"),
        evidence_ref="receipt://authorization/inactive",
        active=False,
    )
    decision = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            requires_authorization=True,
            authorization_ref="auth://human/001",
        ),
        authorization_resolver=lambda _: inactive,
    )
    assert decision.status is GateDecision.BLOCK
    assert "AUTHORIZATION_INACTIVE" in decision.reasons


def test_digest_normalization_produces_same_decision_identity():
    canonical_target = sha256_text("target")
    padded_upper = f"  {canonical_target.upper()}  "

    canonical = OmegaGateContract.evaluate(
        request(InvariantCheck("identity", True, "receipt://identity"))
    )
    normalized = OmegaGateContract.evaluate(
        request(
            InvariantCheck("identity", True, "receipt://identity"),
            target_state_hash=padded_upper,
        )
    )
    assert normalized.decision_id == canonical.decision_id


def test_non_boolean_invariant_outcome_is_rejected_fail_closed():
    with pytest.raises(TypeError, match="bool or None"):
        InvariantCheck("identity", "true", "receipt://identity")  # type: ignore[arg-type]
