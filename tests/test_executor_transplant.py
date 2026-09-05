from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from experiments.executor_transplant.run import run_experiment

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "experiments" / "executor_transplant" / "contract.json"

OBSERVABLE = (
    "DECISION=PASS\n"
    "SAFETY_GATE=PASS\n"
    "CLAIMS=C1,C2,C3\n"
    "TRANSFER_HIDDEN_REASONING=NO\n"
)


def _success_transport(expected_model: str, *, returned_model: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["store"] is False
        assert body["model"] == expected_model
        assert "previous_response_id" not in body
        assert body["metadata"]["matverse_experiment"] == "sol-astra-sol-pilot-001"
        assert body["metadata"]["matverse_organism_snapshot"]
        return httpx.Response(
            200,
            headers={"x-request-id": f"req-{expected_model}"},
            json={
                "id": f"resp-{expected_model}",
                "object": "response",
                "model": returned_model or expected_model,
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": OBSERVABLE}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )

    return httpx.MockTransport(handler)


def test_missing_capability_produces_hold_and_ignores_provider_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "evidence.json"
    provider_secret = "sk-provider-secret-must-not-reach-executor"
    monkeypatch.setenv("OPENAI_API_KEY", provider_secret)
    monkeypatch.delenv("MATVERSE_OPENAI_CAPABILITY_TOKEN", raising=False)

    report = run_experiment(
        contract_path=CONTRACT,
        evidence_path=output,
        repository_root=ROOT,
        api_key=None,
    )

    assert report["experiment_result"] == "HOLD"
    assert report["promotion"]["executor_substitution_invariance"] == "HOLD_NOT_EXECUTED"
    assert report["promotion"]["external_pass"] == "HOLD"
    assert report["steps"] == []
    assert report["secret_persisted"] is False
    assert report["capability_persisted"] is False
    assert report["raw_output_persisted"] is False
    assert report["secret_plane"]["provider_secret_exposed_to_executor"] is False
    assert len(report["evidence_pack_hash"]) == 64

    persisted = output.read_text(encoding="utf-8")
    assert "secret_ref://openai/matverse/executor-transplant" in persisted
    assert provider_secret not in persisted
    assert "OPENAI_API_KEY" not in persisted


def test_mocked_sol_astra_sol_preserves_invariants_without_persisting_capability(
    tmp_path: Path,
) -> None:
    output = tmp_path / "evidence.json"
    capability = "capability-" + ("a" * 40)

    def factory(model: str):
        return _success_transport(model)

    report = run_experiment(
        contract_path=CONTRACT,
        evidence_path=output,
        repository_root=ROOT,
        api_key=capability,
        base_url="http://127.0.0.1:8787/v1",
        transport_factory=factory,
    )

    assert report["experiment_result"] == "PASS"
    assert (
        report["promotion"]["executor_substitution_invariance"]
        == "EXECUTOR_SUBSTITUTION_INVARIANCE_PASS"
    )
    assert report["promotion"]["provider_independence"] == "HOLD_SAME_PROVIDER"
    assert report["promotion"]["external_pass"] == "HOLD"
    assert len(report["steps"]) == 3
    assert [step["requested_model"] for step in report["steps"]] == [
        "gpt-5.6-sol",
        "gpt-6-astra",
        "gpt-5.6-sol",
    ]
    assert all(step["status"] == "PASS" for step in report["steps"])
    assert all(step["raw_output_persisted"] is False for step in report["steps"])
    assert all(step["capability_persisted"] is False for step in report["steps"])
    assert report["invariance"] == {
        "all_steps_pass": True,
        "same_source_contract": True,
        "same_organism_snapshot": True,
        "same_gate_fingerprint": True,
        "same_constitutional_contract": True,
        "same_prompt": True,
        "sol_return_pass": True,
        "provider_secret_not_in_executor": True,
    }
    assert report["steps"][0]["parsed_state"] == report["steps"][2]["parsed_state"]
    assert report["steps"][0]["estimated_cost_usd"] == pytest.approx(0.0008)
    assert report["steps"][1]["estimated_cost_usd"] == pytest.approx(0.002)
    assert capability not in output.read_text(encoding="utf-8")


def test_astra_access_hold_does_not_promote(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"

    def factory(model: str):
        if model == "gpt-6-astra":
            def handler(_: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    403,
                    headers={"x-request-id": "req-astra-hold"},
                    json={
                        "error": {
                            "code": "model_not_available",
                            "message": "not available",
                        }
                    },
                )
            return httpx.MockTransport(handler)
        return _success_transport(model)

    report = run_experiment(
        contract_path=CONTRACT,
        evidence_path=output,
        repository_root=ROOT,
        api_key="capability-" + ("b" * 40),
        transport_factory=factory,
    )

    assert report["experiment_result"] == "HOLD"
    assert report["promotion"]["executor_substitution_invariance"] == "HOLD_PROVIDER_ACCESS"
    assert report["promotion"]["external_pass"] == "HOLD"
    assert len(report["steps"]) == 2
    assert report["steps"][0]["status"] == "PASS"
    assert report["steps"][1]["status"] == "HOLD_PROVIDER"
    assert report["steps"][1]["provider_code"] == "model_not_available"


def test_expired_secret_capability_has_distinct_hold(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"

    def factory(model: str):
        if model == "gpt-6-astra":
            def handler(_: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    401,
                    json={
                        "error": {
                            "code": "capability_expired",
                            "message": "request not authorized by MatVerse secret plane",
                        }
                    },
                )
            return httpx.MockTransport(handler)
        return _success_transport(model)

    report = run_experiment(
        contract_path=CONTRACT,
        evidence_path=output,
        repository_root=ROOT,
        api_key="capability-" + ("c" * 40),
        transport_factory=factory,
    )

    assert report["experiment_result"] == "HOLD"
    assert (
        report["promotion"]["executor_substitution_invariance"]
        == "HOLD_SECRET_CAPABILITY"
    )
    assert report["steps"][1]["status"] == "HOLD_SECRET"
    assert report["steps"][1]["provider_code"] == "capability_expired"


def test_returned_model_mismatch_fails_binding(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"

    def factory(model: str):
        if model == "gpt-6-astra":
            return _success_transport(model, returned_model="gpt-5.6-sol")
        return _success_transport(model)

    report = run_experiment(
        contract_path=CONTRACT,
        evidence_path=output,
        repository_root=ROOT,
        api_key="capability-" + ("d" * 40),
        transport_factory=factory,
    )

    assert report["experiment_result"] == "HOLD"
    assert (
        report["promotion"]["executor_substitution_invariance"]
        == "HOLD_INVARIANCE_FAILURE"
    )
    assert report["steps"][1]["status"] == "FAIL_INVARIANCE"
    assert report["steps"][1]["hard_invariants"]["requested_model_binding"] is False


def test_source_contract_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    copied_root = tmp_path / "repo"
    source_dir = copied_root / "experiments" / "cross_runtime"
    source_dir.mkdir(parents=True)
    source = json.loads(
        (ROOT / "experiments" / "cross_runtime" / "contract.json").read_text(
            encoding="utf-8"
        )
    )
    source["state"]["decision"] = "BLOCK"
    (source_dir / "contract.json").write_text(
        json.dumps(source, indent=2),
        encoding="utf-8",
    )

    output = tmp_path / "evidence.json"
    with pytest.raises(ValueError, match="source contract hash mismatch"):
        run_experiment(
            contract_path=CONTRACT,
            evidence_path=output,
            repository_root=copied_root,
            api_key="",
        )
