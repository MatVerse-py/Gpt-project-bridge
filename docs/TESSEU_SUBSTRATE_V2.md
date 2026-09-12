# Tesseu Substrate Heterogeneity v2

## Objective

Extend Tesseu Cross-Host v1 by changing CPU architecture while preserving the exact frozen authenticated capsule and causal contract.

The first v2 arm pair isolates architecture as the independent variable:

```text
reference capsule from successful Tesseu v1
        -> Ubuntu 24.04 / X64 / GitHub Actions
        -> Ubuntu 24.04 / ARM64 / GitHub Actions
        -> deterministic causal continuation
        -> semantic invariance comparison
```

GitHub documents `ubuntu-24.04-arm` as a hosted ARM64 runner. The experiment records both the provider-reported runner architecture and the machine architecture observed by Python.

## Frozen reference object

`evidence/tesseu/reference_capsule_v1.json` is the exact capsule produced by the successful Tesseu Cross-Host v1 run that established `PARTIAL_PASS_CROSS_HOST_OS`.

The v2 experiment does not regenerate or modify the state before restoration. Both architecture arms consume the same reference bytes.

## Required semantic invariants

- same transferred snapshot;
- same pre-continuation state root;
- same organism identity;
- same constitutional contract;
- same gate fingerprint;
- distinct runtime IDs;
- same semantic lineage;
- same governed PASS decision;
- same decision reason;
- all local restoration invariants PASS.

Architecture promotion additionally requires:

- at least two normalized architectures;
- no UNKNOWN architecture;
- consistency between provider-reported architecture and OS-observed machine architecture.

## Claims boundary

A successful X64/ARM64 run promotes only:

`PASS_PROVIDER_REPORTED_ARCH`

and:

`PARTIAL_PASS_CROSS_ARCH_NOT_CRYPTOGRAPHICALLY_ATTESTED`

It does not claim cryptographic hardware attestation, arbitrary hardware portability, GPU/QPU independence, or independent-provider reproduction.

## Independent-provider extension

The same script can run on another compute provider. A provider is accepted only when its declared identity agrees with environment evidence known to the runner. A label alone is insufficient.

The comparator can therefore combine a GitHub result with a Replit result without changing the frozen causal invariants. Only then can `external_provider_status` move from HOLD to an environment-evidenced multi-provider PASS.
