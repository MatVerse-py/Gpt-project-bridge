# QeX Substrate Invariance v1 — L0

Status: L0 integrated test passed in GitHub Actions; physical and provider-backed levels are not included in this change.

## Placement

QeX is a bounded experimentation bench under the existing MatVerse architecture. This PR does not create a new organ, replace the Bridge, or introduce a quantum-first runtime.

## L0 objective

Execute the same frozen experiment identity and input on two independent local implementations:

1. CPU classical NOT;
2. ideal one-qubit statevector NOT (simulation).

For input bit b in {0,1}, both adapters must return 1-b, with normalized observables and matching canonical contract fields.

## Frozen contract

The canonical experiment is `QEX-SUBSTRATE-01`. It fixes the problem hash, capability requirement, metric schema, observable schema and evidence policy. The payload is exactly one integer field, `bit`, with value 0 or 1.

No provider budget or latency ceiling is defined for this L0 contract. Wall-clock duration is recorded as an observation and is not used as a zero-cost estimate or an automatic routing preference. Unknown telemetry remains unknown.

## Integrated path

`validated request -> Bridge CausalEnvelope -> existing HDB/Ω-Gate -> QeX L0 matrix -> TSC comparison -> shared MatVerse append-only ledger`

The selection gate fails closed unless ontology, signature and transition admissibility are explicitly supplied as booleans. The IAQuantumChain service supplies those flags only after request validation, authenticated-principal verification, envelope construction and governance approval.

The TSC receipt binds both canonical observables, the comparison policy and the resulting status. The shared ledger stores the comparison receipt hash.

## Comparison semantics

- `EXACT`: all hard invariants and numeric values match exactly;
- `FUNCTIONALLY_EQUIVALENT`: hard invariants match and no numeric difference was specified;
- `WITHIN_TOLERANCE`: a numeric difference is within the explicitly configured tolerance;
- `DIVERGENT`: a hard invariant differs or a numeric difference exceeds tolerance;
- `INCOMPARABLE`: required fields are missing or numeric values/tolerances are invalid or non-finite;
- `STATISTICALLY_EQUIVALENT`: reserved; not emitted without a repeated-sample statistical test contract.

Zero tolerance is strict. No implicit epsilon expands a caller's tolerance.

## Explicit non-claims

L0 is a functional equivalence experiment, not evidence of quantum advantage, quantum speedup, fault tolerance, error correction, physical quantum execution, hardware attestation or production PQC.

This PR does not include:
- Qiskit/Aer hardware-noise simulation;
- historical hardware snapshots;
- live calibration capture;
- IBM Runtime provider resolution;
- physical QPU submission.

Those levels remain separate HOLD work. They require their own security review for authorization provenance, finite resource budgets, idempotent job submission, provider deadlines, job-ID preservation, calibration freshness and metrics capture before they can be reintroduced.

## Reproduction

```bash
pip install -r requirements.txt
pytest -q tests/test_qex_substrate.py tests/test_qex_adapters.py tests/test_qex_review_regressions.py tests/test_iaquantumchain_l0_integration.py
```
