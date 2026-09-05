# Live Executor Substitution Runbook

## Purpose
Run the bounded live OpenAI executor-substitution experiment with two different OpenAI models against the same governed Organism snapshot.

Default pair:
- Arm A: `gpt-5.6-sol`
- Arm B: `gpt-6-astra`

## Secret boundary
The OpenAI API key must exist only as the GitHub Actions repository secret named `OPENAI_API_KEY`.

Do not commit the key, place it in workflow inputs, issue comments, pull-request text, artifacts, receipts, or chat.

## One-time setup
In GitHub, open the repository settings and create an Actions repository secret named exactly:

`OPENAI_API_KEY`

Use the OpenAI API key created for the MATVERSE project.

## Run
Open Actions -> `Executor substitution v1` -> `Run workflow`.

Set:
- `live`: true
- `model_a`: `gpt-5.6-sol`
- `model_b`: `gpt-6-astra`

The workflow fails closed if the secret is missing, either model id is empty, or the two model ids are equal.

## Evidence output
A successful live run uploads `executor-substitution-v1-report` containing minimized experiment evidence. Raw provider output and the API key are not intended to be persisted in the report.

## Interpretation boundary
A PASS demonstrates the bounded substitution mechanism and preservation of the declared Organism invariants under two real OpenAI executors for this task. It does not by itself establish general superiority of one model, biological life, consciousness, or broad scientific replication.
