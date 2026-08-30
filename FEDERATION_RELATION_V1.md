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
- bilateral witness.

Wildcard capabilities are forbidden.

## Bilateral witness

The canonical relation payload is hashed with SHA-256. The source authority and target authority independently HMAC that digest using their registered secrets.

Secrets are injected into the verifier and never persisted inside the relation or routing receipt.

A valid source signature without a valid target signature is insufficient. The inverse is also insufficient.

## Fail-closed conditions

A relation is blocked when any of the following is observed:

- status is not `ACTIVE`;
- source or target domain does not match;
- contract hash differs;
- requested capability is outside scope;
- relation is not yet valid or has expired;
- bilateral witness is absent;
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

The receipt is deterministic for the same validated inputs.

## Boundary

This protocol does **not** claim to:

- create constitutional authority;
- replace Omega/HDB;
- execute target-domain effects;
- prove scientific validity;
- provide provider-independent external reproduction;
- replace EvidenceOS.

It supplies one narrower invariant:

> a cross-domain route may exist only when each traversed boundary is backed by a currently valid, bilaterally witnessed, contract-bound relation.
