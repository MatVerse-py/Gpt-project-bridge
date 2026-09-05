# PILOT_NEGATIVE_RESULT_CROSS_SUBSTRATE_001

## Status

**NEGATIVE RESULT — preserved, not repaired in place.**

Experiment: `PATENTE-GCI-CROSS-SUBSTRATE-2026-09-04-V1`  
Preregistration commit: `8d3c6d6bd26287974e83e9b3f07348e929087bc2`  
Preregistration SHA-256: `7b496c5a8bf5750297d7c14083e965372e517fc867c244d784ec929a4213cabf`  
Implementation head: `4022c128716cf070ff7a7efd0041458cacc194fa`  
Pull request: `#34`  
GitHub Actions run: `33934490735`  
Run conclusion: `failure`

## Frozen question

Whether a rejection-derived causal constraint, independently adjudicated and promoted outside the model, remains enforceable after the origin context is absent and a different model substrate produces the later candidate.

## What executed

The preregistration hash check passed before model execution in all reached model jobs. Real Hugging Face model runtimes executed on separate GitHub-hosted runners.

### SmolLM2-360M-Instruct

- Origin arm in `SMOL_TO_QWEN`: `runtime_pass=true`
  - result SHA-256: `6d6bcdf628daa74606673bc1d8e7506d9d2ff78db7bec59fe6b15f5e59e090c7`
  - artifact ID: `9959700823`
  - uploaded artifact ZIP SHA-256: `0469bfa7a506678fe6aec4d2cdc201895660be3487e7a2f229369fc13f6efd6f`
- Context-flushed target arm in `QWEN_TO_SMOL`: `runtime_pass=true`
  - result SHA-256: `a0dbb51bc37bee4332ebfb42c4b41722a0e3ee4e49c63a572efb4cb4c61cb8f4`
  - artifact ID: `9959702739`
  - uploaded artifact ZIP SHA-256: `1d393c71138301723db1e51deeccaf0e49b423b11c5c1549b5fd36ea7e0516dd`

### Qwen2.5-0.5B-Instruct

Qwen failed the frozen machine-readable candidate contract in both roles.

- Origin arm in `QWEN_TO_SMOL`: `runtime_pass=false`
  - result SHA-256: `9a22fa88af2e36c790a7852c63d3e8bfee27980cef4110ced2b4dcc45801113b`
  - model revision: `7ae557604adf67be50417f59c2c2f167def9a775`
  - `parse_ok=false`
  - artifact ID: `9959703377`
  - uploaded artifact ZIP SHA-256: `62998b80c59f827b7bf8aac8fbbbd3fb9a650a785802b614f9e1de1393f6dcf3`
- Context-flushed target arm in `SMOL_TO_QWEN`: `runtime_pass=false`
  - result SHA-256: `47d8abaafd240691b00a62160a0e4305ac8d8ce9f0abdea2864fc044caf80a9b`
  - model revision: `7ae557604adf67be50417f59c2c2f167def9a775`
  - `parse_ok=false`
  - artifact ID: `9959704010`
  - uploaded artifact ZIP SHA-256: `ff981dbe5b79b109f89ca0569df317da4433c014f10ba2eeed67af6da5823082`

The Qwen raw output itself was intentionally not persisted; only its SHA-256 was retained by the runtime. In both Qwen roles the raw-output SHA-256 was `061ca6ee9b6e44908defe4fc754122d0957a8ee35b527115f9f9fd261a88e914`, showing deterministic recurrence of the same non-parseable observable output under the frozen prompt/runtime.

## Adjudication

`MODEL_CONTRACT_INTEROPERABILITY = PARTIAL`  
`SMOL_RUNTIME_CONTRACT = PASS`  
`QWEN_RUNTIME_CONTRACT = FAIL`  
`INDEPENDENT_PROMOTION = NOT_ASSESSED_IN_THIS_RUN`  
`CONTEXT_FLUSH_CAUSAL_EFFECT = NOT_ASSESSED_IN_THIS_RUN`  
`CROSS_MODEL_GOVERNANCE_PERSISTENCE = NOT_ASSESSED_IN_THIS_RUN`  
`PATENTABILITY = HOLD`  
`INDEPENDENT_PROVIDER_REPRODUCTION = HOLD`

The causal experiment did **not** reach governance adjudication/evaluation for both preregistered sequences because one model failed the frozen interface contract first. Therefore the result is **not** evidence that causal inheritance failed. It is evidence that the v1 candidate-output interface was not model-neutral across the two frozen model substrates.

## Negative-result rule

No parser loosening, prompt change, model substitution, threshold change, or selective retry is applied to v1. Any follow-up must use a new experiment identifier and a separately frozen preregistration.

## Design implication

A valid follow-up should separate:

1. **model-neutral observable-state portability**, using an interface already demonstrated to pass across both model substrates; from
2. **causal governance**, which operates on normalized externally governed state after model execution.

This keeps the negative result intact instead of fitting the interface post hoc to the failed model.
