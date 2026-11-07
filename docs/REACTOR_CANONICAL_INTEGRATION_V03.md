# MatVerse Motor/Reactor -> Canonical Runtime (v0.3)

Status: assessment-only implementation / no automatic physical or external execution.

## Architecture and source lineage

Recovered families: FECHO/MSCI (domain + skill + contract + invariant + pipeline);
motores/reatores/transdutores/dissipadores/difusores (corpus PEPE/Umjamss);
PyExperimenter v1-v4 experimental stress and paired metrics.

This implementation supplies one computational vertical slice; it is not a
re-implementation of the Omega-Gate, SymbiOS, EvidenceOS or organism physiology.

The original source package is MatVerse Motor & Reactor Lab v0.2.0.
Only its deterministic graph core is included here. The Apple/iTunes adapter,
research simulator and local SQLite lab ledger remain independently distributed:
there is no transfer of device files or private corpus to this public repository.

    graph specification + explicit numeric input
        -> ReactorGraph (typed deterministic evaluation)
        -> independent replay of graph and result
        -> app.core.evaluate_hdb() and app.core.omega_gate() (unsigned = BLOCK)
        -> app.evidence.evidence_receipt() (hash of result/inputs)
        -> optional app.physiology.DurableEventJournal.append()
        -> HOLD external action; no executor

## Python API

    from app.reactor_bridge import assess_reactor, verify_reactor_result
    from app.physiology import DurableEventJournal

    graph = {
      "schema": "matverse.reactor-graph.v1",
      "nodes": [
        {"node_id": "in", "kind": "TRANSDUCER", "params": {"field": "signal"}},
        {"node_id": "reactor", "kind": "REACTOR", "source": "in",
         "params": {"feedback": 0.25}}
      ]
    }
    preview = assess_reactor(graph_spec=graph, inputs={"signal": 0.75})

For optional journal persistence, pass an explicit DurableEventJournal instance.
The adapter records commitments and decision metadata, never the raw input
value or user human context in the journal payload.

## Boundary conditions

- A graph result has no authority to execute an external action.
- The adapter never sets privileged signature_valid=True, never calls an
  executor, and never mutates organism constraints, capabilities or state.
- A correct result is HOLD for external promotion. Invalid human authorization
  can produce BLOCK. Gate decision and workflow status are separate fields.
- MEASURED_LOCAL is rejected until a verifiable real-instrument adapter
  provides provenance; untrusted labels do not prove physical observation.
- Source result_hash by itself is not evidence of correct semantics: replay
  executes the graph from the supplied inputs and checks the entire result.
- Journal is enabled only when explicitly supplied. If append fails, the
  function raises rather than claiming that the event was durably recorded.
- This is not a security sandbox for executing arbitrary user programs.
  It supports only five declared numeric operations with bounded parameters.
- Scientific novelty, independent validation, real GPU acceleration,
  physical equipment, antifragility and performance superiority remain HOLD.

## Tests

    pytest -q tests/test_reactor_bridge.py tests/test_reactor_canonical_journal.py

The suite includes tamper/rehashed-result rejection, guards, secret/HDB
fail-closed, idempotent journaling, simulated-evidence separation, and integration
with the canonical SQLite journal.

## Promotion and rollback

This change must enter as a reviewed pull request. Do not merge automatically.
Full production acceptance additionally requires CI, journal policy review,
and an explicit constitutional executor contract in a separate PR.

Rollback: revert the PR commit(s); remove the added app/reactor_bridge.py,
matverse_reactors/ and reactor test files. No existing canonical interface
or database schema is modified.
