# OpenAI Secret Plane v2

## Canonical route

```text
platform secret manager / process injection
        ↓
EnvironmentSecretVault
        ↓
Secret Plane
        ↓
provider-exposure preflight
        ↓ PASS only
principal-bound CapabilityLease
TTL=30s / max_uses=1
        ↓
ephemeral credential disclosure
        ↓
OpenAIResponsesRuntime
        ↓
Responses API
        ↓
metadata-only evidence
```

The authenticated `/providers/openai/*` route no longer constructs `OpenAIResponsesRuntime` from `OPENAI_API_KEY` directly. The environment variable is treated as a secret-store injection surface and read only through `EnvironmentSecretVault` during a bounded Secret Plane disclosure.

## Invariants

1. Provider-exposure governance runs before secret disclosure.
2. A BLOCK/HOLD does not issue a credential lease and does not read the vault.
3. A passing request receives a lease bound to the requesting principal, capability `openai.responses`, scope `provider:openai:invoke`, TTL 30 seconds and one use.
4. The raw credential and opaque environment locator are absent from provider responses, ledger events, EvidenceOS receipts and Secret Plane audit output.
5. `OPENAI_SECRET_VERSION` is public metadata used to bind rotation state; the credential value is not hashed into evidence.
6. `store=False` remains mandatory for OpenAI Responses requests.
7. The process-local lease-signing authority is generated with the OS CSPRNG and is not persisted. Leases are intentionally not portable across process restarts.

## Compatibility boundary

`OpenAIResponsesRuntime.from_env()` remains available for low-level compatibility and direct unit tests, but it is no longer used by the authenticated provider route. The canonical production path is Secret Plane mediated.

## Current storage adapter

`EnvironmentSecretVault` is a read-through adapter, not a persistent vault. Production deployments should continue injecting `OPENAI_API_KEY` from a platform secret manager, OS keychain, HSM-backed service or external vault. Migrating to a stronger vault adapter does not change the provider contract.

## Evidence boundary

This implementation proves application-layer mediation, bounded disclosure and non-persistence in MatVerse evidence surfaces. It does not prove resistance to a compromised host process, debugger, kernel, hypervisor, side channel, or malicious trusted consumer after credential access has been granted.
