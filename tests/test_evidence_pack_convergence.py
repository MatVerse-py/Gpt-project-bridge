from __future__ import annotations

import json

import httpx

from app.core import Decision
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.drift_engine import LensReading, TwoAxisAdjudicator, Verdict
from app.openai_runtime import OpenAIResponsesRuntime, OpenAIRuntimeConfig
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult, PhysiologyEngine


def _organism() -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="evidence-pack-convergence",
        frozen_contract_hash="c" * 64,
        runtime_id="pytest-convergence",
        state_secret="state-secret",
        authority_secrets={"independent-authorizer": "authority-secret"},
    )


def test_openai_runtime_executes_as_a_governed_physiology_executor(tmp_path):
    secret = "test-secret-key-material"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["store"] is False
        assert body["model"] == "gpt-6-astra"
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return httpx.Response(
            200,
            headers={"x-request-id": "req_convergence_1"},
            json={
                "id": "resp_convergence_1",
                "object": "response",
                "model": "gpt-6-astra",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "operational"}],
                    }
                ],
                "usage": {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
            },
        )

    provider = OpenAIResponsesRuntime(
        OpenAIRuntimeConfig(api_key=secret, model="gpt-6-astra", max_output_tokens=32),
        transport=httpx.MockTransport(handler),
    )

    def executor(proposal):
        result = provider.governed_invoke(
            input_text=str(proposal["input"]),
            instructions="Return one word.",
            metadata={"matverse_scope": "evidence-pack-convergence"},
        )
        assert result["decision"] == "PASS"
        return ExecutionResult(
            status="OK",
            effect={
                "provider": "openai",
                "model": result["model"],
                "output_text": result["output_text"],
                "request_hash": result["request_hash"],
                "response_hash": result["response_hash"],
                "provider_request_id": result["provider_request_id"],
            },
        )

    journal = DurableEventJournal(tmp_path / "convergence.sqlite3")
    engine = PhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=DeterministicTelemetry(plan=DeterministicFaultPlan(seed=17, cycles=1, directives=())),
        executor=executor,
    )
    cycle = engine.tick(proposal={"action": "READ", "resource": "openai", "input": "say operational"})

    assert cycle.decision is Decision.PASS
    assert cycle.executed is True
    events = journal.read(limit=100)
    assert [event.event_type for event in events] == [
        "TICK",
        "OBSERVATION",
        "RECOVERY_PLAN",
        "DECISION",
        "EXECUTION",
        "EFFECT",
        "MEMORY_COMMIT",
    ]
    effect = next(event for event in events if event.event_type == "EFFECT").payload
    rendered = json.dumps(effect, sort_keys=True)
    assert "operational" in rendered
    assert "req_convergence_1" in rendered
    assert secret not in rendered
    journal.close()


def test_drift_engine_remains_a_distinct_instrument_not_a_parallel_runtime():
    lenses = [
        LensReading("L1", True, (1.0, 1.2, 1.1), 0.5, 0.9, 1.3),
        LensReading("L2", True, (0.9, 1.0, 1.1), 0.5, 0.8, 1.2),
    ]
    verdict, detail = TwoAxisAdjudicator(quorum_k=2).adjudicate(True, lenses)
    assert verdict is Verdict.DRIFT
    assert detail["L1"][0] == "drift"
    assert detail["L2"][0] == "drift"
