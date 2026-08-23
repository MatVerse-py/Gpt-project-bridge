# Cross-runtime pilot negative result 001

GitHub Actions run: `32608126923`

Commit under test: `0b8eb9fc82ef674518db526adc7274eeeef8fdac`

## Observed result

The first real cross-runtime pilot did **not** satisfy the reproduction gate.

- `Qwen/Qwen2.5-0.5B-Instruct` loaded and executed successfully and reported `runtime_pass=true`.
- `HuggingFaceTB/SmolLM2-135M-Instruct` loaded and executed successfully but reported `runtime_pass=false`.
- No `REPRODUCTION_PASS` was issued.

This is a model-level invariant failure, not an infrastructure/load failure: the SmolLM2 job completed model execution and wrote a result file before exiting with code `2` because one or more hard observable invariants were not preserved.

## Harness defect discovered

The initial workflow uploaded artifacts only after a successful runtime step. Because the SmolLM2 execution correctly exited non-zero on invariant failure, its JSON evidence was not uploaded. The raw generated text was intentionally not persisted, so the exact failed observable fields cannot be reconstructed from that first pilot.

The workflow is therefore hardened in the next revision to:

1. upload runtime evidence even when invariants fail;
2. run the comparison stage even after a matrix failure;
3. fail closed only after evidence is preserved.

The 135M model is not silently reclassified. This pilot remains a `NEGATIVE_RESULT` in the experiment lineage. A second experiment uses the larger `SmolLM2-360M-Instruct` as the alternate runtime while retaining Qwen as the runtime that passed the pilot.
