"""MatVerse Reactor -> canonical constitutional runtime: assessment-only adapter.

This integration deliberately does not supply privileged signature_valid=True.
There is no automatic executor and no mutation to organism state.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from .core import evaluate_hdb, omega_gate
from .evidence import evidence_receipt
from matverse_reactors.core import ReactorGraph, digest

BRIDGE_SCHEMA = "matverse.reactor-canonical-bridge.v1"
INPUT_LIMIT_BYTES = 128 * 1024


class JournalWriter(Protocol):
    def append(self, *, event_id: str, topic: str, event_type: str,
               payload: Mapping[str, Any], causation_id: str | None = None,
               correlation_id: str | None = None, created_ns: int | None = None) -> Any: ...


def _validate_inputs(graph_spec: dict[str, Any], inputs: dict[str, Any],
                     previous: dict[str, float] | None) -> None:
    if not isinstance(graph_spec, dict) or not isinstance(inputs, dict):
        raise ValueError("graph_spec and inputs must be dictionaries")
    if previous is not None and not isinstance(previous, dict):
        raise ValueError("previous must be dictionary or None")
    if len(digest({'spec': graph_spec, 'inputs': inputs, 'previous': previous})) != 64:
        raise ValueError("hash error")
    from matverse_reactors.core import canon
    if len(canon({'spec': graph_spec, 'inputs': inputs, 'previous': previous}).encode('utf-8')) > INPUT_LIMIT_BYTES:
        raise ValueError("assessment input exceeds size limit")


def verify_reactor_result(*, graph_spec: dict[str, Any], inputs: dict[str, Any],
                          candidate: dict[str, Any],
                          previous: dict[str, float] | None = None) -> bool:
    """Re-run independent of an untrusted result; reject forged/self-hashed values."""
    _validate_inputs(graph_spec, inputs, previous)
    if not isinstance(candidate, dict):
        return False
    evidence_class = candidate.get('evidence_class')
    if evidence_class not in {'USER_SUPPLIED', 'SIMULATED'}:
        return False
    graph = ReactorGraph.from_dict(graph_spec)
    expected = graph.run(inputs, previous, evidence_class=evidence_class)
    return expected == candidate


def assess_reactor(*, graph_spec: dict[str, Any], inputs: dict[str, Any],
                   previous: dict[str, float] | None = None,
                   evidence_class: str = 'USER_SUPPLIED',
                   human: Mapping[str, Any] | None = None,
                   journal: JournalWriter | None = None) -> dict[str, Any]:
    """Run math, invoke canonical HDB/Ω and receipt, optionally journal hashes.

    This function grants no operational authority, even if local analysis passed.
    Measured telemetry is held until it has an authenticated instrument adapter.
    """
    if evidence_class not in {'USER_SUPPLIED', 'SIMULATED'}:
        raise ValueError('Measured or unknown evidence requires an independent source adapter')
    _validate_inputs(graph_spec, inputs, previous)
    graph = ReactorGraph.from_dict(graph_spec)
    result = graph.run(inputs, previous, evidence_class=evidence_class)
    if not verify_reactor_result(graph_spec=graph_spec, inputs=inputs,
                                 candidate=result, previous=previous):
        raise RuntimeError('Reactor result did not replay')
    # No authority/secrets are passed by the laboratory. Fail closed.
    hdb = evaluate_hdb(None if human is None else dict(human))
    gate_decision, gate_reason = omega_gate(
        hdb=hdb, action='EXECUTE', ontology_ok=True,
        signature_valid=False, transition_valid=False,
    )
    status = 'BLOCK' if gate_decision.value == 'BLOCK' and gate_reason != 'invalid signature' else 'HOLD'
    receipt = evidence_receipt(
        'REACTOR_ASSESSMENT',
        {'bridge_schema': BRIDGE_SCHEMA, 'graph_fingerprint': graph.fingerprint,
         'input_commitment': digest({'inputs': inputs, 'previous': previous}),
         'evidence_class': evidence_class},
        {'result_hash': result['result_hash'], 'gate_decision': gate_decision.value,
         'decision': status, 'external_effects': []},
    )
    outcome = {
        'schema': BRIDGE_SCHEMA,
        'decision': status,
        'reason': gate_reason if status == 'BLOCK' else 'EXTERNAL_AUTHORITY_NOT_PRESENT',
        'gate_decision': gate_decision.value,
        'gate_reason': gate_reason,
        'external_execution_allowed': False,
        'external_effects': [],
        'graph_fingerprint': graph.fingerprint,
        'result_hash': result['result_hash'],
        'evidence_class': evidence_class,
        'result': result,
        'receipt': receipt,
    }
    if journal is not None:
        event = journal.append(
            event_id='reactor-' + receipt['receipt_hash'][:48],
            topic='reactor', event_type='REACTOR_ASSESSMENT',
            payload={
                'schema': BRIDGE_SCHEMA, 'graph_fingerprint': graph.fingerprint,
                'result_hash': result['result_hash'],
                'receipt_hash': receipt['receipt_hash'],
                'evidence_class': evidence_class, 'decision': status,
                'external_execution_allowed': False,
            },
        )
        outcome['journal_event_id'] = event.event_id
        outcome['journal_seq'] = event.seq
    return outcome
