# MatVerse structural diagrams

These diagrams are documentation views over the current MatVerse architecture.
They are not evidence of scientific claims by themselves.

Canonical terminology used here:

- `mnb` = Mem-Nano-Bit, informational primitive/node.
- `mem-bit` = digital/contract representation.
- `m-bit` = physical/item representation.
- MatVerse = field of informational possibilities.
- Organism = governed information + admitted capabilities + causal continuity.
- Bridge = governed mediation/admission/transition mechanism across partitions,
  capabilities and substrates.
- `Q-Gate` = historical/UI alias for the `Ω-GATE::CanPublish` projection, not a
  second constitutional organ.

## 1. L0-L7 working structural view

The L0-L7 view is retained as a useful structural projection, not as a claim that
these eight layers are the only valid decomposition of MatVerse.

```mermaid
graph TB
    L7["L7 · Auto-structure / meta-adaptation"]
    L6["L6 · Audit / evidence / replay"]
    L5["L5 · Semantic/cognitive transformation"]
    L4["L4 · Governance / value / risk"]
    L3["L3 · Ω-Gate admissibility boundary"]
    L2["L2 · mnb state / local decisions"]
    L1["L1 · Identity / coherence / invariants"]
    L0["L0 · Time / causal ordering"]

    L0 --> L1 --> L2 --> L3 --> L4 --> L5 --> L6 --> L7
    L7 -. governed mutation proposal .-> L3
    L6 -. evidence .-> L3
```

## 2. Organism physiology as a closed loop

```mermaid
stateDiagram-v2
    [*] --> Perceive
    Perceive --> Govern: observation / state input
    Govern --> Hold: Ω = HOLD
    Govern --> Block: Ω = BLOCK
    Govern --> Act: Ω = ADMIT
    Act --> Observe: admitted capability transforms state
    Observe --> Evidence: consequence measured
    Evidence --> Inherit: receipt / ledger / causal constraint
    Inherit --> Perceive: updated governed state
    Hold --> Perceive: unresolved evidence retained
    Block --> Evidence: blocked proposal is evidence too
```

The organism is not the model executing one step. The identity-bearing object is
its governed state, authority, lineage and admitted capability set.

## 3. Corpus coverage + private fractions + public commitments

```mermaid
flowchart TD
    K["Known accessible universe K"] --> S1["Complete sweep"]
    S1 --> CR["CoverageRegistry"]
    CR --> D{"Δobjects=0?<br/>Δsources=0?<br/>missing=0?<br/>R_ext=∅?"}
    D -->|no| R["Classify / relate / adjudicate / extend source scope"]
    R --> S1
    D -->|yes| Z{"Two consecutive clean complete sweeps<br/>over same scope hash?"}
    Z -->|no| S1
    Z -->|yes| SAT["DISCOVERY_SATURATED"]

    SAT --> CM["Canonical membership + ordering"]
    CM --> FP["matverse.corpus-fractions.v1<br/>private deterministic plan"]
    FP --> F1["F1"]
    FP --> F2["F2"]
    FP --> FN["Fn"]
    F1 --> SR["private structural merge_root"]
    F2 --> SR
    FN --> SR
    FP --> HC["matverse.corpus-commitments.v1"]
    HC --> PR["public salted fraction roots"]
    PR --> CR2["public corpus commitment root"]
```

`DISCOVERY_SATURATED` is scoped and closed under currently observed source
references. It does not claim that nothing exists outside the accessible universe.

The deterministic structural `merge_root` is kept private when the source material
is private or low entropy. Public publication integrity uses salted commitments.

## 4. Fraction publication governance

```mermaid
sequenceDiagram
    autonumber
    participant C as Canonical corpus
    participant F as Fraction/Commitment Bridge
    participant A as Body-A proposer
    participant D as Body-D adjudicator
    participant X as Body-X executor
    participant Z as Zenodo/provider

    C->>F: private FractionPlan
    F->>F: salted commitment work
    F-->>A: privacy-safe envelope
    A->>D: Ω-GATE::CanPublish proposal
    Note over A,D: default HOLD / MARXIV.Prepared
    alt BLOCK or TTL expiry
        D-->>A: BLOCK
    else independent ADMIT
        D-->>A: ADMIT / MARXIV.Approved
        Note over A,D: no provider call yet
        X->>Z: external write with scoped credential
        Z-->>X: provider receipt
        X-->>F: MARXIV.Submitted receipt
    end
```

Rules:

```text
Body-A != Body-D != Body-X
Prepared != Approved != Submitted
commitment root != disclosure authorization
ADMIT != external execution
```

Raw chats, private files, deterministic source hashes and salts are never made
public by default.

## 5. Complete Bridge planes

```mermaid
flowchart LR
    U["User / constitutional authority"] --> G["Governance plane"]

    subgraph B["Bridge"]
      IF["Information federation\nProject Vault · Drive · Mail · GitHub · Notion"]
      MF["Model / capability federation\nGPT · Claude · Manus · MiniMax · local runtimes"]
      RD["Runtime discovery / executor binding"]
      PB["Publication bridge\nGit · HF · arXiv · Zenodo"]
      CF["Corpus fractions / commitments / semantic continuity"]
    end

    G --> IF
    G --> MF
    G --> RD
    G --> PB
    G --> CF

    IF --> E["Evidence / receipts / ledger / replay"]
    MF --> E
    RD --> E
    PB --> E
    CF --> E

    E --> O["Organism governed state"]
    O --> G
```

## 6. Capability admission

```mermaid
flowchart TD
    ENV["Environment / host / provider"] --> DISC["DISCOVERED capability"]
    DISC --> SPEC["Contract / permissions / authority scope"]
    SPEC --> IMPL["Implementation binding"]
    IMPL --> TEST["Tests + evidence"]
    TEST --> GOV{"Governance"}
    GOV -->|ADMIT| CAP["Admitted capability"]
    GOV -->|HOLD| HOLD["HOLD"]
    GOV -->|BLOCK| BLOCK["BLOCKED"]
    CAP --> PHY["Organism physiology"]
    PHY --> E["Receipt / evidence"]
    E --> GOV
```

`SkillCreated != SkillAdmitted`.

## 7. Ontological compression

```text
MatVerse   = informational possibility field
mnb        = localized informational primitive / node
Governance = admissibility boundary
Bridge     = governed admission + transition + mediation
Capability = possibility of transformation
Organism   = governed information + admitted capabilities + causal continuity
Physiology = causal exercise of admitted capabilities over governed information
Evidence   = record that a proposed/possible transition was evaluated or actualized
```
