from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable

SCHEMA = "matverse.omega-canpublish.v1"
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 2_592_000


class CanPublishError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class PublishDecision(str, Enum):
    HOLD = "HOLD"
    BLOCK = "BLOCK"
    ADMIT = "ADMIT"


class MarxivStage(str, Enum):
    PREPARED = "Prepared"
    APPROVED = "Approved"
    SUBMITTED = "Submitted"


@dataclass(frozen=True)
class PublishProposal:
    schema: str
    proposal_id: str
    intended_action: str
    payload_hash: str
    proposer_key_id: str
    merge_root: str | None
    hold_ttl_seconds: int
    gate_decision: PublishDecision
    marxiv_stage: MarxivStage
    created_at_ns: int
    expires_at_ns: int
    adjudicator_key_id: str | None = None
    policy_id: str | None = None
    evidence_ref: str | None = None
    signature: str | None = None
    executor_key_id: str | None = None
    provider_receipt: str | None = None


def _require_sha256(value: str, *, field: str) -> str:
    normalized = str(value).strip().lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return normalized


def _require_text(value: str, *, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


class OmegaCanPublishGate:
    """Stateful CanPublish projection of the canonical Ω-Gate.

    Q-Gate is an alias/projection, never an independent constitutional organ.

    Body-A proposes -> HOLD / MARXIV.Prepared.
    Body-D adjudicates with an independent principal -> ADMIT/BLOCK.
    ADMIT maps to MARXIV.Approved and performs no external effect.
    Body-X records the externally executed effect -> MARXIV.Submitted.

    HOLD expiry is fail-closed and transitions to BLOCK.
    """

    organ = "Ω-GATE::CanPublish"

    def __init__(self, clock_ns: Callable[[], int] = time.time_ns) -> None:
        self._clock_ns = clock_ns
        self._proposals: dict[str, PublishProposal] = {}

    def propose(
        self,
        *,
        proposal_id: str,
        intended_action: str,
        payload_hash: str,
        proposer_key_id: str,
        merge_root: str | None = None,
        hold_ttl_seconds: int = 600,
    ) -> PublishProposal:
        proposal_id = _require_text(proposal_id, field="proposal_id")
        intended_action = _require_text(intended_action, field="intended_action")
        proposer_key_id = _require_text(proposer_key_id, field="proposer_key_id")
        payload_hash = _require_sha256(payload_hash, field="payload_hash")
        if merge_root is not None:
            merge_root = _require_sha256(merge_root, field="merge_root")
        if hold_ttl_seconds < MIN_TTL_SECONDS or hold_ttl_seconds > MAX_TTL_SECONDS:
            raise CanPublishError(
                400,
                f"hold_ttl_seconds must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}",
            )
        if proposal_id in self._proposals:
            raise CanPublishError(409, "proposal_id already exists")

        now = self._clock_ns()
        proposal = PublishProposal(
            schema=SCHEMA,
            proposal_id=proposal_id,
            intended_action=intended_action,
            payload_hash=payload_hash,
            proposer_key_id=proposer_key_id,
            merge_root=merge_root,
            hold_ttl_seconds=hold_ttl_seconds,
            gate_decision=PublishDecision.HOLD,
            marxiv_stage=MarxivStage.PREPARED,
            created_at_ns=now,
            expires_at_ns=now + hold_ttl_seconds * 1_000_000_000,
        )
        self._proposals[proposal_id] = proposal
        return proposal

    def get(self, proposal_id: str) -> PublishProposal:
        proposal = self._require(proposal_id)
        return self._apply_ttl(proposal)

    def list(self) -> tuple[PublishProposal, ...]:
        return tuple(
            self.get(proposal_id)
            for proposal_id in sorted(
                self._proposals,
                key=lambda item: self._proposals[item].created_at_ns,
                reverse=True,
            )
        )

    def adjudicate(
        self,
        proposal_id: str,
        *,
        decision: PublishDecision,
        adjudicator_key_id: str,
        policy_id: str,
        evidence_ref: str,
        signature: str,
    ) -> PublishProposal:
        if decision not in {PublishDecision.ADMIT, PublishDecision.BLOCK}:
            raise CanPublishError(400, "adjudication decision must be ADMIT or BLOCK")

        proposal = self.get(proposal_id)
        if proposal.gate_decision is not PublishDecision.HOLD:
            raise CanPublishError(409, f"proposal already in {proposal.gate_decision.value}")

        adjudicator_key_id = _require_text(adjudicator_key_id, field="adjudicator_key_id")
        if adjudicator_key_id == proposal.proposer_key_id:
            raise CanPublishError(
                409,
                "adjudicator_key_id equals proposer_key_id; principal separation violated",
            )

        policy_id = _require_text(policy_id, field="policy_id")
        evidence_ref = _require_text(evidence_ref, field="evidence_ref")
        signature = _require_text(signature, field="signature")

        updated = replace(
            proposal,
            gate_decision=decision,
            marxiv_stage=(
                MarxivStage.APPROVED
                if decision is PublishDecision.ADMIT
                else MarxivStage.PREPARED
            ),
            adjudicator_key_id=adjudicator_key_id,
            policy_id=policy_id,
            evidence_ref=evidence_ref,
            signature=signature,
        )
        self._proposals[proposal_id] = updated
        return updated

    def execute(
        self,
        proposal_id: str,
        *,
        executor_key_id: str,
        provider_receipt: str,
    ) -> PublishProposal:
        proposal = self.get(proposal_id)
        if proposal.gate_decision is not PublishDecision.ADMIT:
            raise CanPublishError(412, "proposal is not adjudicated ADMIT")
        executor_key_id = _require_text(executor_key_id, field="executor_key_id")
        provider_receipt = _require_text(provider_receipt, field="provider_receipt")

        if executor_key_id in {
            proposal.proposer_key_id,
            proposal.adjudicator_key_id,
        }:
            raise CanPublishError(409, "Body-X executor must be distinct from proposer and adjudicator")

        updated = replace(
            proposal,
            marxiv_stage=MarxivStage.SUBMITTED,
            executor_key_id=executor_key_id,
            provider_receipt=provider_receipt,
        )
        self._proposals[proposal_id] = updated
        return updated

    def expire_now(self, proposal_id: str) -> PublishProposal:
        proposal = self._require(proposal_id)
        if proposal.gate_decision is PublishDecision.HOLD:
            expired = replace(proposal, expires_at_ns=self._clock_ns() - 1)
            self._proposals[proposal_id] = expired
        return self.get(proposal_id)

    def _apply_ttl(self, proposal: PublishProposal) -> PublishProposal:
        if (
            proposal.gate_decision is PublishDecision.HOLD
            and self._clock_ns() >= proposal.expires_at_ns
        ):
            blocked = replace(proposal, gate_decision=PublishDecision.BLOCK)
            self._proposals[proposal.proposal_id] = blocked
            return blocked
        return proposal

    def _require(self, proposal_id: str) -> PublishProposal:
        try:
            return self._proposals[proposal_id]
        except KeyError as exc:
            raise CanPublishError(404, f"unknown proposal: {proposal_id}") from exc


def proposal_digest(proposal: PublishProposal) -> str:
    """Stable audit digest for a proposal snapshot."""
    payload = "|".join(
        [
            proposal.schema,
            proposal.proposal_id,
            proposal.intended_action,
            proposal.payload_hash,
            proposal.proposer_key_id,
            proposal.gate_decision.value,
            proposal.marxiv_stage.value,
            proposal.policy_id or "",
            proposal.evidence_ref or "",
            proposal.executor_key_id or "",
            proposal.provider_receipt or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
