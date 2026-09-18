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
TTL, expiration, signature and evidence references. Its identifier is a
deterministic hash of the causal content, including protocol version, canonical
UTC timestamp and TTL. An authority reference is mandatory but is not created by
the Bridge: the envelope records a Gate decision instead of impersonating one.

Payload content is detached from caller-owned mutable structures and stored as a
recursive immutable snapshot. `as_dict()` materializes a JSON-compatible copy,
so post-build caller mutation cannot silently change transported content under
an unchanged `envelope_id`.

A positive TTL alone is not treated as proof that an envelope is currently
traversable. `assert_envelope_fresh()` is the fail-closed traversal-boundary
check: adapters or transports must call it immediately before crossing a
boundary. The envelope also carries a deterministic `expires_at` derived from
its canonical timestamp and TTL.

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
diff, engineering evidence, failure and timestamps. It deliberately does not
scrape `chatgpt.com/codex/cloud`; a UI is not a stable protocol.

Explicit `null` values for optional collection metadata normalize to empty
collections. Required task identity fields fail closed unless they are non-empty
strings.

### Engineering promotion is not task status

The adapter preserves the following states independently:

```text
workspace != commit != push != pull request != CI != merge != runtime
```

An open task and a workspace diff demonstrate only workspace activity. They do
not demonstrate a commit, push, pull request, passing CI, merge into the
canonical repository, or live Bridge traversal. `assess_engineering_transition`
reports every evidence item as `PRESENT`, `FAILED`, or `UNKNOWN`, advances
only through an unbroken sequence, and reports canonical integration and runtime
traversal separately.

The Codex task `task_e_6aac90fdeed8832699998eb648a87ff7` initially surfaced
as an open workspace change. Its GitHub branch and pull request are separate
engineering evidence and must be assessed from GitHub rather than inferred from
the task UI. Runtime traversal remains a distinct later state even after merge.

## Attention and relevance boundary

Bridge preserves context and causal lineage but does not decide analytical
priority. ASTRA may mark information as `NOT_RELEVANT_TO_CURRENT_OBJECTIVE`
without declaring it `IRRELEVANT_TO_SYSTEM`. Runtime risk and authority blocks
come first, followed by runtime/document contradictions, missing objective
evidence, unproven relations, and only then frontier hypotheses.

No fixed attention percentages, empirical success rates, Monte Carlo outcomes,
or mathematical optimum are claimed by this specification.

## Promotion boundary

Implemented and tested in the Bridge Core branch:

* deterministic causal envelopes with explicit governance references;
* independent epistemic and analytic classifications;
* required routing-field validation and positive integer TTL;
* canonical timezone-aware timestamps and TTL-bound envelope identity;
* recursive immutable payload snapshots;
* explicit expiration materialization plus fail-closed freshness check;
* normalization of Codex operational task metadata and localized statuses;
* null-safe optional task metadata handling;
* explicit, evidence-bound workspace-to-runtime promotion assessment.

Not claimed by this specification alone:

* a public Codex Cloud task-enumeration API;
* UI scraping, credential brokerage or live provider invocation;
* Gate authorization, task execution, causal inference or system learning;
* merge into the canonical branch before GitHub evidence shows it;
* runtime traversal before a cross-domain transport actually runs;
* empirical proof of autonomous evolution.
