import pytest

from app.social_manifest import SourceCapabilityManifest
from app.social_transport import SocialCapability


def test_manifest_is_explicit_and_secret_free():
    manifest = SourceCapabilityManifest.build(
        source_id="instagram:acct-1",
        provider="meta",
        account_id="acct-1",
        capabilities=[SocialCapability.READ_PROFILE, SocialCapability.READ_MEDIA],
        credential_ref="env:IG_TOKEN",
    )
    data = manifest.as_dict()
    assert data["schema"] == "matverse.source-capability-manifest.v1"
    assert data["capabilities"] == ["read_media", "read_profile"]
    assert "token" not in str(data).lower()


def test_manifest_requires_capabilities():
    with pytest.raises(ValueError, match="capability"):
        SourceCapabilityManifest.build(
            source_id="instagram:acct-1",
            provider="meta",
            account_id="acct-1",
            capabilities=[],
            credential_ref="env:IG_TOKEN",
        )
