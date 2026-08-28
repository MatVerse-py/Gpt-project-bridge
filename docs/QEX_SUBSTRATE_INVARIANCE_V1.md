# QeX Computational Substrate Invariance v1

Status: IMPLEMENTED_FOR_REVIEW

## Canonical placement

QeX remains a specialized quantum-classical experimentation bench under URANO. This change does not create a new MatVerse organ or a quantum-first runtime.

## Principle

`Experiment Identity != Computational Substrate`

The same frozen experiment contract may be evaluated against classical, hybrid, gate-model quantum, or annealing backends. Backend choice is capability-based and occurs only after constitutional admissibility.

## Flow

`ProblemSpec -> ExperimentContract -> CapabilityProfile[] -> Omega/HDB admissibility -> hard capability filtering -> preference routing -> execution adapter -> canonical observable result -> substrate comparison -> EvidenceOS receipt`

## QEX-SUBSTRATE-01

The first benchmark freezes:

- problem_hash
- objective
- metric_schema_hash
- observable_schema_hash
- evidence_policy_hash
- required capabilities
- budget/latency limits

Only the computational substrate is intended to vary.

Comparison states:

- EXACT
- FUNCTIONALLY_EQUIVALENT
- STATISTICALLY_EQUIVALENT
- DIVERGENT
- INCOMPARABLE

## Scientific boundary

`QUANTUM_USED` never implies `QUANTUM_ADVANTAGE`.

A classical baseline is required by default. A quantum backend may be selected only when it is admissible and its declared, instrumented capability profile justifies the choice. Unknown or missing required measurements fail closed when the experiment contract depends on them.

Topological hardware remains treated as experimental/contested unless the registered capability profile explicitly carries a verified/production maturity state.

## Current implementation scope

`app/qex_substrate.py` implements:

- ComputeRegime
- QuantumModality
- CapabilityProfile
- ExperimentContract
- hard candidate admissibility
- post-admissibility preference routing
- EvidenceOS-compatible receipts
- cross-substrate observable comparison

No vendor SDK or QPU execution is claimed in v1. Physical execution adapters are intentionally a later layer; this slice establishes the substrate-neutral contract and comparator first.
