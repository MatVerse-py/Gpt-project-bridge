# QeX Computational Substrate Invariance v1

Status: IMPLEMENTED_AND_OBSERVED_THROUGH_L2 / L3_IMPLEMENTED_HOLD_NO_LIVE_BACKEND

## Canonical placement

QeX remains a specialized quantum-classical experimentation bench under URANO. This change does not create a new MatVerse organ or a quantum-first runtime.

## Principle

`Experiment Identity != Computational Substrate`

The same frozen experiment contract may be evaluated against classical, hybrid, gate-model quantum, annealing, controlled-noise, hardware-derived noise, and live-calibration replay substrates. Backend choice is capability-based and occurs only after constitutional admissibility.

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
- fail-closed live-calibration Aer replay adapter

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

`AerHardwareSnapshotNotAdapter` constructs a Qiskit Aer noise model from Qiskit Runtime's `FakeSherbrooke` backend snapshot. Execution uses finite shots and fixed simulator/transpiler seeds. The evidence receipt binds backend metadata, package versions, seed, shot count, source backend identity, and noise-model basis gates into `backend_metadata_hash`.

This level is materially stronger than a scalar synthetic error model because the noise model is derived from a hardware snapshot. It remains a historical/mock snapshot bundled with the pinned runtime package. It is **not** current device calibration and **not** execution on a physical QPU.

### L3 — current calibration replay

`prepare_live_calibration(backend)` is the L3 admissibility boundary. It requires a non-simulator, non-fake, operational backend and explicitly refreshes dynamic calibration data through `backend.properties(refresh=True)`.

The refreshed `BackendProperties.to_dict()` representation is normalized and hashed together with backend identity and optional `calibration_id`. The resulting `calibration_snapshot_hash` becomes evidence metadata and the local Aer simulator is bound immediately during the same preparation step. This prevents a later calibration refresh from silently changing the simulated substrate after the snapshot identity was recorded.

If no authenticated backend is supplied, the backend is fake/simulated, status is unavailable/non-operational, properties cannot be refreshed, or Aer cannot bind the snapshot, preparation returns `Decision.HOLD`. `LiveCalibrationAerAdapter` refuses to execute from a HOLD preparation.

When preparation is PASS, `LiveCalibrationAerAdapter` executes **locally in Aer**, not on the QPU, with finite shots and deterministic seeds. Evidence includes backend name, calibration id, calibration snapshot hash, properties last-update timestamp, SDK versions, shots, and seed.

CI covers the fail-closed path and a dependency-injected local replay path. This validates L3 architecture and evidence semantics without pretending that CI possesses authenticated live QPU credentials. Therefore the current epistemic state is:

- L3 architecture: IMPLEMENTED
- L3 fail-closed behavior: PASS_OBSERVED
- L3 deterministic local replay contract: PASS_OBSERVED when supplied an admissible test preparation
- authenticated live-device calibration capture: HOLD / NOT_OBSERVED

### L4 — physical QPU execution (future gate)

The same frozen QEX-SUBSTRATE-01 contract is submitted to a physical backend. Hardware execution must preserve experiment identity while recording queue, shots, backend, compilation, calibration context, and raw measurement evidence.

## Scientific boundary

`QUANTUM_USED` never implies `QUANTUM_ADVANTAGE`.

A classical baseline is required by default. A quantum backend may be selected only when it is admissible and its declared, instrumented capability profile justifies the choice. Unknown or missing required measurements fail closed when the experiment contract depends on them.

The Qiskit adapter proves execution through an independent external SDK, but remains ideal simulation. The controlled-noise adapter proves tolerance/divergence handling, but remains synthetic. The FakeSherbrooke/Aer path exercises a hardware-derived historical snapshot, but remains simulation. The L3 adapter can bind a current calibration to a local simulator, but until an authenticated real backend is actually supplied and observed it remains HOLD for the live-capture claim. None establishes physical QPU execution, fault tolerance, or quantum advantage.

Topological hardware remains treated as experimental/contested unless the registered capability profile explicitly carries a verified/production maturity state.

## Pinned SDK stack

The hardware-snapshot and L3 gates intentionally use a stack compatible with the repository's frozen application dependencies:

- qiskit 2.5.1
- qiskit-aer 0.17.2
- qiskit-ibm-runtime 0.47.0

`qiskit-ibm-runtime 0.49.0` requires a newer Pydantic line than this repository freezes. QeX therefore fails closed on that dependency conflict instead of silently upgrading the application stack.

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
- live-calibration preparation gate
- live-calibration local Aer replay adapter
- calibration snapshot hashing
- TVD divergence metric
- cross-substrate canonical comparator
- dedicated GitHub Actions gate

Not yet claimed:

- observed authenticated live-device calibration capture
- physical QPU execution
- error correction
- quantum advantage
- statistical equivalence from repeated hardware samples
