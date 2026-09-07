from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable

from .evidence import canonical_json, evidence_receipt, sha256_text

SCHEMA = "matverse.llm-publication-bridge.v1"


class BridgeError(RuntimeError):
    pass


class Role(str, Enum):
    RESEARCH = "research"
    DRAFT = "draft"
    CODE = "code"
    METADATA = "metadata"
    REVIEW = "review"
    CURATION = "curation"
    WORKFLOW = "workflow"
    REDTEAM = "redteam"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


class AuthorityKind(str, Enum):
    HUMAN = "HUMAN"
    CONSTITUTIONAL = "CONSTITUTIONAL"


class Stage(str, Enum):
    LOCAL_SOURCE = "LOCAL_SOURCE"
    ANALYZED = "ANALYZED"
    GIT_STAGED = "GIT_STAGED"
    GOVERNANCE_PASS = "GOVERNANCE_PASS"
    HF_STAGED = "HF_STAGED"
    ZENODO_DRAFT = "ZENODO_DRAFT"
    COMMUNITY_REVIEW = "COMMUNITY_REVIEW"
    PUBLISH_AUTHORIZED = "PUBLISH_AUTHORIZED"
    PUBLISHED = "PUBLISHED"


FORBIDDEN_AGENT_OUTPUT_KEYS = frozenset(
    {
        "authorized",
        "authorization",
        "publish",
        "published",
        "merge",
        "canonical",
        "canonize",
        "secret",
        "token",
        "password",
        "credential",
        "api_key",
    }
)

WRITE_ACTIONS = frozenset(
    {
        "git.push",
        "git.merge",
        "hf.push",
        "zenodo.create_draft",
        "zenodo.publish",
        "community.submit",
    }
)


@dataclass(frozen=True)
class Capability:
    capability_id: str
    provider: str
    model: str
    principal: str
    roles: tuple[Role, ...]
    enabled: bool = True
    transport: str = "external_adapter"

    def __post_init__(self) -> None:
        if not self.capability_id or not self.provider or not self.model or not self.principal:
            raise ValueError("capability_id, provider, model and principal are required")
        if not self.roles:
            raise ValueError("at least one role is required")


@dataclass(frozen=True)
class WorkItem:
    work_id: str
    artifact_sha256: str
    title: str
    source_ref: str
    publication_kind: str
    target_venues: tuple[str, ...] = ("git", "hf", "zenodo")

    def __post_init__(self) -> None:
        if len(self.artifact_sha256) != 64:
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 hex digest")
        if any(ch not in "0123456789abcdef" for ch in self.artifact_sha256):
            raise ValueError("artifact_sha256 must be lowercase hexadecimal")


@dataclass(frozen=True)
class AgentProposal:
    proposal_id: str
    work_id: str
    capability_id: str
    capability_principal: str
    role: Role
    payload: dict[str, Any]
    payload_hash: str
    receipt: dict[str, Any]


@dataclass(frozen=True)
class AgentReview:
    review_id: str
    proposal_id: str
    reviewer_capability_id: str
    reviewer_principal: str
    decision: ReviewDecision
    findings: tuple[str, ...]
    receipt: dict[str, Any]


@dataclass(frozen=True)
class Authorization:
    authorization_id: str
    proposal_id: str
    action: str
    principal: str
    authority_kind: AuthorityKind
    scope: str
    receipt: dict[str, Any]


@dataclass(frozen=True)
class PlannedAction:
    action_id: str
    work_id: str
    proposal_id: str
    action: str
    target: str
    authorization_id: str | None
    side_effect: bool
    receipt: dict[str, Any]


@dataclass(frozen=True)
class PublicationState:
    work_id: str
    stage: Stage
    history: tuple[str, ...]


class CapabilityRegistry:
    def __init__(self, capabilities: Iterable[Capability] = ()) -> None:
        self._items: dict[str, Capability] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: Capability) -> None:
        if capability.capability_id in self._items:
            raise BridgeError(f"duplicate capability_id: {capability.capability_id}")
        self._items[capability.capability_id] = capability

    def get(self, capability_id: str) -> Capability:
        try:
            capability = self._items[capability_id]
        except KeyError as exc:
            raise BridgeError(f"unknown capability: {capability_id}") from exc
        if not capability.enabled:
            raise BridgeError(f"capability disabled: {capability_id}")
        return capability

    def select(self, role: Role, preferred_provider: str | None = None) -> Capability:
        candidates = [item for item in self._items.values() if item.enabled and role in item.roles]
        if preferred_provider:
            preferred = [item for item in candidates if item.provider == preferred_provider]
            if preferred:
                candidates = preferred
        if not candidates:
            raise BridgeError(f"no admitted capability for role {role.value}")
        return sorted(candidates, key=lambda item: item.capability_id)[0]

    def public_inventory(self) -> list[dict[str, Any]]:
        return [
            {
                "capability_id": item.capability_id,
                "provider": item.provider,
                "model": item.model,
                "principal": item.principal,
                "roles": [role.value for role in item.roles],
                "enabled": item.enabled,
                "transport": item.transport,
            }
            for item in sorted(self._items.values(), key=lambda item: item.capability_id)
        ]


class LLMPublicationBridge:
    """Governed multi-LLM publication coordinator.

    LLMs are advisory/recruitable capabilities. They may propose, review, draft,
    code, curate and prepare metadata. They never self-authorize publication,
    canonization, merge, or external writes.
    """

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _id(prefix: str, payload: dict[str, Any]) -> str:
        return f"{prefix}-{sha256_text(canonical_json(payload))[:20]}"

    @staticmethod
    def _assert_agent_payload(payload: dict[str, Any]) -> None:
        for key in payload:
            lowered = str(key).strip().lower()
            if lowered in FORBIDDEN_AGENT_OUTPUT_KEYS:
                raise BridgeError(f"agent output contains authority/secret field: {key}")

    def propose(
        self,
        work: WorkItem,
        *,
        capability_id: str,
        role: Role,
        payload: dict[str, Any],
    ) -> AgentProposal:
        capability = self.registry.get(capability_id)
        if role not in capability.roles:
            raise BridgeError(f"capability {capability_id} is not admitted for role {role.value}")
        self._assert_agent_payload(payload)
        core = {
            "schema": SCHEMA,
            "work_id": work.work_id,
            "artifact_sha256": work.artifact_sha256,
            "capability_id": capability.capability_id,
            "capability_principal": capability.principal,
            "role": role.value,
            "payload": payload,
        }
        proposal_id = self._id("proposal", core)
        receipt = evidence_receipt("llm_publication.proposal", {"work": asdict(work)}, {**core, "proposal_id": proposal_id})
        return AgentProposal(
            proposal_id=proposal_id,
            work_id=work.work_id,
            capability_id=capability.capability_id,
            capability_principal=capability.principal,
            role=role,
            payload=dict(payload),
            payload_hash=sha256_text(canonical_json(payload)),
            receipt=receipt,
        )

    def review(
        self,
        proposal: AgentProposal,
        *,
        reviewer_capability_id: str,
        decision: ReviewDecision,
        findings: Iterable[str] = (),
    ) -> AgentReview:
        reviewer = self.registry.get(reviewer_capability_id)
        if Role.REVIEW not in reviewer.roles and Role.REDTEAM not in reviewer.roles and Role.CURATION not in reviewer.roles:
            raise BridgeError("reviewer capability lacks review/redteam/curation role")
        if reviewer.capability_id == proposal.capability_id or reviewer.principal == proposal.capability_principal:
            raise BridgeError("generator and reviewer must be independent principals")
        findings_tuple = tuple(str(item).strip() for item in findings if str(item).strip())
        core = {
            "schema": SCHEMA,
            "proposal_id": proposal.proposal_id,
            "reviewer_capability_id": reviewer.capability_id,
            "reviewer_principal": reviewer.principal,
            "decision": decision.value,
            "findings": findings_tuple,
        }
        review_id = self._id("review", core)
        receipt = evidence_receipt("llm_publication.review", {"proposal_id": proposal.proposal_id}, {**core, "review_id": review_id})
        return AgentReview(
            review_id=review_id,
            proposal_id=proposal.proposal_id,
            reviewer_capability_id=reviewer.capability_id,
            reviewer_principal=reviewer.principal,
            decision=decision,
            findings=findings_tuple,
            receipt=receipt,
        )

    def authorize(
        self,
        proposal: AgentProposal,
        review: AgentReview,
        *,
        action: str,
        principal: str,
        authority_kind: AuthorityKind,
        scope: str,
    ) -> Authorization:
        if review.proposal_id != proposal.proposal_id:
            raise BridgeError("review does not belong to proposal")
        if review.decision is not ReviewDecision.APPROVE:
            raise BridgeError("only an APPROVE advisory review can proceed to external authorization")
        if not principal or principal in {proposal.capability_principal, review.reviewer_principal}:
            raise BridgeError("authorizer must be independent from LLM generator/reviewer principals")
        if action not in WRITE_ACTIONS:
            raise BridgeError(f"unknown governed write action: {action}")
        if not scope:
            raise BridgeError("authorization scope is required")
        core = {
            "schema": SCHEMA,
            "proposal_id": proposal.proposal_id,
            "review_id": review.review_id,
            "action": action,
            "principal": principal,
            "authority_kind": authority_kind.value,
            "scope": scope,
        }
        authorization_id = self._id("auth", core)
        receipt = evidence_receipt("llm_publication.authorization", {"proposal_id": proposal.proposal_id}, {**core, "authorization_id": authorization_id})
        return Authorization(
            authorization_id=authorization_id,
            proposal_id=proposal.proposal_id,
            action=action,
            principal=principal,
            authority_kind=authority_kind,
            scope=scope,
            receipt=receipt,
        )

    def plan_action(
        self,
        work: WorkItem,
        proposal: AgentProposal,
        *,
        action: str,
        target: str,
        authorization: Authorization | None = None,
    ) -> PlannedAction:
        side_effect = action in WRITE_ACTIONS
        if side_effect:
            if authorization is None:
                raise BridgeError(f"external write requires authorization: {action}")
            if authorization.proposal_id != proposal.proposal_id or authorization.action != action:
                raise BridgeError("authorization does not cover this proposal/action")
        core = {
            "schema": SCHEMA,
            "work_id": work.work_id,
            "proposal_id": proposal.proposal_id,
            "action": action,
            "target": target,
            "side_effect": side_effect,
            "authorization_id": authorization.authorization_id if authorization else None,
        }
        action_id = self._id("action", core)
        receipt = evidence_receipt("llm_publication.action_plan", {"proposal_id": proposal.proposal_id}, {**core, "action_id": action_id})
        return PlannedAction(
            action_id=action_id,
            work_id=work.work_id,
            proposal_id=proposal.proposal_id,
            action=action,
            target=target,
            authorization_id=authorization.authorization_id if authorization else None,
            side_effect=side_effect,
            receipt=receipt,
        )

    @staticmethod
    def initial_state(work: WorkItem) -> PublicationState:
        return PublicationState(work_id=work.work_id, stage=Stage.LOCAL_SOURCE, history=(Stage.LOCAL_SOURCE.value,))

    @staticmethod
    def transition(state: PublicationState, target: Stage, *, authorization: Authorization | None = None) -> PublicationState:
        allowed: dict[Stage, tuple[Stage, ...]] = {
            Stage.LOCAL_SOURCE: (Stage.ANALYZED,),
            Stage.ANALYZED: (Stage.GIT_STAGED,),
            Stage.GIT_STAGED: (Stage.GOVERNANCE_PASS,),
            Stage.GOVERNANCE_PASS: (Stage.HF_STAGED, Stage.ZENODO_DRAFT),
            Stage.HF_STAGED: (Stage.ZENODO_DRAFT,),
            Stage.ZENODO_DRAFT: (Stage.COMMUNITY_REVIEW, Stage.PUBLISH_AUTHORIZED),
            Stage.COMMUNITY_REVIEW: (Stage.PUBLISH_AUTHORIZED,),
            Stage.PUBLISH_AUTHORIZED: (Stage.PUBLISHED,),
            Stage.PUBLISHED: (),
        }
        if target not in allowed[state.stage]:
            raise BridgeError(f"invalid publication transition: {state.stage.value} -> {target.value}")
        if target in {Stage.PUBLISH_AUTHORIZED, Stage.PUBLISHED} and authorization is None:
            raise BridgeError(f"{target.value} requires external authorization")
        return PublicationState(work_id=state.work_id, stage=target, history=(*state.history, target.value))


def canonical_matverse_capabilities() -> tuple[Capability, ...]:
    """Suggested role map, not a hard dependency on any vendor/model version."""
    return (
        Capability(
            capability_id="gpt",
            provider="openai",
            model="configured-openai-model",
            principal="llm:openai:gpt",
            roles=(Role.RESEARCH, Role.DRAFT, Role.METADATA, Role.REVIEW),
        ),
        Capability(
            capability_id="claude-code",
            provider="anthropic",
            model="configured-claude-code-model",
            principal="llm:anthropic:claude-code",
            roles=(Role.CODE, Role.DRAFT, Role.REVIEW),
        ),
        Capability(
            capability_id="manus",
            provider="manus",
            model="configured-manus-agent",
            principal="agent:manus",
            roles=(Role.WORKFLOW, Role.RESEARCH, Role.CURATION),
        ),
        Capability(
            capability_id="minimax",
            provider="minimax",
            model="configured-minimax-model",
            principal="llm:minimax",
            roles=(Role.REVIEW, Role.REDTEAM, Role.CURATION, Role.METADATA),
        ),
        Capability(
            capability_id="local",
            provider="local",
            model="configured-local-model",
            principal="llm:local",
            roles=(Role.RESEARCH, Role.DRAFT, Role.REDTEAM, Role.METADATA),
            transport="local_runtime",
        ),
    )
