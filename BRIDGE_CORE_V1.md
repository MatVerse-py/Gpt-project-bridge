# MatVerse Bridge Core v1

## Canonical responsibility

Bridge is the governed traversal mechanism between heterogeneous domains. It
transports context, capabilities and effects without absorbing ownership,
cognition, authorization or execution.

```text
Mesh defines a path -> Gate authorizes -> Bridge traverses -> Executor acts
```

Accordingly, `Authorization != Connection != Execution`. Atlas maps state,
Cassandra interprets it, ASTRA prioritizes the objective, Gate authorizes a
transition, Bridge transports it, and Evidence/Atlas records the consequence.

## Causal envelope

`app.bridge_core.CausalEnvelope` is the provider-neutral traversal unit. It
contains source and destination, actor, objective, capability, payload schema,
context, authority and policy references, correlation and causation identifiers,
TTL, signature and evidence references. Its identifier is a deterministic hash
of the causal content. An authority reference is mandatory but is not created by
the Bridge: the envelope records a Gate decision instead of impersonating one.

Every envelope also has two independent classifications:

* epistemic nature: `ARTEFATO`, `RUNTIME`, `INFERENCIA`, or `MODELO`;
* analytic status: `FATO`, `CITACAO`, `INFERENCIA`, `HOLD`, or `CONTRADICAO`.

This prevents a proposed model from being silently promoted to an observed
runtime fact.

## Adapter family

Adapters translate domain-specific state into the common envelope contract.
Expected profiles include project/corpus, model, workspace, repository,
runtime, technology, network, device, human, GitHub and Codex Cloud. They are
profiles of one Bridge Core, not independent architectural authorities.

The initial Codex task normalizer accepts exported or API/CLI-derived metadata:
task, repository, branch/worktree, objective, execution status, changed files,
diff, commit/PR references, failure, timestamps and evidence. It deliberately
does not scrape `chatgpt.com/codex/cloud`; a UI is not a stable protocol.

### Engineering promotion is not task status

The adapter preserves the following states independently:

```text
workspace != commit != push != pull request != CI != merge != runtime
```

An open task and a workspace diff demonstrate only workspace activity. They do
not demonstrate a commit, push, pull request, passing CI, merge into the
canonical repository, or live Bridge traversal. `assess_engineering_transition`
reports every evidence item as `PRESENT`, `FAILED`, or `UNKNOWN`, advances only
through an unbroken sequence, and reports canonical integration and runtime
traversal separately.

For the observed task `task_e_6aac90fdeed8832699998eb648a87ff7`, an `OPEN`
status and `+338/-0` diff support `highest_demonstrated_state=WORKSPACE`; absent
additional receipts, both canonical integration and runtime traversal remain
`HOLD`.

## Attention and relevance boundary

Bridge preserves context and causal lineage but does not decide analytical
priority. ASTRA may mark information as `NOT_RELEVANT_TO_CURRENT_OBJECTIVE`
without declaring it `IRRELEVANT_TO_SYSTEM`. Runtime risk and authority blocks
come first, followed by runtime/document contradictions, missing objective
evidence, unproven relations, and only then frontier hypotheses.

No fixed attention percentages, empirical success rates, Monte Carlo outcomes,
or mathematical optimum are claimed by this specification.

## Promotion boundary

Implemented and tested:

* deterministic causal envelopes with explicit governance references;
* independent epistemic and analytic classifications;
* validation of required routing fields and positive TTL;
* normalization of Codex operational task metadata and localized statuses.
* explicit, evidence-bound workspace-to-runtime promotion assessment.

Not claimed:

* a public Codex Cloud task-enumeration API;
* UI scraping, credential brokerage or live provider invocation;
* Gate authorization, task execution, causal inference or system learning;
* empirical proof of autonomous evolution.
