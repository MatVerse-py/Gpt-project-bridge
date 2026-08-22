# MatVerse Model Bridge v1

## Scope

Model Bridge v1 turns the existing governed Bridge into a model-neutral relay for observable, explicit state. It does not transfer hidden reasoning, provider-private memory, system/developer prompts, credentials, or model weights.

The protocol is designed so a task can move between distinct models while the experiment contract remains fixed.

## Invariants

1. **Model-neutral state** — state belongs to the Bridge session, not to a provider.
2. **Frozen contract** — ontology, policy, task, rubric, and memory policy are pinned by SHA-256 before exchange.
3. **Explicit participants** — only enrolled model identities may send or receive handoffs.
4. **No hidden-state transfer** — chain-of-thought, reasoning traces, private memory, system/developer prompts, credentials, and hidden state are rejected.
5. **Fail closed** — ontology, signature, transition, HDB, or contract mismatch prevents handoff storage.
6. **Receipt before continuity** — session creation, accepted handoff, acknowledgement, and rejected exposure attempts are ledger events.
7. **Portability is not text equality** — cross-model portability is evaluated only against pre-declared observable invariants.
8. **Human data boundary remains active** — SECRET or unauthorized third-party data never enters a model handoff.

## Frozen contract

A session binds five SHA-256 values:

- `ontology_hash`
- `policy_hash`
- `task_hash`
- `rubric_hash`
- `memory_policy_hash`

The Bridge derives one `contract_hash` from those values plus protocol version. Every handoff must present the same hash. Drift is rejected.

## Participants

Each participant has:

- `participant_id`
- `provider`
- `model`
- optional `revision`
- `endpoint_class`: `LOCAL`, `REMOTE`, or `UNKNOWN`

A cross-model session requires at least two distinct model identities.

## Handoff lifecycle

```text
source model
   |
   | observable payload
   v
Model Bridge
   |
   | hidden-state scan
   | HDB
   | Omega gate
   | frozen-contract check
   | participant check
   v
PENDING handoff + receipt
   |
   v
target inbox
   |
   v
ACK + receipt
```

A rejected handoff records only metadata and payload hash. The rejected raw payload is not persisted by the ledger path.

## API

### Protocol

`GET /model-bridge/protocol`

Returns the protocol version, state boundary, forbidden classes, and portability semantics.

### Create session

`POST /model-bridge/sessions`

Creates an immutable session contract and participant set after Omega-Gate admission.

### Read session

`GET /model-bridge/sessions/{session_id}`

Returns frozen contract metadata and enrolled participants.

### Create handoff

`POST /model-bridge/sessions/{session_id}/handoffs`

Submits an observable payload from one enrolled model to another. A stale or different contract hash is rejected.

### Inbox

`GET /model-bridge/sessions/{session_id}/inbox/{participant_id}`

Returns only unacknowledged handoffs addressed to the participant.

### Acknowledge

`POST /model-bridge/handoffs/{handoff_id}/ack`

Only the target participant may acknowledge a handoff. Repeated acknowledgement is idempotent.

### Compare observable invariants

`POST /model-bridge/compare`

Supported deterministic comparison modes:

- `exact`
- `set_equal`
- `type_equal`

The endpoint deliberately does not compute semantic equivalence from free text. The experiment must declare the observable invariants before execution.

## Cross-model scientific use

For H/M/HM-fixed/HM-adaptive work, the Bridge keeps the experimental contract fixed and lets the model identity vary. A valid portability claim requires the pre-declared invariant set to remain satisfied under the declared model substitution. This implementation does not by itself establish Third Order, coadaptation, external reproduction, or provider equivalence.

## Current promotion boundary

Implemented in this branch:

- provider-neutral session contract;
- frozen-state hashing;
- explicit participant enrollment;
- governed handoff relay;
- hidden/private state rejection;
- target inbox and acknowledgement;
- deterministic portability comparison;
- ledger receipts;
- concurrency-safe ledger append path;
- tests and CI workflow.

Not claimed:

- live invocation of OpenAI, Anthropic, Google, Manus, or local model APIs;
- external provider credentials or OAuth brokerage;
- semantic equivalence oracle;
- cross-model empirical portability result;
- external witness validation.

Those are separate adapters and experiments layered on this protocol rather than responsibilities of the protocol core.
