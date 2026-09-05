from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.secret_plane import (
    CapabilityLease,
    EnvironmentSecretVault,
    InMemoryLeaseStateStore,
    InMemorySecretVault,
    KeyAuthority,
    SecretAccessDenied,
    SecretDescriptor,
    SecretExposureDetector,
    SecretExposureError,
    SecretNotAvailable,
    SecretPlane,
    SecretPlaneError,
    SecretPolicy,
    SecretState,
    SQLiteLeaseStateStore,
    StorageClass,
)


class Clock:
    def __init__(self, now: int = 1_000) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)


@pytest.fixture
def descriptor() -> SecretDescriptor:
    return SecretDescriptor(
        secret_id="openai-prod-current",
        kind="provider_api_key",
        owner="matverse",
        purpose="governed OpenAI Responses execution",
        provider="openai",
        storage_class=StorageClass.TEST_MEMORY,
        version=1,
        created_at=900,
        expires_at=10_000,
        rotation_due_at=5_000,
    )


@pytest.fixture
def policy() -> SecretPolicy:
    return SecretPolicy(
        allowed_actors=("gpt-5.6-sol", "gpt-6-astra"),
        allowed_capabilities=("openai.responses",),
        allowed_scopes=("executor-substitution-v1",),
        max_ttl_seconds=120,
        max_uses=2,
    )


def make_plane(descriptor: SecretDescriptor, policy: SecretPolicy, *, clock: Clock | None = None, lease_state=None):
    vault = InMemorySecretVault({"slot-v1": "TOP-SECRET-VALUE", "slot-v2": "ROTATED-TOP-SECRET"})
    plane = SecretPlane(
        vault=vault,
        lease_signing_key=b"L" * 32,
        lease_state=lease_state or InMemoryLeaseStateStore(),
        clock=clock or Clock(),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )
    plane.register_secret(descriptor, policy=policy, locator="slot-v1")
    return plane, vault


def issue(plane: SecretPlane, **overrides):
    params = {
        "secret_id": "openai-prod-current",
        "actor": "gpt-6-astra",
        "capability": "openai.responses",
        "scope": "executor-substitution-v1",
        "ttl_seconds": 60,
        "max_uses": 1,
    }
    params.update(overrides)
    return plane.issue_lease(**params)


def test_descriptor_contains_metadata_only(descriptor, policy):
    plane, _ = make_plane(descriptor, policy)
    payload = plane.descriptors()[0]
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["secret_id"] == "openai-prod-current"
    assert "TOP-SECRET" not in serialized
    assert "value" not in payload
    assert "encrypted_value" not in payload
    assert "secret_hash" not in payload
    assert "locator" not in payload


def test_lease_use_is_bounded_and_audit_never_contains_secret(descriptor, policy):
    plane, _ = make_plane(descriptor, policy)
    lease = issue(plane)
    result = plane.execute_with_secret(lease, lambda material: {"length": len(material), "ok": True})
    assert result == {"length": len(b"TOP-SECRET-VALUE"), "ok": True}
    with pytest.raises(SecretAccessDenied):
        plane.execute_with_secret(lease, lambda material: None)
    audit = json.dumps(plane.audit_events(), sort_keys=True)
    assert "TOP-SECRET-VALUE" not in audit
    assert "slot-v1" not in audit


def test_disclosure_gate_is_actor_capability_and_scope_scoped(descriptor, policy):
    plane, _ = make_plane(descriptor, policy)
    for kwargs in (
        {"actor": "unknown-model"},
        {"capability": "github.write"},
        {"scope": "unbounded"},
    ):
        with pytest.raises(SecretAccessDenied):
            issue(plane, **kwargs)


def test_lease_tampering_fails_closed(descriptor, policy):
    plane, _ = make_plane(descriptor, policy)
    lease = issue(plane)
    tampered = replace(lease, actor="gpt-5.6-sol")
    with pytest.raises(SecretAccessDenied, match="signature"):
        plane.verify_lease(tampered)


def test_rotation_invalidates_old_lease_and_new_version_uses_new_binding(descriptor, policy):
    plane, _ = make_plane(descriptor, policy)
    old = issue(plane)
    plane.rebind_secret("openai-prod-current", new_locator="slot-v2", new_version=2)
    with pytest.raises(SecretAccessDenied):
        plane.execute_with_secret(old, lambda material: None)
    new = issue(plane)
    assert new.secret_version == 2
    observed = plane.execute_with_secret(new, lambda material: bytes(material).decode("utf-8") == "ROTATED-TOP-SECRET")
    assert observed is True


def test_revoke_removes_local_use_authority(descriptor, policy):
    plane, _ = make_plane(descriptor, policy)
    lease = issue(plane)
    updated = plane.revoke_secret("openai-prod-current")
    assert updated.state is SecretState.REVOKED
    with pytest.raises(SecretAccessDenied):
        plane.verify_lease(lease)
    with pytest.raises(SecretAccessDenied):
        issue(plane)


def test_destroy_zeroizes_test_vault_slot_and_marks_destroyed(descriptor, policy):
    plane, vault = make_plane(descriptor, policy)
    updated = plane.destroy_secret("openai-prod-current")
    assert updated.state is SecretState.DESTROYED
    with pytest.raises(SecretNotAvailable):
        vault.read("slot-v1")


def test_consumer_cannot_return_secret_material(descriptor, policy):
    plane, _ = make_plane(descriptor, policy)
    lease = issue(plane)
    with pytest.raises(SecretExposureError):
        plane.execute_with_secret(lease, lambda material: bytes(material))
    audit = json.dumps(plane.audit_events(), sort_keys=True)
    assert "TOP-SECRET-VALUE" not in audit


def test_expired_descriptor_and_expired_lease_fail_closed(descriptor, policy):
    clock = Clock(1_000)
    plane, _ = make_plane(descriptor, policy, clock=clock)
    lease = issue(plane, ttl_seconds=10)
    clock.now = 1_010
    with pytest.raises(SecretAccessDenied, match="expired"):
        plane.verify_lease(lease)
    clock.now = 10_000
    with pytest.raises(SecretAccessDenied, match="expired"):
        issue(plane)


def test_hkdf_is_domain_separated_and_csprng_requires_256_bits():
    root = b"R" * 32
    a = KeyAuthority.derive(root, context="openai", salt=b"S" * 16)
    b = KeyAuthority.derive(root, context="github", salt=b"S" * 16)
    assert len(a) == 32
    assert a != b
    assert len(KeyAuthority.generate()) == 32
    with pytest.raises(ValueError):
        KeyAuthority.generate(16)


def test_exposure_detector_reports_location_not_secret_text():
    text = "header Authorization: Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 and sk-abcdefghijklmnopqrstuvwxyz123456"
    findings = SecretExposureDetector.scan(text)
    assert {item.rule for item in findings} >= {"bearer_token", "openai_like_key"}
    serialized = json.dumps([item.__dict__ for item in findings], sort_keys=True)
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in serialized
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in serialized


def test_environment_vault_is_read_through_but_not_a_destroyer(monkeypatch):
    monkeypatch.setenv("MATVERSE_TEST_SECRET", "env-secret-value")
    vault = EnvironmentSecretVault()
    material = vault.read("MATVERSE_TEST_SECRET")
    assert bytes(material) == b"env-secret-value"
    with pytest.raises(SecretPlaneError):
        vault.destroy("MATVERSE_TEST_SECRET")


def test_sqlite_lease_state_preserves_usage_limit_across_store_reopen(tmp_path, descriptor, policy):
    db = tmp_path / "leases.sqlite3"
    store = SQLiteLeaseStateStore(db)
    plane, _ = make_plane(descriptor, policy, lease_state=store)
    lease = issue(plane)
    assert plane.execute_with_secret(lease, lambda material: "ok") == "ok"

    reopened = SQLiteLeaseStateStore(db)
    vault = InMemorySecretVault({"slot-v1": "TOP-SECRET-VALUE"})
    second_plane = SecretPlane(
        vault=vault,
        lease_signing_key=b"L" * 32,
        lease_state=reopened,
        clock=Clock(),
        nonce_factory=lambda: "fedcba9876543210fedcba9876543210",
    )
    second_plane.register_secret(descriptor, policy=policy, locator="slot-v1")
    with pytest.raises(SecretAccessDenied):
        second_plane.execute_with_secret(lease, lambda material: "should-not-run")
