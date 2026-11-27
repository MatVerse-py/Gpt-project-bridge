# QeX Computational Substrate Invariance v1

Status: IMPLEMENTED_AND_OBSERVED_THROUGH_L2 / L3_AND_L4_BOUNDARIES_IMPLEMENTED_HOLD_NO_LIVE_QPU

## Canonical placement

QeX remains a specialized quantum-classical experimentation bench under URANO. This change does not create a new MatVerse organ or a quantum-first runtime.

## Principle

`Experiment Identity != Computational Substrate`

The same frozen experiment contract may be evaluated against classical, hybrid, gate-model quantum, annealing, controlled-noise, hardware-derived noise, live-calibration replay, and physical-QPU execution substrates. Backend choice is capability-based and occurs only after constitutional admissibility.

## Flow

`ProblemSpec -> ExperimentContract -> CapabilityProfile[] -> Omega/HDB admissibility -> hard capability filtering -> preference routing -> execution adapter -> canonical observable result -> substrate comparison -> EvidenceOS receipt`

## QEX-SUBSTRATE-01

The benchmark freezes experiment identity, problem hash, objective, metric schema, observable schema, evidence policy, required capabilities, and budget/latency limits. The canonical contract is materialized by `app/qex_experiment.py`. Derived adjudication metrics such as total variation distance (TVD) remain outside the contract hash.

Comparison states are `EXACT`, `FUNCTIONALLY_EQUIVALENT`, `WITHIN_TOLERANCE`, `STATISTICALLY_EQUIVALENT` (reserved for a future repeated-sample statistical-test contract), `DIVERGENT`, and `INCOMPARABLE`. `WITHIN_TOLERANCE` means numerical tolerance only.

## Noise and execution realism ladder

### L0 — ideal

CPU, internal statevector, and Qiskit statevector establish substrate-independent execution under an identical frozen contract.

### L1 — controlled synthetic noise

A controlled bit-flip/readout proxy perturbs the outcome distribution and TVD is measured explicitly. This validates tolerance/divergence semantics without a hardware claim.

### L2 — hardware-derived historical snapshot

`AerHardwareSnapshotNotAdapter` builds a Qiskit Aer noise model from the bundled `FakeSherbrooke` historical hardware snapshot. Finite shots, deterministic seeds, backend identity, SDK versions, and noise basis gates are bound into execution evidence. It remains simulation, not current calibration and not physical execution.

### L3 — current calibration replay

`prepare_live_calibration(backend)` requires a non-simulator, non-fake, operational backend and refreshes dynamic calibration data through `backend.properties(refresh=True)`. The normalized `BackendProperties.to_dict()` representation, backend identity, and optional `calibration_id` are frozen into `calibration_snapshot_hash`. The simulator binding occurs in the same preparation step so later calibration drift cannot silently alter the recorded replay substrate.

Missing authentication/backend, fake/simulator sources, unavailable/non-operational status, absent properties, refresh failure, or Aer binding failure returns `Decision.HOLD`. A PASS preparation may execute locally through `LiveCalibrationAerAdapter`; this is still local Aer replay, not physical QPU execution.

Current epistemic state:

- L3 architecture: IMPLEMENTED
- L3 fail-closed behavior: PASS_OBSERVED
- L3 dependency-injected local replay contract: PASS_OBSERVED
- authenticated live-device calibration capture: HOLD / NOT_OBSERVED

### L4 — physical QPU execution

`prepare_physical_qpu(backend, authorization)` is the physical execution admissibility boundary. It requires:

- explicit `PhysicalExecutionAuthorization`
- named authority and purpose
- positive maximum shot budget
- explicit permission for resource consumption
- authenticated non-fake, non-simulator backend
- operational backend status
- refreshed pre-execution calibration properties

The preparation step records backend identity, queue depth when available, `calibration_id`, `properties_last_update`, `calibration_snapshot_hash`, `authorization_hash`, and the authorized maximum shot count. PASS means only that an L4 attempt is admissible. It does not mean a physical job was submitted or observed.

`PhysicalQPUAdapter` refuses HOLD preparations and refuses requested shots above the authorized maximum. For the default IBM path it:

1. builds the frozen QEX-SUBSTRATE-01 circuit;
2. compiles it into an ISA circuit with `generate_preset_pass_manager(backend=..., optimization_level=1)`;
3. hashes the serialized ISA circuit through QPY;
4. submits through `SamplerV2(mode=backend)` in job mode;
5. records the provider `job_id`;
6. obtains raw counts from `result[0].data.meas.get_counts()`;
7. validates that counts contain only the expected one-bit outcomes and sum exactly to requested shots;
8. records raw counts, raw-count hash, job metrics/usage hashes, calibration/authorization hashes, backend identity, and SDK versions into execution evidence.

Any submission/result exception or malformed counts fails closed through `PhysicalQPUExecutionFailed`; there is no fallback to simulator execution.

CI uses dependency-injected compiler and Sampler/job doubles. Therefore it can prove L4 authorization, validation, evidence-binding, and fail-closed semantics without consuming QPU resources. It cannot prove a physical job actually ran.

Current epistemic state:

- L4 architecture: IMPLEMENTED
- L4 explicit resource authorization: PASS_OBSERVED
- L4 calibration/authorization evidence binding: PASS_OBSERVED
- L4 dependency-injected submission/result contract: PASS_OBSERVED
- authenticated physical QPU job: HOLD / NOT_OBSERVED
- physical job ID from a real provider: NOT_OBSERVED

## Governed IBM Runtime entrypoint

`app/qex_ibm_runtime_entrypoint.py` is the operational boundary for a real provider account. It resolves either a named backend through `QiskitRuntimeService.backend(...)` or the least-busy operational non-simulator backend through `least_busy(min_num_qubits=1, operational=True, simulator=False)`.

Credential handling is deliberately external to the repository:

- an already saved Qiskit Runtime account may be used;
- `IBM_QUANTUM_API_KEY` may be supplied in the process environment;
- `IBM_QUANTUM_INSTANCE`, `IBM_QUANTUM_BACKEND`, and `IBM_QUANTUM_CHANNEL` may optionally select instance/backend/channel;
- no token is accepted as a CLI flag and no token is included in public output/evidence.

The default channel is `ibm_quantum_platform`, matching the pinned Runtime 0.47 API. A real resource-consuming execution requires two independent operator signals: `--execute` and `--allow-resource-consumption`. Omitting either leaves the run in HOLD and no sampler job is submitted.

Example invocation after authentication is configured outside the repository:

```bash
python -m app.qex_ibm_runtime_entrypoint \
  --authority MATVERSE_OPERATOR \
  --purpose "QEX-SUBSTRATE-01 governed physical validation" \
  --backend <physical-backend-name> \
  --shots 128 \
  --bit 0 \
  --execute \
  --allow-resource-consumption
```

A PASS at `PHYSICAL_RESULT_OBSERVED` is allowed only after the adapter receives a provider result and records `job_id`, raw counts, calibration snapshot hash, ISA-circuit hash, authorization hash, receipt hash, and usage/job-metric hashes. Before that point the physical claim remains HOLD / NOT_OBSERVED.

## Scientific boundary

`QUANTUM_USED` never implies `QUANTUM_ADVANTAGE`.

A classical baseline is required by default. The Qiskit statevector path remains ideal simulation; L1 is synthetic noise; L2 is a historical hardware-derived simulation; L3 can replay a current calibration locally when an authenticated backend is supplied; L4 is the only layer allowed to assert physical execution, and only after a real provider job ID and physical result are observed and recorded.

No layer in this PR establishes fault tolerance, error correction, or quantum advantage. `STATISTICALLY_EQUIVALENT` remains reserved until a repeated-sample statistical test contract exists.

## Pinned SDK stack

The QeX hardware-realism gates use the repository-compatible stack:

- qiskit 2.5.1
- qiskit-aer 0.17.2
- qiskit-ibm-runtime 0.47.0

The newer Runtime client line currently conflicts with the repository's frozen Pydantic dependency, so QeX keeps the compatible pinned stack rather than silently upgrading the application.

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
- live-calibration preparation and local replay adapter
- physical-QPU preparation boundary
- explicit physical resource authorization object
- physical SamplerV2 adapter with ISA-circuit hashing
- raw-count/job/usage evidence binding
- governed IBM Runtime backend resolver/CLI entrypoint
- dual physical-consumption gate (`--execute` + `--allow-resource-consumption`)
- token exclusion from CLI/public output
- TVD divergence metric
- cross-substrate canonical comparator
- dedicated GitHub Actions gate

Not yet claimed:

- observed authenticated live-device calibration capture
- observed physical QPU execution
- real provider job ID in corpus evidence
- error correction
- quantum advantage
- statistical equivalence from repeated hardware samples
