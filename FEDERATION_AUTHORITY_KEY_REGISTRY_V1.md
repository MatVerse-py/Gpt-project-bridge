# MatVerse Federation Authority Key Registry v1

## Purpose

This component adds an internal governed lifecycle for Ed25519 federation authority keys while preserving `FederationRelation v1` unchanged.

The architectural rule is:

```text
Identity != Relation
```

A federation relation continues to express bilateral authorization. Authority-key identity, rotation, revocation, and relation-to-key binding are separate Trust Plane objects.

## Objects

### AuthorityKeyRecord

An authority key record contains public material and lifecycle metadata only:

```text
authority_id
key_id
algorithm = Ed25519
public_key_hex
valid_from
valid_until
previous_key_id
revoked_at
revocation_reason
```

Private keys are never accepted or persisted by the registry.

`key_id` is deterministic:

```text
ed25519:<sha256(raw_public_key_bytes)>
```

This makes public-key identity content-addressed and prevents an arbitrary label from silently pointing at different key material.

### FederationRelationKeyBinding

The binding is a separate immutable object:

```text
relation_id
relation_sha256
source_authority
source_key_id
target_authority
target_key_id
```

The binding does not modify the canonical `FederationRelation v1` payload. A relation ID cannot be rebound to different key material.

## Persistence and receipts

Key registration, revocation, and relation-key binding use the existing MatVerse transactional state store.

Each mutation:

1. opens `BEGIN IMMEDIATE`;
2. validates the state transition;
3. appends a canonical event to the existing hash-chained ledger;
4. persists the state row and ledger receipt in the same transaction;
5. commits only after both operations succeed.

Event types:

```text
FEDERATION_AUTHORITY_KEY_REGISTERED
FEDERATION_AUTHORITY_KEY_REVOKED
FEDERATION_RELATION_KEY_BOUND
```

Exact retries are idempotent. Conflicting retries fail closed.

## Rotation

Rotation is a linear lineage:

```text
key_1 -> key_2 -> key_3
```

For v1:

- the first key for an authority is a genesis key and has no `previous_key_id`;
- every successor must reference an existing key owned by the same authority;
- one key can have only one successor;
- `previous.valid_until == successor.valid_from`;
- silent forks and unlinked successors are rejected.

This v1 deliberately forbids overlapping planned rotation windows. Emergency overlap or multi-key quorum is a future protocol version, not an implicit exception.

## Revocation

Revocation is temporal and non-destructive.

A revocation records:

```text
effective_at
reason
revoked_by
ledger receipt
```

The historical key record remains available. Verification before `effective_at` can still succeed if all other conditions hold; verification at or after `effective_at` fails closed.

This preserves historical replay while preventing revoked authority from re-entering future routing.

## Relation validity

At binding time, both bound keys must cover the full relation validity interval. A relation cannot be bound if its validity extends beyond either selected key's declared validity or an already-known revocation point.

A later revocation can shorten the relation's effective routable lifetime without mutating the relation itself.

## Routing enforcement

`GovernedEd25519RelationIntegrityGate` resolves:

```text
relation
  -> immutable key binding
  -> source/target AuthorityKeyRecord
  -> lifecycle state at evaluation time
  -> public keys
  -> Ed25519 signature verification
```

The gate declares:

```text
enforces_key_lifecycle = True
```

`FederatedCapabilityGraph` treats `ED25519-PUBLIC-KEY-V1` as a governance-required witness scheme. If a caller supplies a cryptographic-only gate such as the original `Ed25519RelationIntegrityGate` or a legacy hybrid gate, the edge is blocked with:

```text
governed_key_lifecycle_required
```

Therefore key binding, rotation, and revocation cannot be bypassed merely by choosing the weaker verifier when routing.

## Security invariants

The implementation and regression suite enforce:

1. key IDs are derived from public-key bytes;
2. one public key cannot silently represent multiple authorities;
3. rotation lineage cannot fork;
4. non-genesis keys cannot appear without lineage;
5. relation IDs cannot be rebound to new key material;
6. bound keys must match the relation's declared authorities;
7. source and target keys must be distinct;
8. relation validity must fit inside both key validity intervals;
9. a relation witness must verify under the exact keys being bound;
10. relation payload tampering invalidates the binding and witness;
11. revocation blocks routing at its effective time;
12. Ed25519 routing requires lifecycle-aware verification;
13. lifecycle events remain in the MatVerse hash-chained ledger;
14. `FederationRelation v1` remains structurally unchanged.

## Authority of mutations

The registry methods accept an `actor_id` and persist it into ledger events. The registry core does **not** independently authenticate that string.

Therefore the supported claim is:

> the Bridge has an executable, fail-closed internal public-key lifecycle with ledgered actor attribution.

The stronger claim:

> every registry mutation is proven to have been authorized by an independently authenticated administrative authority

is **not** established by this module alone.

That stronger property requires a Trust Kernel or authenticated API mutation surface that derives `actor_id` from verified request identity and enforces explicit capabilities for key registration, rotation, revocation, and relation binding.

## Claim boundary

### Supported

- internal governed Ed25519 public-key lifecycle;
- deterministic key identity;
- linear rotation lineage;
- temporal revocation;
- immutable relation-to-key binding;
- routing enforcement of lifecycle-aware Ed25519 verification;
- transactional persistence and ledger receipts;
- historical replay without deleting old keys;
- unchanged `FederationRelation v1` canonical structure.

### Not established

- independent organizations actually custody source and target private keys;
- external governance of the public-key registry;
- authenticated administrative authority for direct library calls;
- third-party witnessed key rotation or revocation;
- HSM/KMS-backed custody;
- provider-independent deployment;
- `EXTERNAL_PASS`;
- `WORLD_REAL_PASS`.

## Next promotion gate

The next meaningful gate is an authenticated mutation surface in the Trust Plane:

```text
verified principal
  -> explicit capability
  -> intent-bound registry mutation
  -> AuthorityKeyRecord / FederationRelationKeyBinding
  -> ledger receipt
```

Suggested capabilities:

```text
federation:key:register
federation:key:rotate
federation:key:revoke
federation:relation:bind-key
federation:key:read
```

Only after that surface is proven should actor attribution be promoted from internal metadata to authenticated governance evidence.
