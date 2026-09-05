# MatVerse Secret Plane v1

Status: implementation candidate
Schema: `matverse.secret-plane.v1`

## Canonical rule

A secret value is not corpus state.

The corpus may retain metadata describing a secret, its authority, scope, version, lifetime and revocation state, but it must not persist the secret value, an encrypted copy of that value, or a hash intended to fingerprint that value.

Normative invariants:

- `SecretValue ∉ Corpus`
- `SecretValue ∉ Prompt`
- `SecretValue ∉ Ledger`
- `SecretValue ∉ Receipt`
- `SecretValue ∉ BridgePortableState`
- possession of a provider credential does not imply authority to disclose arbitrary data to that provider
- secret authority is scoped by actor, capability, scope, time, use count and secret version
- rotation invalidates outstanding leases for prior versions
- revocation invalidates outstanding leases and removes the local binding
- destruction is only claimed if the selected vault adapter can actually destroy the referenced material

## Components

`SecretDescriptor`
: public metadata only: id, kind, owner, purpose, provider, storage class, version, lifetime and state.

`SecretPolicy`
: allowlists actors, capabilities and scopes, with TTL and usage ceilings.

`CapabilityLease`
: short-lived HMAC-authenticated authority to use one secret version. It carries no secret value. The signature is not written into Secret Plane audit events.

`DisclosureGate`
: evaluates actor + capability + scope independently from possession of a credential.

`SecretVaultAdapter`
: the only interface allowed to resolve opaque secret bindings into secret material.

`EnvironmentSecretVault`
: migration/read-through adapter for process environments. It is explicitly not a persistent vault and cannot claim cryptographic destruction of environment variables.

`InMemorySecretVault`
: test-only implementation. It must not be presented as production secure storage.

`LeaseStateStore`
: accounts for bounded lease use and revocation. `SQLiteLeaseStateStore` makes use-count/revocation state durable without storing secret values or vault locators.

`KeyAuthority`
: uses the operating-system CSPRNG and domain-separated HKDF-SHA256. It generates/derives key material but intentionally does not decide where that material is persisted.

`SecretExposureDetector`
: conservative DLP heuristics for private-key headers, bearer tokens, OpenAI-like keys and credential assignments. Findings contain rule and offsets only; matched text is never returned.

## Runtime path

```text
Secret Manager / HSM / TPM / OS Keychain / Platform Secret
        |
        | opaque binding
        v
    Secret Plane
        |
        | SecretDescriptor + SecretPolicy
        v
  Disclosure Gate
        |
        | bounded CapabilityLease
        v
   runtime consumer
        |
        | ephemeral material view
        v
 provider/tool action
        |
        v
  metadata-only audit
```

The Secret Plane zeroes its local mutable byte buffer after use. This is best-effort process hygiene, not a claim that Python, the operating system, an environment variable, a provider SDK, crash dump or arbitrary consumer made no additional copies.

## Key/authority separation

Secret Plane does not replace public-key signing.

Use HMAC for trust domains that intentionally share a secret, such as in-process lease authentication or authenticated internal state. Use asymmetric signatures such as Ed25519 when third parties must verify evidence without possessing signing authority.

Recommended separation:

- Organism state authentication key
- constraint/authority grant key
- request-authentication principal secret
- provider credential
- human approval private key
- Cassandra/release private key
- device identity key
- evidence-signing private key
- Secret Plane lease-signing key

No universal root key should be reused directly across these purposes.

## Rotation and revocation

`rebind_secret(secret_id, new_locator, new_version)` is the local acknowledgement of a rotated secret. It does not claim that a third-party provider revoked the old credential. Provider-side rotation/revocation remains an external operation that must be independently confirmed.

`revoke_secret` removes local authority to resolve/use the secret and revokes active leases.

`destroy_secret` calls the vault adapter's destruction operation first. If the adapter cannot destroy the material, the operation fails closed and Secret Plane does not promote the descriptor to `DESTROYED`.

## Secret handling boundary

A consumer receives a `memoryview` to a mutable local buffer. Secret Plane rejects common return structures that contain the raw secret, but arbitrary consumer code can always copy or exfiltrate data once granted access. Therefore the security boundary is:

```text
Secret Plane governance + trusted consumer implementation
```

not Secret Plane alone.

## Migration map from corpus techniques

Promote directly:

- environment/platform secret injection
- no hardcoding
- HMAC request/state authentication
- anti-replay nonce handling
- capability-scoped authority
- Ed25519 public verification patterns
- blocked-payload minimization
- hidden-state/secret interdiction
- closed-envelope allowlists
- Secret Plane separation
- unique per-purpose keys
- scoped nullifier principle
- rotation/revocation metadata

Preserve as specification until independently implemented:

- PQXDH / Double Ratchet
- MLS group epochs
- TPM/HSM hardware binding
- crypto-shredding in external storage
- provider-side automatic rotation after detected exposure

Do not promote literally:

- historical hardcoded credentials
- denylist-only DLP
- simulated TPM as hardware attestation
- RAM-only storage as proof of non-extractability
- entropy recipes that replace the operating-system CSPRNG with timing/thermal/network noise

## Current OpenAI migration

The current OpenAI runtime already satisfies several Secret Plane invariants: it reads `OPENAI_API_KEY` from runtime environment, uses `repr=False`, fixes provider egress to the OpenAI API origin, disables redirects, sanitizes provider errors and excludes the key from request/response hashes, metadata, receipts and Bridge state.

Secret Plane v1 intentionally lands first as a provider-neutral governance primitive. The next migration is to issue a bounded lease to the OpenAI runtime so that the provider adapter receives the credential only for an authorized invocation instead of reading it as long-lived application state.

## Evidence boundary

A passing Secret Plane test suite proves the local implementation invariants exercised by those tests. It does not prove protection against host compromise, malicious root/administrator access, process-memory inspection, compromised provider SDKs, side channels, physical extraction, or hardware-backed key custody.
