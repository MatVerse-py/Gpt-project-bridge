# MatVerse Federation Ed25519 Witness v1

## Purpose

This adapter removes shared-secret custody from the Bridge for federation-relation verification.

The existing `FederationRelation.canonical_payload()` remains unchanged. The selected witness scheme is still part of that payload, and Ed25519 signs the resulting relation SHA-256 digest with explicit scheme and role domain separation.

```text
canonical FederationRelation
        -> SHA-256
        -> source Ed25519 signature
        -> target Ed25519 signature
        -> public-key-only verification
```

## Scheme

```text
ED25519-PUBLIC-KEY-V1
```

The signature message is:

```text
matverse.federation-relation.v1
ED25519-PUBLIC-KEY-V1
<source|target>
<relation_payload_sha256>
```

The role is part of the signed message, so a source signature cannot be replayed as a target signature and vice versa.

## Independent signing

Source and target signatures are attached by separate functions:

- `sign_relation_ed25519_source(...)`
- `sign_relation_ed25519_target(...)`

They may execute in separate processes and in either order. Each function receives only that authority's private key. A partially signed relation is representable for transport but is never admissible for routing.

Private keys are not serialized into the relation, witness, receipt, or verifier.

## Verification boundary

`Ed25519RelationIntegrityGate` receives only an authority-to-public-key registry. It validates:

- relation status;
- source and target domains;
- contract hash;
- capability scope;
- validity window;
- witness scheme;
- canonical payload hash;
- existence of both authority public keys;
- distinct source and target public keys;
- source-role signature;
- target-role signature.

Any failure blocks the relation.

## Migration

`HybridRelationIntegrityGate` supports both schemes during migration:

```text
HMAC-SHA256-SHARED-SECRET-V1  -> legacy trust-domain verification
ED25519-PUBLIC-KEY-V1         -> public-key-only verification
anything else                 -> fail closed
```

HMAC is retained for backward compatibility and internal trust-domain use. It is not promoted to independent-domain evidence.

## Security invariants

The implementation and tests enforce:

1. one valid authority signature is insufficient;
2. signatures are bound to source/target roles;
3. changing the canonical relation after signing invalidates the witness;
4. swapping registry public keys invalidates the witness;
5. distinct authority IDs using one public key fail closed;
6. missing registry keys fail closed;
7. duplicate signatures are rejected rather than overwritten;
8. Ed25519 relations remain compatible with the existing federated routing graph;
9. HMAC relations continue to verify through the hybrid migration gate.

## Claim boundary

This adapter supports the narrower statement:

> the Bridge can verify bilateral relation authorization without custody of either authority's private signing key.

It does **not** by itself prove:

- independent organizations operated the two keys;
- the public-key registry is externally governed;
- key rotation/revocation is independently witnessed;
- provider-independent deployment;
- `EXTERNAL_PASS`;
- `WORLD_REAL_PASS`.

Those claims require separate evidence.

## Next promotion gate

The next meaningful gate is governed public-key identity lifecycle: authority key IDs, rotation/revocation history, registry receipts, and an execution where source and target private keys are controlled by separate administrative domains.
