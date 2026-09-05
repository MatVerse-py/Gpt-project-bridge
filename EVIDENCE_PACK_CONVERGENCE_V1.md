# Evidence Pack Convergence v1

## Goal

Converge the validated local package `Desenvolvimento_de_Testes_e_Benchmarking_2_FIXED.zip` with the canonical `main` runtime without copying historical runtimes that already have stronger canonical equivalents.

Source package SHA-256:

`589dda3c5431175036019ae56f0a08f97d4bfa47a23307c1b6f0894d3162101b`

Local clean-extract verification before import:

`62 passed in 0.15s`

Scope: `LOCAL_ISOLATED_NO_NETWORK_NO_SECRETS`.

## Adjudication

| Pack artifact | Canonical decision | Canonical target |
| --- | --- | --- |
| `drift_engine.py` | **PROMOTE** | `app/drift_engine.py` |
| `runtime_audit.py` | **DO NOT DUPLICATE** | `app/deterministic_lab.py` + `app/physiology.py` + `app/evidence.py` |
| `integrated_cycle.py` | **DO NOT DUPLICATE** | `app/physiology.py` + `app/organism_loop.py` |
| `field_runtime.py` | **DO NOT DUPLICATE** | constitutional core + organism loop + physiology + storage |
| `capsule/verify.py` | **DO NOT DUPLICATE** | `app/reproduction_capsule.py` + EvidenceOS |
| old OpenAI audit | **SUPERSEDED** | governed OpenAI runtime merged by PR #38 |

The convergence rule is therefore:

`new instrument -> promote`

`historical parallel runtime -> map to canonical runtime`

`stale audit -> preserve as provenance, never as current state`

## Imported capability: DRIFT

`app/drift_engine.py` is retained as a distinct measurement/adjudication instrument. It does not become a second physiology, ledger, gate, or runtime. Its role is bounded to distribution divergence, lens readings, two-axis adjudication, viability, sample-size and probe-cost calculations.

## Integrated battery

`tests/test_evidence_pack_convergence.py` closes the new cross-layer path:

`PhysiologyEngine -> GovernedOrganism -> OpenAI provider governance -> mocked Responses API -> observed effect -> durable journal/memory`

The test intentionally uses `httpx.MockTransport`; it verifies architecture and secret boundaries without claiming a live provider execution.

The same battery also verifies that DRIFT remains an instrument rather than a parallel runtime.

## Evidence boundary

The package's local benchmark values are imported as historical measurements only. They are not promoted to current-main performance claims because implementation and environment changed after the package was produced.

Live OpenAI execution remains `HOLD` until `OPENAI_API_KEY` is injected into an actual runtime secret manager/environment and a controlled provider call is observed. No credential is stored in this repository, test fixture, manifest, receipt, or metadata.
