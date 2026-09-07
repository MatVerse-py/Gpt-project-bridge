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

## 3. Corpus fractions + No-Left-Behind

```mermaid
flowchart TD
    K["Known universe K"] --> S1["Exhaustive sweep"]
    S1 --> CR["CoverageRegistry"]
    CR --> Q{"New / missing / orphan items?"}
    Q -->|yes| R["Classify / relate / adjudicate / supersede"]
    R --> S1
    Q -->|no| Z{"Two consecutive zero-delta complete sweeps?"}
    Z -->|no| S1
    Z -->|yes| SAT["DISCOVERY_SATURATED"]

    SAT --> CM["Canonical membership + ordering"]
    CM --> FP["matverse.corpus-fractions.v1"]
    FP --> F1["F1"]
    FP --> F2["F2"]
    FP --> FN["Fn"]
    F1 --> MR["fraction merge_root"]
    F2 --> MR
    FN --> MR
```

`DISCOVERY_SATURATED` is scoped: it means two complete sweeps over the same
registered partition set found no new or missing objects. It does not claim that
nothing exists outside the accessible universe.

## 4. Fraction publication governance

```mermaid
sequenceDiagram
    autonumber
    participant C as Canonical corpus
    participant F as Fraction Bridge
    participant G as Governance
    participant P as Publication Bridge
    participant Z as Zenodo Executor

    C->>F: verified FractionPlan + merge_root
    F->>F: build privacy-safe envelope
    F->>G: request disclosure classification
    alt metadata only
        G-->>F: METADATA_ONLY
        F->>P: manifest-only Zenodo draft plan
    else redacted/public bundle
        G-->>F: REDACTED_BUNDLE or PUBLIC_BUNDLE + bundle hash
        F->>P: draft plan + expected bundle hash
    end
    P->>G: request external-write authorization
    alt no matching authorization
        G-->>P: HOLD/BLOCK
    else authorized
        G-->>P: authorization receipt
        P->>Z: zenodo.create_draft / zenodo.publish
        Z-->>P: provider result
    end
```

The `merge_root` proves structural integrity. It does **not** authorize disclosure.
Raw chats, private files or source bundles are never made public by default.

## 5. Complete Bridge planes

```mermaid
flowchart LR
    U["User / constitutional authority"] --> G["Governance plane"]

    subgraph B["Bridge"]
      IF["Information federation\nProject Vault · Drive · Mail · GitHub · Notion"]
      MF["Model / capability federation\nGPT · Claude · Manus · MiniMax · local runtimes"]
      RD["Runtime discovery / executor binding"]
      PB["Publication bridge\nGit · HF · arXiv · Zenodo"]
      CF["Corpus fractions / semantic continuity"]
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

## 6. Ontological compression

```text
MatVerse   = informational possibility field
mnb        = localized informational primitive / node
Governance = admissibility boundary
Bridge     = governed admission + transition + mediation
Capability = possibility of transformation
Organism   = governed information + admitted capabilities + causal continuity
Physiology = causal exercise of admitted capabilities over governed information
Evidence   = record that a proposed/possible transition was actually evaluated or actualized
```
