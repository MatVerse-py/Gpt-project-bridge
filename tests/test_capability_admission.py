from __future__ import annotations

from dataclasses import replace

from app.capability_admission import (
    CapabilityAdmission,
    CapabilityCatalog,
    CapabilityClass,
    CapabilitySpec,
    CapabilityState,
    CapabilityType,
    discovered_host_capabilities,
)


def test_host_capabilities_are_discovered_not_admitted():
    catalog = CapabilityCatalog(discovered_host_capabilities())
    inventory = catalog.inventory()

    assert len(inventory) == 9
    assert {item.capability_id for item in inventory} >= {
        "experiment-design-gate",
        "external-reproduction-kit",
        "mechanism-binding-audit",
        "sovereign-installer-guard",
        "skill-creator",
    }
    assert all(item.state is CapabilityState.DISCOVERED for item in inventory)

    decision = CapabilityAdmission.evaluate(catalog.get("experiment-design-gate"))
    assert decision.admitted is False
    assert decision.state is CapabilityState.HOLD
    assert "MISSING_CONTRACT" in decision.reasons
    assert "MISSING_IMPLEMENTATION" in decision.reasons
    assert "MISSING_TEST" in decision.reasons
    assert "MISSING_EVIDENCE" in decision.reasons
    assert "MISSING_AUTHORITY" in decision.reasons


def test_fully_bound_executable_capability_can_be_admitted():
    spec = CapabilitySpec(
        capability_id="test-gate",
        name="test-gate",
        capability_type=CapabilityType.SKILL,
        capability_class=CapabilityClass.SCIENTIFIC_GATE,
        source_ref="repo://skills/test-gate",
        contract_ref="contract://test-gate/v1",
        implementation_ref="git://commit/abc123",
        test_ref="ci://run/123",
        evidence_ref="receipt://test-gate/123",
        authority_ref="authority://constitution/capability-admission",
        state=CapabilityState.TESTED,
    )

    decision = CapabilityAdmission.evaluate(spec)
    assert decision.admitted is True
    assert decision.state is CapabilityState.ADMITTED
    assert decision.reasons == ()
    assert len(decision.decision_hash) == 64

    promoted = CapabilityAdmission.promote(spec)
    assert promoted.state is CapabilityState.ADMITTED


def test_skill_creator_does_not_auto_admit_generated_skill():
    generated = CapabilitySpec(
        capability_id="generated-skill",
        name="generated-skill",
        capability_type=CapabilityType.SKILL,
        capability_class=CapabilityClass.GENERAL,
        source_ref="skill-creator://proposal/001",
        generated_by="skill-creator",
        contract_ref="contract://generated-skill/v1",
        implementation_ref="git://commit/generated",
        state=CapabilityState.IMPLEMENTED,
    )

    decision = CapabilityAdmission.evaluate(generated)
    assert decision.admitted is False
    assert decision.state is CapabilityState.HOLD
    assert "MISSING_TEST" in decision.reasons
    assert "MISSING_EVIDENCE" in decision.reasons
    assert "MISSING_AUTHORITY" in decision.reasons


def test_capabilities_emit_coverage_observations_with_stable_identity():
    spec = discovered_host_capabilities()[0]
    observation_v1 = spec.to_coverage_observation()
    changed = replace(spec, contract_ref="contract://experiment-design-gate/v1")
    observation_v2 = changed.to_coverage_observation()

    assert observation_v1.partition_id == "capability-registry"
    assert observation_v1.source_uri == observation_v2.source_uri
    assert observation_v1.artifact_type == "capability"
    assert observation_v1.content_hash != observation_v2.content_hash


def test_blocked_and_superseded_capabilities_cannot_be_admitted():
    base = CapabilitySpec(
        capability_id="dangerous-tool",
        name="dangerous-tool",
        capability_type=CapabilityType.TOOL,
        capability_class=CapabilityClass.GENERAL,
        source_ref="repo://tools/dangerous-tool",
        contract_ref="contract://dangerous-tool/v1",
        implementation_ref="git://commit/tool",
        test_ref="ci://run/tool",
        evidence_ref="receipt://tool",
        authority_ref="authority://tool",
    )

    for state in (CapabilityState.BLOCKED, CapabilityState.SUPERSEDED):
        decision = CapabilityAdmission.evaluate(replace(base, state=state))
        assert decision.admitted is False
        assert decision.state is state
