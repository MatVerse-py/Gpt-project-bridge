# MatVerse Federation Relation Integrity v1

## Scope

Federation Relation Integrity v1 makes the relation between sovereign domains a first-class governed object.

The existence of two domains does not authorize a relationship between them:

```text
Exist(A) + Exist(B) != AuthorizedRelation(A, B)
```

The protocol is deliberately separate from Omega authorization, execution, and global evidence infrastructure. It verifies only whether a declared cross-domain relation is currently valid for a requested transfer.

## FederationRelation

A directed relation binds:

- `relation_id`;
- `source_domain` and `target_domain`;
- distinct `source_authority` and `target_authority`;
- frozen `contract_hash`;
- explicit capability scope;
- `valid_from` / `valid_until`;
- `ACTIVE` or `REVOKED` state;
- evidence policy;
- an explicit witness scheme;
- bilateral witness.

Wildcard capabilities are forbidden.

## Bilateral witness — current trust-domain implementation

The current implementation uses the repository's existing HMAC trust model:

```text
HMAC-SHA256-SHARED-SECRET-V1
```

The canonical relation payload, including the selected witness scheme, is hashed with SHA-256. The source authority and target authority each HMAC that digest using separately registered secrets.

Secrets are injected into the verifier and never persisted inside the relation or routing receipt. A valid source signature without a valid target signature is insufficient. The inverse is also insufficient.

This is intentionally scoped as a **trust-domain implementation**. Because the verifier has access to both shared secrets, this mechanism alone does not demonstrate cryptographic sovereignty between independently administered domains and does not qualify a run for `EXTERNAL_PASS`. A public-key or independently verifiable witness adapter is a separate promotion gate.

Unsupported witness schemes are rejected rather than silently downgraded to HMAC.

## Trusted time

Temporal validity is evaluated from the gate's injected trusted clock. A crossing or request cannot supply its own evaluation timestamp to reopen an expired or not-yet-valid relation.

For deterministic replay/tests, the verifier may be constructed with a fixed trusted clock. Production callers use the runtime clock or a separately governed time source.

## Fail-closed conditions

A relation is blocked when any of the following is observed:

- status is not `ACTIVE`;
- source or target domain does not match;
- contract hash differs;
- requested capability is outside scope;
- relation is not yet valid or has expired;
- selected witness scheme is unsupported;
- bilateral witness is absent;
- witness scheme differs from the relation contract;
- witness payload hash differs from the current canonical relation;
- source or target authority is unknown;
- either authority signature is invalid.

A blocked relation is removed before capability routing. Weighted preference cannot compensate for a relation-integrity failure.

## Federated routing

`FederatedCapabilityGraph` adds a mandatory relation check in front of the existing `CapabilityGraph`.

```text
request
  -> relation integrity
  -> capability admissibility
  -> preference/routing
  -> route receipt
  -> relation receipt
```

Every traversed cross-domain edge must have one validated `FederationRelation`.

The v1 implementation rejects parallel cross-domain edges between the same ordered pair to keep relation attribution deterministic. A future protocol version may add explicit edge identifiers for multi-edge routing.

## Receipts

The outer relation receipt commits to:

- the existing route receipt hash;
- the ordered relation IDs actually traversed;
- canonical hashes of those relations;
- blocked relation decisions visible during graph construction.

The receipt is deterministic for the same validated inputs and trusted-clock state.

## Boundary

This protocol does **not** claim to:

- create constitutional authority;
- replace Omega/HDB;
- execute target-domain effects;
- prove scientific validity;
- demonstrate administratively independent federation;
- provide provider-independent external reproduction;
- replace EvidenceOS.

It supplies one narrower invariant:

> a cross-domain route may exist only when each traversed boundary is backed by a currently valid, bilaterally witnessed, contract-bound relation.

## Promotion path

The next cryptographic promotion is not another routing feature. It is an asymmetric/public-key witness adapter that lets each domain retain its signing key while the Bridge verifies only public material. That promotion must preserve the same relation payload and fail-closed semantics while removing shared-secret custody from the verifier.
