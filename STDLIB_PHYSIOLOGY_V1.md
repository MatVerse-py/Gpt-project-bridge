# MatVerse Stdlib Physiology v1

Status: implementation candidate on `closure/stdlib-physiology-v1`.

## Objective

Close the runtime/physiology/reproducibility gaps without introducing mandatory infrastructure dependencies.

The canonical organism remains sovereign. External brokers, telemetry stacks, workflow engines, chaos systems, and reproducibility platforms may be added later through adapters, but are not required for liveness, persistence, replay, homeostasis, deterministic testing, or reproduction packaging.

## Components

### `app/physiology.py`

- SQLite/WAL append-only event journal.
- Idempotent event IDs.
- Durable monotonic consumer offsets.
- Runtime state persistence.
- Native telemetry using Python stdlib instruments only.
- Homeostatic states: `NORMAL`, `DEGRADED`, `CRITICAL`, `SAFE_MODE`.
- Hysteresis for safe-mode exit.
- MAPE-K-inspired cycle with an explicit MatVerse constitutional authorization stage:

```text
Sense
  -> Analyze
  -> Plan
  -> Authorize (GovernedOrganism / HDB / Omega)
  -> Execute
  -> ObserveEffect
  -> Remember
```

`Authorize` is deliberately separate from `Analyze`, `Plan`, and `Execute`.

### `app/deterministic_lab.py`

- Seeded deterministic fault plans.
- Explicit simulated telemetry labels.
- Deterministic executor fault injection.
- Stable plan receipts for exact replay.

Simulation output must remain classified as simulated; the module never presents synthetic telemetry as world-real observation.

### `app/reproduction_capsule.py`

- Deterministic uncompressed TAR capsule.
- Explicit allow-list of files.
- SHA-256 manifest.
- Normalized archive metadata.
- Path traversal and symlink rejection.
- Independent verifier.

No environment or secret is captured implicitly.

## Dependency boundary

No new package is required by these components. They rely on Python stdlib plus existing MatVerse modules.

The following remain optional adapters, not constitutional dependencies:

- NATS / Kafka / Redis streams;
- OpenTelemetry collectors/exporters;
- Temporal / Dapr;
- Chaos Mesh;
- Hypothesis;
- ReproZip.

## Evidence classes

- `NativeTelemetry`: observed local runtime instrumentation.
- `DeterministicTelemetry`: simulated instrumentation.
- `DurableEventJournal`: local durable evidence transport.
- `ReproductionCapsule`: packaging/replay instrument; it does not itself grant `EXTERNAL_PASS`.

## Fail-closed boundaries

- A PASS decision without an explicit executor raises and does not silently execute.
- `SAFE_MODE` converts external proposals to `HOLD`.
- Journal event ID collisions with different content are rejected.
- Consumer offsets cannot move backward.
- Capsules reject path escape and symlinks.
- Missing platform telemetry remains unavailable rather than fabricated.

## Promotion gates

`IMPLEMENTATION_PASS` requires the repository test suite to pass on the PR.

Further gates remain separate:

- deterministic long-run replay;
- restart/persistence evidence;
- real fault injection;
- independent-domain reproduction;
- world-real workload;
- scientific claim validation.
