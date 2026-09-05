from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

from app.core import stable_hash
from app.executor_substitution import (
    ExecutorArm,
    OrganismCloneConfig,
    capture_snapshot,
    run_executor_substitution,
)
from app.openai_runtime import OpenAIResponsesRuntime, OpenAIRuntimeConfig
from app.physiology import ExecutionResult

EXPECTED_TOKEN = "MATVERSE_SUBSTITUTION_OK"


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def build_openai_executor(*, api_key: str, model: str, arm_id: str):
    runtime = OpenAIResponsesRuntime(
        OpenAIRuntimeConfig(
            api_key=api_key,
            model=model,
            timeout_seconds=120.0,
            max_output_tokens=64,
        )
    )

    def execute(proposal):
        task = str(proposal.get("task", "")).strip()
        if not task:
            return ExecutionResult(
                status="MISMATCH",
                effect={"validated": False, "provider": "openai", "model": model, "reason": "missing_task"},
            )
        result = runtime.governed_invoke(
            input_text=task,
            instructions=f"Return exactly this token and no other text: {EXPECTED_TOKEN}",
            metadata={"matverse_experiment": "executor-substitution-v1", "arm_id": arm_id},
        )
        if result.get("decision") != "PASS" or not result.get("executed"):
            return ExecutionResult(
                status=str(result.get("decision", "HOLD")),
                effect={
                    "validated": False,
                    "provider": "openai",
                    "model": model,
                    "governance_decision": result.get("decision"),
                    "request_hash": result.get("request_hash"),
                },
            )
        output_text = str(result.get("output_text", "")).strip()
        validated = output_text == EXPECTED_TOKEN
        return ExecutionResult(
            status="OK" if validated else "MISMATCH",
            effect={
                "validated": validated,
                "provider": "openai",
                "model": result.get("model") or model,
                "response_id": result.get("response_id"),
                "request_hash": result.get("request_hash"),
                "response_hash": result.get("response_hash"),
                "provider_request_id": result.get("provider_request_id"),
                "output_hash": stable_hash({"output_text": output_text}),
                "usage": result.get("usage", {}),
            },
        )

    return execute


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MatVerse Executor Substitution Experiment v1")
    parser.add_argument("--output", default="executor-substitution-v1-report.json")
    args = parser.parse_args()

    api_key = require_env("OPENAI_API_KEY")
    model_a = require_env("MATVERSE_EXECUTOR_A_MODEL")
    model_b = require_env("MATVERSE_EXECUTOR_B_MODEL")
    if model_a == model_b:
        raise SystemExit("MATVERSE_EXECUTOR_A_MODEL and MATVERSE_EXECUTOR_B_MODEL must differ")

    # Ephemeral constitutional secrets are generated in-process and are never written to the report.
    state_secret = secrets.token_hex(32)
    authority_secret = secrets.token_hex(32)
    frozen_contract_hash = stable_hash(
        {
            "schema": "matverse.executor-substitution-experiment.v1",
            "purpose": "paired executor substitution under one frozen organism snapshot",
        }
    )
    config = OrganismCloneConfig(
        organism_id="executor-substitution-v1",
        frozen_contract_hash=frozen_contract_hash,
        runtime_id="executor-substitution-runtime-v1",
        state_secret=state_secret,
        authority_secrets={"experiment-authority": authority_secret},
    )
    snapshot = capture_snapshot(config)
    proposal = {
        "action": "READ",
        "task": (
            "This is a bounded MatVerse executor-substitution validation task. "
            f"Follow the runtime instruction and return the exact required token {EXPECTED_TOKEN}."
        ),
    }
    report = run_executor_substitution(
        config=config,
        snapshot=snapshot,
        proposal=proposal,
        arms=(
            ExecutorArm("arm-a", "openai", model_a, build_openai_executor(api_key=api_key, model=model_a, arm_id="arm-a")),
            ExecutorArm("arm-b", "openai", model_b, build_openai_executor(api_key=api_key, model=model_b, arm_id="arm-b")),
        ),
    )
    output_path = Path(args.output)
    output_path.write_text(json.dumps(report.public_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": report.schema,
        "experiment_id": report.experiment_id,
        "snapshot_hash": report.snapshot_hash,
        "task_hash": report.task_hash,
        "models": [item.model for item in report.arms],
        "success": [item.success for item in report.arms],
        "elapsed_ms": [item.elapsed_ms for item in report.arms],
        "usage": [dict(item.usage) for item in report.arms],
        "invariants": dict(report.invariants),
        "substitution_pass": report.substitution_pass,
        "receipt_hash": report.receipt.get("receipt_hash"),
        "output": str(output_path),
    }, sort_keys=True, indent=2))
    return 0 if report.substitution_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
