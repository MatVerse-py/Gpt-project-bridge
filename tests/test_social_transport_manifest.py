import pytest

from app.social_credentials import CredentialBroker, CredentialRef, EnvironmentSecretProvider
from app.social_manifest import SourceCapabilityManifest
from app.social_transport import MetaInstagramTransport, SocialCapability


def _manifest():
    return SourceCapabilityManifest.build(
        source_id="instagram:acct-1",
        provider="meta",
        account_id="acct-1",
        capabilities=[SocialCapability.READ_SELF],
        credential_ref="env:IG_TOKEN",
    )


def test_transport_builds_only_from_matching_manifest_and_credential():
    broker = CredentialBroker(EnvironmentSecretProvider({"IG_TOKEN": "secret"}))
    credential = CredentialRef(provider="meta", account_id="acct-1", secret_ref="env:IG_TOKEN")
    transport = MetaInstagramTransport.from_manifest(manifest=_manifest(), credential=credential, broker=broker)
    assert transport.name == "meta.instagram.v1"


def test_manifest_account_mismatch_fails_before_secret_resolution():
    class BombProvider:
        def resolve(self, secret_ref: str) -> str:
            raise AssertionError("secret must not be resolved")

    credential = CredentialRef(provider="meta", account_id="other", secret_ref="env:IG_TOKEN")
    with pytest.raises(PermissionError, match="identity"):
        MetaInstagramTransport.from_manifest(
            manifest=_manifest(), credential=credential, broker=CredentialBroker(BombProvider())
        )


def test_manifest_secret_reference_mismatch_fails_closed():
    broker = CredentialBroker(EnvironmentSecretProvider({"OTHER": "secret"}))
    credential = CredentialRef(provider="meta", account_id="acct-1", secret_ref="env:OTHER")
    with pytest.raises(PermissionError, match="reference"):
        MetaInstagramTransport.from_manifest(manifest=_manifest(), credential=credential, broker=broker)
