# Tesseu Cross-Host Continuity v1

## Objective

Test whether a governed MatVerse Organism can preserve causal identity and constitutional continuity when an authenticated exported state is physically transferred to distinct CI hosts and resumed under different logical runtimes and operating systems.

This experiment extends the existing cross-runtime replay and executor-substitution work. It does not replace either one.

## Experimental topology

```text
Ubuntu seed host
  -> GovernedOrganism
  -> authenticated export_state() capsule
  -> GitHub Actions artifact transfer
        -> Ubuntu restore host / runtime-linux
        -> Windows restore host / runtime-windows
  -> independent continuation event on each host
  -> cross-host semantic comparison
```

The exact same capsule bytes are consumed by both restore arms.

## Why raw state-root equality is not the invariant

`GovernedOrganism.evaluate()` records `runtime_id` in causal lineage and binds the evaluation receipt to that provenance. Therefore a legitimate runtime substitution can produce distinct raw post-execution roots even when the governed causal meaning is equivalent.

Tesseu v1 consequently preserves two layers:

1. **raw provenance** — runtime-specific and expected to differ;
2. **semantic causal projection** — runtime-neutral and required to match.

The semantic projection removes only `runtime_id` and the derived `receipt_hash` from lineage before comparison. Decision, reason, proposal, gate inputs, event order, constraints, organism identity, constitution and gate fingerprint remain part of the invariant surface.

## Hard invariants

Each restore arm must independently preserve:

- authenticated snapshot restoration;
- organism identity;
- constitutional contract hash;
- gate fingerprint;
- inherited constraints;
- seed lineage;
- continuation event append;
- governed PASS decision.

The cross-host comparator additionally requires:

- one transferred snapshot hash;
- one pre-continuation state root;
- one organism identity;
- one constitutional contract;
- one gate fingerprint;
- distinct runtime IDs;
- equal semantic lineage hash;
- equal governed decision and reason;
- all local invariants PASS;
- at least two operating systems.

## Claims boundary

A PASS supports only:

`PARTIAL_PASS_CROSS_HOST_OS`

It demonstrates that the bounded causal state can survive an artifact transfer and deterministic continuation across distinct GitHub-hosted runner jobs using Ubuntu and Windows while preserving the declared semantic invariants.

It does **not** establish:

- independent compute-provider replication;
- cryptographically attested hardware heterogeneity;
- arbitrary CPU/GPU/QPU portability;
- arbitrary model independence;
- unrestricted organism persistence;
- biological life;
- WORLD_REAL completion.

Those remain HOLD unless separately tested.

## Relationship to existing experiments

### Cross-runtime replay v1

Existing cross-runtime replay tests distinct language-model executions under a frozen portable-state contract. Its explicit boundary remains `HOLD_SECOND_INDEPENDENT_PROVIDER_REQUIRED`.

### Executor Substitution v1

Existing executor substitution tests a real `GovernedOrganism` restored from the same authenticated snapshot while executor identity varies.

### Tesseu Cross-Host v1

This experiment adds the missing physical transfer step:

`exported organism state -> artifact transport -> restore on distinct host/OS -> causal continuation`.

Together, the three experiments test different independent variables rather than collapsing them into one claim.

## Promotion path

The next promotion should use exactly the same capsule and comparator while replacing one restore arm with an independently operated compute provider. A later experiment should also use attested heterogeneous architecture, for example x86_64 versus ARM64, before promoting hardware independence.
