"""Production journal integration must use app.physiology, not a parallel ledger."""
from __future__ import annotations

from tempfile import TemporaryDirectory

from app.physiology import DurableEventJournal
from app.reactor_bridge import assess_reactor


def test_reactor_evidence_idempotently_enters_canonical_journal():
    graph = {
        "schema": "matverse.reactor-graph.v1",
        "nodes": [{"node_id": "sensor", "kind": "TRANSDUCER",
                   "params": {"field": "signal"}}],
    }
    with TemporaryDirectory() as tmp:
        with DurableEventJournal(f"{tmp}/organism.db") as journal:
            first = assess_reactor(
                graph_spec=graph, inputs={"signal": 5}, journal=journal
            )
            second = assess_reactor(
                graph_spec=graph, inputs={"signal": 5}, journal=journal
            )
            events = journal.read(topic="reactor")
            assert first["decision"] == "HOLD"
            assert second["journal_event_id"] == first["journal_event_id"]
            assert len(events) == 1
            assert events[0].payload["result_hash"] == first["result_hash"]
            assert events[0].payload["receipt_hash"] == first["receipt"]["receipt_hash"]
            assert events[0].payload["external_execution_allowed"] is False
