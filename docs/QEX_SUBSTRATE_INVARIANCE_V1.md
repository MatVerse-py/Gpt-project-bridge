# QeX Computational Substrate Invariance v1

Status: IMPLEMENTED_AND_OBSERVED_FOR_SIMULATION

## Canonical placement

QeX remains a specialized quantum-classical experimentation bench under URANO. This change does not create a new MatVerse organ or a quantum-first runtime.

## Principle

`Experiment Identity != Computational Substrate`

The same frozen experiment contract may be evaluated against classical, hybrid, gate-model quantum, annealing, controlled-noise, and hardware-derived noise substrates. Backend choice is capability-based and occurs only after constitutional admissibility.

## Flow

`ProblemSpec -> ExperimentContract -> CapabilityProfile[] -> Omega/HDB admissibility -> hard capability filtering -> preference routing -> execution adapter -> canonical observable result -> substrate comparison -> EvidenceOS receipt`

## QEX-SUBSTRATE-01

The benchmark freezes:

- experiment_id
- problem_hash
- objective
- metric_schema_hash
- observable_schema_hash
- evidence_policy_hash
- required capabilities
- budget/latency limits

The canonical contract is materialized by `app/qex_experiment.py`. Derived adjudication metrics such as total variation distance (TVD) are intentionally outside the contract hash. They describe the comparison between executions; they do not silently redefine the experiment being compared.

Observed or implemented execution classes include:

- CPU classical reference
- MatVerse dependency-free ideal statevector simulator
- Qiskit statevector SDK adapter
- controlled bit-flip/readout noise proxy
- Qiskit Aer noise model derived from the bundled `FakeSherbrooke` historical hardware snapshot

Comparison states:

- EXACT
- FUNCTIONALLY_EQUIVALENT
- WITHIN_TOLERANCE
- STATISTICALLY_EQUIVALENT (reserved for a future repeated-sample statistical-test contract)
- DIVERGENT
- INCOMPARABLE

`WITHIN_TOLERANCE` means only that declared numeric deltas remain inside configured tolerances. It must not be interpreted as statistical equivalence.

## Noise realism ladder

### L0 — ideal

CPU, internal statevector, and Qiskit statevector establish substrate-independent execution under an identical frozen contract.

### L1 — controlled synthetic noise

The controlled-noise adapter perturbs the binary outcome distribution by a configured symmetric error probability. TVD is measured explicitly. In the controlled tests, error probability 0.05 yields TVD 0.05 and is inside a 0.05 tolerance; error probability 0.10 yields TVD 0.10 and is DIVERGENT under that tolerance.

### L2 — hardware-derived historical snapshot

`AerHardwareSnapshotNotAdapter` constructs a Qiskit Aer `NoiseModel` from Qiskit Runtime's `FakeSherbrooke` backend snapshot. Execution uses finite shots and fixed simulator/transpiler seeds. The evidence receipt binds backend metadata, package versions, seed, shot count, source backend identity, and noise-model basis gates into `backend_metadata_hash`.

This level is materially stronger than a scalar synthetic error model because the noise model is derived from a hardware snapshot. It remains a historical/mock snapshot bundled with the pinned runtime package. It is **not** current device calibration and **not** execution on a physical QPU.

### L3 — current calibration replay (next gate)

A live Qiskit-compatible backend may be used to construct `NoiseModel.from_backend(backend)` or `AerSimulator.from_backend(backend)` from current calibration data. This requires authenticated backend access and must record calibration/backend identity in evidence.

### L4 — physical QPU execution (future gate)

The same frozen QEX-SUBSTRATE-01 contract is submitted to a physical backend. Hardware execution must preserve experiment identity while recording queue, shots, backend, compilation, calibration context, and raw measurement evidence.

## Scientific boundary

`QUANTUM_USED` never implies `QUANTUM_ADVANTAGE`.

A classical baseline is required by default. A quantum backend may be selected only when it is admissible and its declared, instrumented capability profile justifies the choice. Unknown or missing required measurements fail closed when the experiment contract depends on them.

The Qiskit adapter proves execution through an independent external SDK, but remains ideal simulation. The controlled-noise adapter proves tolerance/divergence handling, but remains synthetic. The FakeSherbrooke/Aer path exercises a hardware-derived historical snapshot, but remains simulation. None establishes physical QPU execution, fault tolerance, or quantum advantage.

Topological hardware remains treated as experimental/contested unless the registered capability profile explicitly carries a verified/production maturity state.

## Pinned SDK stack

The hardware-snapshot gate intentionally pins the documented compatible stack used by IBM's current noise-model guide rather than the newest runtime client in isolation:

- qiskit 2.5.1
- qiskit-aer 0.17.2
- qiskit-ibm-runtime 0.47.0

`qiskit-ibm-runtime 0.49.0` currently requires `pydantic>=2.13.0`, while this repository freezes `pydantic==2.11.7`. The QeX gate therefore fails closed on that dependency conflict instead of silently upgrading the application stack.

## Current implementation scope

Implemented:

- ComputeRegime and QuantumModality
- CapabilityProfile and frozen ExperimentContract
- canonical QEX-SUBSTRATE-01 contract factory
- Omega/HDB admissibility before preference routing
- classical baseline requirement
- deterministic EvidenceOS-compatible receipts
- backend metadata hashing in execution evidence
- CPU classical execution adapter
- internal ideal statevector adapter
- Qiskit statevector SDK adapter
- controlled-noise adapter
- hardware-snapshot Aer adapter
- TVD divergence metric
- cross-substrate canonical comparator
- dedicated GitHub Actions gate

Not yet claimed:

- current live-device calibration reproduction
- physical QPU execution
- error correction
- quantum advantage
- statistical equivalence from repeated hardware samples
