# Recoverability Contract v1

Protocol: `matverse.recoverability.v1`

## Purpose

Make the Bridge able to answer deterministically, before exposing content:

> Was the object ingested, canonically identified, and authorized for retrieval?

This contract composes existing Project Vault evidence instead of creating a new storage system.

## Proof model

For object (o) and principal (p):

```text
I(o) = ingestion proof
D(o) = identity/attribution proof
A(p,o) = authorization proof

Recoverable(p,o) = I(o) AND D(o) AND A(p,o)
```

### Ingestion proof

Passes only when the object exists in Project Vault and has both:

- `source_hash`
- `ingested_at`

### Identity proof

Passes only when:

- `document_id` is canonical;
- `project_id != "unassigned"`;
- `attribution_basis` is explicit and non-empty.

Owner manual assignment is accepted because it is an auditable attribution event.

### Authorization proof

Uses the existing Owner Access Fabric for the `projectvault` partition.

Passes only when the current principal receives `GRANTED`.

## Reference normalization

Accepted examples:

```text
chat:6a374228-03fc-83e9-ad20-ddc925b091ba
https://chatgpt.com/g/.../c/6a374228-03fc-83e9-ad20-ddc925b091ba
```

A ChatGPT conversation URL is normalized to:

```text
chat:<conversation_id>
```

No network bypass is attempted. The conversation must already have been ingested through an authorized source/export.

## Output

```json
{
  "protocol": "matverse.recoverability.v1",
  "reference": "...",
  "canonical_id": "chat:...",
  "recoverable": true,
  "status": "RECOVERABLE",
  "proofs": {
    "ingestion": {
      "passed": true,
      "source_hash": "...",
      "ingested_at": "..."
    },
    "identity": {
      "passed": true,
      "document_id": "chat:...",
      "conversation_id": "...",
      "project_id": "...",
      "project_name": "...",
      "attribution_basis": "..."
    },
    "authorization": {
      "passed": true,
      "principal": "...",
      "partition_id": "projectvault",
      "access_status": "GRANTED",
      "reason": null
    }
  },
  "next_action": "fetch"
}
```

## Status machine

```text
reference
   |
normalize
   v
object exists? ---- no ----> NOT_INGESTED
   |
  yes
   v
provenance complete? -- no --> INGESTION_UNVERIFIED
   |
  yes
   v
identity resolved? ---- no --> IDENTITY_UNRESOLVED
   |
  yes
   v
authorized? ----------- no --> ACCESS_DENIED / ACCESS_UNAVAILABLE
   |
  yes
   v
RECOVERABLE
   |
   v
fetch
```

## Security rule

`resolve_recoverability` returns proof metadata only. It never returns the object body.

This preserves:

```text
prove access first
!=
expose content first
```

## Architectural placement

```text
Project Vault  -> proves ingestion + identity
Access Fabric  -> proves authorization
Bridge         -> composes the decision
fetch          -> retrieves only after RECOVERABLE
Audit Log      -> records the decision
```

No new sovereign subsystem is introduced.
