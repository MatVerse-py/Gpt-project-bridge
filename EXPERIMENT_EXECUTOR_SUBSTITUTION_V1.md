# Executor Substitution Experiment v1

## Purpose

Test whether a MatVerse Organism preserves its governed identity while the executor is replaced.

The executor is the independent variable. The Organism snapshot, constitutional binding, proposal, physiological policy, telemetry plan and initial cycle sequence are held constant.

## Hypothesis

**H1 — Organismic invariance under executor substitution**

For two distinct executors `E_A != E_B`, a correctly implemented Organism should preserve:

- organism identity;
- constitutional contract;
- gate fingerprint;
- inherited constraints;
- equivalent governed lineage for the same proposal;
- equivalent post-decision organism state root.

Executor performance and effects may differ.

Formally, the experiment allows:

`Performance(O_A) != Performance(O_B)`

while requiring:

`Identity(O_A) = Identity(O_B)`

`Constitution(O_A) = Constitution(O_B)`

`Constraints(O_A) = Constraints(O_B)`

`Lineage(O_A) = Lineage(O_B)`

for the paired governed evaluation.

## Canonical path under test

`Frozen snapshot -> PhysiologyEngine -> GovernedOrganism -> executor -> observed effect -> DurableEventJournal -> MEMORY_COMMIT`

The same authenticated snapshot is restored separately for every arm. The physiology uses deterministic telemetry so executor identity is the intended varying factor.

## Runtime implementation

- `app/executor_substitution.py` — model-neutral paired harness.
- `tests/test_executor_substitution.py` — offline deterministic invariance tests.
- `experiments/executor_substitution_v1.py` — live OpenAI runner using the existing governed Responses runtime.
- `.github/workflows/executor-substitution-v1.yml` — offline CI plus opt-in live workflow dispatch.

## Live OpenAI execution

The live runner requires process-environment configuration:

- `OPENAI_API_KEY`
- `MATVERSE_EXECUTOR_A_MODEL`
- `MATVERSE_EXECUTOR_B_MODEL`

The two model identifiers must differ. The harness intentionally does not guess or silently select model aliases.

The live task is deliberately bounded: both executors must return one exact token. This first experiment tests substitution mechanics and invariant preservation, not broad model intelligence.

Raw provider output is not persisted in the experiment report. The physiological effect contains validation state, output hash, request/response hashes, usage metadata and provider identifiers.

## Evidence classes

### What a PASS proves

A PASS demonstrates, for this bounded task and implementation:

1. both distinct executors completed the same governed task;
2. both began from the same authenticated Organism snapshot;
3. the listed constitutional and state invariants were preserved across substitution;
4. each arm closed a physiological cycle into durable local memory;
5. the resulting experiment receipt commits to the snapshot, task, executor identities and minimized results.

### What a PASS does not prove

It does not establish:

- general intelligence equivalence;
- biological life;
- unrestricted autonomous safety;
- distributed exactly-once execution;
- independent external scientific replication;
- superiority of one model from a single bounded task.

## Scaling path

After v1 closes, replace the single exact-token task with a preregistered paired battery. Recommended metrics include task success, actions, elapsed time, token usage, intervention count, violations, rollback success and final integrity. Statistical comparison should only be promoted once the battery size and adjudication rules are frozen in advance.
