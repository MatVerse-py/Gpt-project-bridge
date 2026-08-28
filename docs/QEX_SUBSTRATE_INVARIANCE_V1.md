# QeX Computational Substrate Invariance v1

Status: IMPLEMENTED_AND_OBSERVED_FOR_SIMULATION

## Canonical placement

QeX remains a specialized quantum-classical experimentation bench under URANO. This change does not create a new MatVerse organ or a quantum-first runtime.

## Principle

`Experiment Identity != Computational Substrate`

The same frozen experiment contract may be evaluated against classical, hybrid, gate-model quantum, annealing, and controlled hardware-realism proxies. Backend choice is capability-based and occurs only after constitutional admissibility.

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

Observed execution classes now include:

- CPU classical reference
- MatVerse dependency-free ideal statevector simulator
- Qiskit statevector SDK adapter
- controlled bit-flip/readout noise proxy

Comparison states:

- EXACT
- FUNCTIONALLY_EQUIVALENT
- WITHIN_TOLERANCE
- STATISTICALLY_EQUIVALENT (reserved for a future repeated-sample statistical-test contract)
- DIVERGENT
- INCOMPARABLE

`WITHIN_TOLERANCE` means only that declared numeric deltas remain inside contract tolerances. It must not be interpreted as statistical equivalence.

## Noise realism gate

The controlled-noise adapter preserves the hard experiment identity while perturbing the binary outcome distribution by a configured symmetric error probability. Total variation distance (TVD) is measured explicitly.

For the controlled tests:

- error probability 0.05 produces TVD 0.05 and is accepted only when tolerance is 0.05
- error probability 0.10 produces TVD 0.10 and is DIVERGENT under tolerance 0.05
- invalid noise configuration fails closed

This adapter is a deterministic hardware-realism proxy. It is not a physical QPU and is not a calibrated device-faithful noise model.

## Scientific boundary

`QUANTUM_USED` never implies `QUANTUM_ADVANTAGE`.

A classical baseline is required by default. A quantum backend may be selected only when it is admissible and its declared, instrumented capability profile justifies the choice. Unknown or missing required measurements fail closed when the experiment contract depends on them.

The Qiskit adapter proves execution through an independent external SDK, but remains simulation. The controlled-noise adapter proves tolerance/divergence handling, but remains synthetic. Neither establishes physical QPU execution, fault tolerance, or quantum advantage.

Topological hardware remains treated as experimental/contested unless the registered capability profile explicitly carries a verified/production maturity state.

## Current implementation scope

Implemented:

- ComputeRegime and QuantumModality
- CapabilityProfile and frozen ExperimentContract
- Omega/HDB admissibility before preference routing
- classical baseline requirement
- deterministic EvidenceOS-compatible receipts
- CPU classical execution adapter
- internal ideal statevector adapter
- Qiskit statevector SDK adapter
- controlled-noise adapter
- TVD divergence metric
- cross-substrate canonical comparator
- dedicated GitHub Actions gate

Not yet claimed:

- physical QPU execution
- device-calibrated noise reproduction
- error correction
- quantum advantage
- statistical equivalence from repeated hardware samples
