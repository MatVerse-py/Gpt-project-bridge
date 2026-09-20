from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Iterable

from .coverage_registry import SweepObservation

SCHEMA = "matverse.capability-admission.v1"


class CapabilityType(str, Enum):
    SKILL = "SKILL"
    NUCLEUS = "NUCLEUS"
    MODEL = "MODEL"
    RUNTIME = "RUNTIME"
    TOOL = "TOOL"
    HUMAN_AUTHORITY = "HUMAN_AUTHORITY"


class CapabilityClass(str, Enum):
    SCIENTIFIC_GATE = "SCIENTIFIC_GATE"
    EXTERNAL_REPRODUCTION = "EXTERNAL_REPRODUCTION"
    MECHANISM_BINDING = "MECHANISM_BINDING"
    SOVEREIGN_SECURITY = "SOVEREIGN_SECURITY"
    ARTIFACT = "ARTIFACT"
    META = "META"
    GENERAL = "GENERAL"


class CapabilityState(str, Enum):
    DISCOVERED = "DISCOVERED"
    SPECIFIED = "SPECIFIED"
    IMPLEMENTED = "IMPLEMENTED"
    TESTED = "TESTED"
    ADMITTED = "ADMITTED"
    HOLD = "HOLD"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    name: str
    capability_type: CapabilityType
    capability_class: CapabilityClass
    source_ref: str
    triggers: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    contract_ref: str | None = None
    implementation_ref: str | None = None
    test_ref: str | None = None
    evidence_ref: str | None = None
    authority_ref: str | None = None
    generated_by: str | None = None
    state: CapabilityState = CapabilityState.DISCOVERED

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.name.strip() or not self.source_ref.strip():
            raise ValueError("capability_id, name and source_ref are required")

    def canonical_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["capability_type"] = self.capability_type.value
        payload["capability_class"] = self.capability_class.value
        payload["state"] = self.state.value
        return payload

    def to_coverage_observation(self, *, partition_id: str = "capability-registry") -> SweepObservation:
        content = json.dumps(self.canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return SweepObservation.from_content(
            partition_id=partition_id,
            source_uri=f"capability://{self.capability_id}",
            artifact_type="capability",
            content=content,
            metadata={
                "name": self.name,
                "capability_type": self.capability_type.value,
                "capability_class": self.capability_class.value,
                "state": self.state.value,
                "source_ref": self.source_ref,
            },
        )


@dataclass(frozen=True)
class CapabilityAdmissionDecision:
    capability_id: str
    state: CapabilityState
    admitted: bool
    reasons: tuple[str, ...]
    decision_hash: str


class CapabilityAdmission:
    """Fail-closed admission of capabilities into the organism.

    Discovery or interface availability is never enough. Executable capabilities
    require a contract, implementation, test evidence, independent evidence and
    an authority reference before they can become ADMITTED.
    """

    @staticmethod
    def evaluate(spec: CapabilitySpec) -> CapabilityAdmissionDecision:
        if spec.state in {CapabilityState.BLOCKED, CapabilityState.SUPERSEDED}:
            reasons = (f"STATE_{spec.state.value}",)
            state = spec.state
            admitted = False
        else:
            missing: list[str] = []
            if not spec.contract_ref:
                missing.append("MISSING_CONTRACT")
            if not spec.authority_ref:
                missing.append("MISSING_AUTHORITY")
            if not spec.evidence_ref:
                missing.append("MISSING_EVIDENCE")

            if spec.capability_type is not CapabilityType.HUMAN_AUTHORITY:
                if not spec.implementation_ref:
                    missing.append("MISSING_IMPLEMENTATION")
                if not spec.test_ref:
                    missing.append("MISSING_TEST")

            if missing:
                reasons = tuple(missing)
                state = CapabilityState.HOLD
                admitted = False
            else:
                reasons = ()
                state = CapabilityState.ADMITTED
                admitted = True

        decision_core = {
            "schema": SCHEMA,
            "capability_id": spec.capability_id,
            "source_ref": spec.source_ref,
            "requested_state": spec.state.value,
            "result_state": state.value,
            "reasons": reasons,
        }
        decision_hash = hashlib.sha256(
            json.dumps(decision_core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return CapabilityAdmissionDecision(
            capability_id=spec.capability_id,
            state=state,
            admitted=admitted,
            reasons=reasons,
            decision_hash=decision_hash,
        )

    @staticmethod
    def promote(spec: CapabilitySpec) -> CapabilitySpec:
        decision = CapabilityAdmission.evaluate(spec)
        if not decision.admitted:
            return replace(spec, state=CapabilityState.HOLD)
        return replace(spec, state=CapabilityState.ADMITTED)


class CapabilityCatalog:
    def __init__(self, capabilities: Iterable[CapabilitySpec] = ()) -> None:
        self._items: dict[str, CapabilitySpec] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: CapabilitySpec) -> None:
        if capability.capability_id in self._items:
            raise ValueError(f"duplicate capability_id: {capability.capability_id}")
        self._items[capability.capability_id] = capability

    def get(self, capability_id: str) -> CapabilitySpec:
        try:
            return self._items[capability_id]
        except KeyError as exc:
            raise KeyError(f"unknown capability: {capability_id}") from exc

    def inventory(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def coverage_observations(self) -> tuple[SweepObservation, ...]:
        return tuple(item.to_coverage_observation() for item in self.inventory())


def discovered_host_capabilities() -> tuple[CapabilitySpec, ...]:
    """Capabilities observed in the host UI; discovery is not implementation proof."""

    return (
        CapabilitySpec(
            capability_id="experiment-design-gate",
            name="experiment-design-gate",
            capability_type=CapabilityType.SKILL,
            capability_class=CapabilityClass.SCIENTIFIC_GATE,
            source_ref="host-ui:experiment-design-gate",
            triggers=("ATE", "four-arm", "placebo", "ablation", "H1-H7", "causal revocation", "homeostasis", "causal inheritance"),
            permissions=("analyze:experiment-design", "decision:advisory-block"),
        ),
        CapabilitySpec(
            capability_id="external-reproduction-kit",
            name="external-reproduction-kit",
            capability_type=CapabilityType.SKILL,
            capability_class=CapabilityClass.EXTERNAL_REPRODUCTION,
            source_ref="host-ui:external-reproduction-kit",
            triggers=("EXTERNAL_PASS", "ATTESTED", "VERIFIED", "clean-room", "blind-probes", "OIDC"),
            permissions=("prepare:clean-room-kit", "validate:external-boundary"),
        ),
        CapabilitySpec(
            capability_id="mechanism-binding-audit",
            name="mechanism-binding-audit",
            capability_type=CapabilityType.SKILL,
            capability_class=CapabilityClass.MECHANISM_BINDING,
            source_ref="host-ui:mechanism-binding-audit",
            triggers=("policy mutation", "fingerprint", "guard", "PROTECTED", "DESTRUCTIVE", "fail-closed", "BindingRegistry"),
            permissions=("audit:mechanism-binding", "detect:transitive-mutation"),
        ),
        CapabilitySpec(
            capability_id="sovereign-installer-guard",
            name="sovereign-installer-guard",
            capability_type=CapabilityType.SKILL,
            capability_class=CapabilityClass.SOVEREIGN_SECURITY,
            source_ref="host-ui:sovereign-installer-guard",
            triggers=("curl | bash", "wget | sh", "pipe-to-shell"),
            permissions=("inspect:installer", "hash:installer", "validate:provenance", "decision:advisory-block"),
        ),
        CapabilitySpec(
            capability_id="word-documents",
            name="Word Documents",
            capability_type=CapabilityType.TOOL,
            capability_class=CapabilityClass.ARTIFACT,
            source_ref="host-ui:word-documents",
            permissions=("artifact:docx",),
        ),
        CapabilitySpec(
            capability_id="pdfs",
            name="PDFs",
            capability_type=CapabilityType.TOOL,
            capability_class=CapabilityClass.ARTIFACT,
            source_ref="host-ui:pdfs",
            permissions=("artifact:pdf",),
        ),
        CapabilitySpec(
            capability_id="presentations",
            name="Presentations",
            capability_type=CapabilityType.TOOL,
            capability_class=CapabilityClass.ARTIFACT,
            source_ref="host-ui:presentations",
            permissions=("artifact:pptx",),
        ),
        CapabilitySpec(
            capability_id="spreadsheets",
            name="Spreadsheets",
            capability_type=CapabilityType.TOOL,
            capability_class=CapabilityClass.ARTIFACT,
            source_ref="host-ui:spreadsheets",
            permissions=("artifact:xlsx", "artifact:csv"),
        ),
        CapabilitySpec(
            capability_id="skill-creator",
            name="Skill Creator",
            capability_type=CapabilityType.SKILL,
            capability_class=CapabilityClass.META,
            source_ref="host-ui:skill-creator",
            permissions=("propose:skill",),
        ),
    )
