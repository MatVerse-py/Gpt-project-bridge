# MatVerse Cross-Runtime Reproduction Protocol v1

## Purpose

This protocol advances the canonical Bridge from `IMPLEMENTATION_PASS` to a scoped runtime-level reproduction test without weakening the evidence boundary.

Two distinct open model executions receive the same frozen portable-state contract. They are evaluated only on declared observable invariants. Hidden reasoning is neither requested nor persisted.

## Frozen invariants

- `decision = PASS`
- `safety_gate = PASS`
- `claims = {C1,C2,C3}`
- `transfer_hidden_reasoning = false`

The prompt, contract and model output are hashed. Only the parsed observable state and the output hash are retained as evidence; raw generated text is intentionally not persisted.

## Promotion semantics

`REPRODUCTION_PASS` requires:

1. two or more distinct model identities;
2. identical frozen `contract_hash`;
3. every hard invariant passing independently;
4. equality of the hard-invariant vector across runtimes.

This first experiment executes on separate GitHub-hosted jobs, so it is external compute relative to the local development environment but **not provider-independent reproduction**.

Therefore:

- successful matrix execution may promote `CROSS_RUNTIME_REPLAY -> REPRODUCTION_PASS`;
- `EXTERNAL_PASS` remains `HOLD_SECOND_INDEPENDENT_PROVIDER_REQUIRED`;
- `WORLD_REAL_PASS` remains HOLD until the public deployment and external-provider gates are satisfied.

## External-provider next gate

A provider-independent run must execute the same contract through a separately operated API/runtime (for example OpenAI API plus another provider or a separately administered inference service) and return a signed evidence result that can be compared against this baseline.
