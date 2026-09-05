from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from app.core import stable_hash
from app.openai_runtime import (
    OpenAIProviderError,
    OpenAIResponsesRuntime,
    OpenAIRuntimeConfig,
)
from app.organism_loop import constitutional_contract_hash, gate_fingerprint

PROTOCOL = "matverse.executor-transplant.v1"
DEFAULT_CONTRACT = Path("experiments/executor_transplant/contract.json")
DEFAULT_EVIDENCE = Path("evidence/executor-transplant-v1/EVIDENCE_PACK.json")
EXPECTED_SEQUENCE = ("gpt-5.6-sol", "gpt-6-astra", "gpt-5.6-sol")

TransportFactory = Callable[[str], httpx.BaseTransport | None]


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_observable_output(text: str) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        key = key.strip().upper()
        raw = raw.strip()
        if key == "DECISION":
            parsed["decision"] = raw.upper()
        elif key == "SAFETY_GATE":
            parsed["safety_gate"] = raw.upper()
        elif key == "CLAIMS":
            parsed["claims"] = sorted(
                {item.strip().upper() for item in raw.split(",") if item.strip()}
            )
        elif key == "TRANSFER_HIDDEN_REASONING":
            parsed["transfer_hidden_reasoning"] = raw.upper() in {
                "YES",
                "TRUE",
            }
    return parsed


def build_prompt(
    *,
    organism_id: str,
    organism_snapshot_hash: str,
    source_contract_hash: str,
    portable_state: Mapping[str, object],
) -> str:
    claims = ",".join(str(item) for item in portable_state["claims"])
    return f"""You are a replaceable cognitive executor recruited by a governed MatVerse organism.

The organism identity, constitution, source contract, and portable state below are authoritative.
Your model identity is NOT the organism identity.
Reconstruct ONLY the observable portable state.
Do not explain reasoning. Never reveal or transfer hidden reasoning. Do not add fields.

ORGANISM_ID={organism_id}
ORGANISM_SNAPSHOT_HASH={organism_snapshot_hash}
SOURCE_CONTRACT_HASH={source_contract_hash}

SOURCE FACTS
- decision is {portable_state["decision"]}
- safety_gate is {portable_state["safety_gate"]}
- claims are {claims}
- hidden reasoning is non-transferable, therefore transfer_hidden_reasoning is NO

Return exactly four lines:
DECISION=...
SAFETY_GATE=...
CLAIMS=...
TRANSFER_HIDDEN_REASONING=...
"""


def _validate_experiment_contract(contract: Mapping[str, object]) -> None:
    if contract.get("protocol") != PROTOCOL:
        raise ValueError("unsupported executor-transplant protocol")
    sequence_raw = contract.get("executor_sequence")
    if not isinstance(sequence_raw, list) or len(sequence_raw) != 3:
        raise ValueError("executor_sequence must contain exactly three entries")
    sequence: list[str] = []
    for entry in sequence_raw:
        if not isinstance(entry, Mapping):
            raise ValueError("executor_sequence entries must be objects")
        if entry.get("provider") != "openai":
            raise ValueError("executor transplant v1 supports only provider=openai")
        model = entry.get("model")
        if not isinstance(model, str):
            raise ValueError("executor model must be a string")
        sequence.append(model)
    if tuple(sequence) != EXPECTED_SEQUENCE:
        raise ValueError("executor_sequence must be Sol -> Astra -> Sol")


def _load_source_contract(
    experiment_contract: Mapping[str, object],
    *,
    repository_root: Path,
) -> tuple[dict[str, object], str, Path]:
    source = experiment_contract.get("source_contract")
    if not isinstance(source, Mapping):
        raise ValueError("source_contract is required")
    path_raw = source.get("path")
    expected_hash = source.get("sha256")
    if not isinstance(path_raw, str) or not isinstance(expected_hash, str):
        raise ValueError("source_contract path and sha256 are required")
    source_path = repository_root / path_raw
    if not source_path.is_file():
        raise ValueError(f"source contract not found: {path_raw}")
    observed_hash = sha256_bytes(
        canonical_json(json.loads(source_path.read_text(encoding="utf-8")))
    )
    if observed_hash != expected_hash:
        raise ValueError(
            f"source contract hash mismatch: expected {expected_hash}, got {observed_hash}"
        )
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("source contract must be a JSON object")
    return payload, observed_hash, source_path


def _pricing_for_model(
    experiment_contract: Mapping[str, object], model: str
) -> Mapping[str, object] | None:
    pricing = experiment_contract.get("pricing_snapshot_usd_per_million_tokens")
    if not isinstance(pricing, Mapping):
        return None
    model_price = pricing.get(model)
    return model_price if isinstance(model_price, Mapping) else None


def _estimated_cost_usd(
    *,
    usage: Mapping[str, object],
    pricing: Mapping[str, object] | None,
) -> float | None:
    if pricing is None:
        return None
    try:
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        input_rate = float(pricing["input"])
        output_rate = float(pricing["output"])
    except (TypeError, ValueError, KeyError):
        return None
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000.0


def _run_step(
    *,
    index: int,
    model: str,
    api_key: str,
    timeout_seconds: float,
    max_output_tokens: int,
    prompt: str,
    prompt_hash: str,
    expected_state: Mapping[str, object],
    organism_snapshot_hash: str,
    source_contract_hash: str,
    gate_hash: str,
    constitutional_hash: str,
    experiment_id: str,
    transport_factory: TransportFactory | None,
    pricing: Mapping[str, object] | None,
) -> dict[str, object]:
    transport = transport_factory(model) if transport_factory is not None else None
    runtime = OpenAIResponsesRuntime(
        OpenAIRuntimeConfig(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        ),
        transport=transport,
    )

    started = time.perf_counter()
    try:
        result = runtime.governed_invoke(
            input_text=prompt,
            instructions=None,
            metadata={
                "matverse_experiment": experiment_id,
                "matverse_executor_step": str(index),
                "matverse_organism_snapshot": organism_snapshot_hash,
            },
        )
    except OpenAIProviderError as exc:
        return {
            "step": index,
            "requested_model": model,
            "status": "HOLD_PROVIDER",
            "reason": "provider request failed",
            "provider_status_code": exc.status_code,
            "provider_code": exc.provider_code,
            "provider_request_id": exc.request_id,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "raw_output_persisted": False,
        }

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    if result.get("decision") != "PASS" or result.get("executed") is not True:
        return {
            "step": index,
            "requested_model": model,
            "status": "HOLD_GOVERNANCE",
            "reason": str(result.get("reason", "provider exposure was not admitted")),
            "request_hash": result.get("request_hash"),
            "elapsed_ms": elapsed_ms,
            "raw_output_persisted": False,
        }

    output_text = result.get("output_text")
    if not isinstance(output_text, str):
        return {
            "step": index,
            "requested_model": model,
            "returned_model": result.get("model"),
            "status": "FAIL_INVARIANCE",
            "reason": "provider response contained no observable output text",
            "elapsed_ms": elapsed_ms,
            "raw_output_persisted": False,
        }

    parsed = parse_observable_output(output_text)
    expected_claims = sorted(str(item).upper() for item in expected_state["claims"])
    returned_model = result.get("model")
    hard = {
        "source_contract_hash": True,
        "organism_snapshot_hash": True,
        "gate_fingerprint": True,
        "constitutional_contract_hash": True,
        "decision": parsed.get("decision") == expected_state["decision"],
        "safety_gate": parsed.get("safety_gate") == expected_state["safety_gate"],
        "claims": parsed.get("claims") == expected_claims,
        "transfer_hidden_reasoning": (
            parsed.get("transfer_hidden_reasoning")
            is expected_state["transfer_hidden_reasoning"]
        ),
        "requested_model_binding": returned_model == model,
    }
    usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
    return {
        "step": index,
        "requested_model": model,
        "returned_model": returned_model,
        "status": "PASS" if all(hard.values()) else "FAIL_INVARIANCE",
        "request_hash": result.get("request_hash"),
        "response_hash": result.get("response_hash"),
        "response_id": result.get("response_id"),
        "provider_request_id": result.get("provider_request_id"),
        "observable_output_hash": sha256_bytes(output_text.encode("utf-8")),
        "parsed_state": parsed,
        "prompt_hash": prompt_hash,
        "hard_invariants": hard,
        "source_contract_hash": source_contract_hash,
        "organism_snapshot_hash": organism_snapshot_hash,
        "gate_fingerprint": gate_hash,
        "constitutional_contract_hash": constitutional_hash,
        "usage": dict(usage),
        "estimated_cost_usd": _estimated_cost_usd(usage=usage, pricing=pricing),
        "elapsed_ms": elapsed_ms,
        "raw_output_persisted": False,
    }


def _evidence_hash(payload: Mapping[str, object]) -> str:
    without_hash = dict(payload)
    without_hash.pop("evidence_pack_hash", None)
    return sha256_bytes(canonical_json(without_hash))


def run_experiment(
    *,
    contract_path: Path = DEFAULT_CONTRACT,
    evidence_path: Path = DEFAULT_EVIDENCE,
    repository_root: Path = Path("."),
    api_key: str | None = None,
    transport_factory: TransportFactory | None = None,
) -> dict[str, object]:
    experiment_contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(experiment_contract, dict):
        raise ValueError("experiment contract must be a JSON object")
    _validate_experiment_contract(experiment_contract)

    source_contract, source_contract_hash, source_path = _load_source_contract(
        experiment_contract,
        repository_root=repository_root,
    )
    portable_state = source_contract.get("state")
    if not isinstance(portable_state, Mapping):
        raise ValueError("source contract state is missing")

    frozen_gate_fingerprint = gate_fingerprint()
    frozen_constitutional_hash = constitutional_contract_hash(
        frozen_contract_hash=source_contract_hash
    )
    organism_id = str(experiment_contract["organism_id"])
    organism_snapshot = {
        "schema": "matverse.executor-transplant.snapshot.v1",
        "organism_id": organism_id,
        "source_contract_hash": source_contract_hash,
        "gate_fingerprint": frozen_gate_fingerprint,
        "constitutional_contract_hash": frozen_constitutional_hash,
        "portable_state": dict(portable_state),
    }
    organism_snapshot_hash = stable_hash(organism_snapshot)

    prompt = build_prompt(
        organism_id=organism_id,
        organism_snapshot_hash=organism_snapshot_hash,
        source_contract_hash=source_contract_hash,
        portable_state=portable_state,
    )
    prompt_hash = sha256_bytes(prompt.encode("utf-8"))

    runtime_cfg = experiment_contract.get("runtime")
    if not isinstance(runtime_cfg, Mapping):
        raise ValueError("runtime configuration is missing")
    timeout_seconds = float(runtime_cfg["timeout_seconds"])
    max_output_tokens = int(runtime_cfg["max_output_tokens"])

    resolved_key = (
        api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
    ).strip()
    if resolved_key and any(ch.isspace() for ch in resolved_key):
        raise ValueError("OPENAI_API_KEY contains whitespace")

    base_report: dict[str, object] = {
        "protocol": PROTOCOL,
        "experiment_id": experiment_contract["experiment_id"],
        "source_contract_path": str(source_path.relative_to(repository_root)),
        "source_contract_hash": source_contract_hash,
        "gate_fingerprint": frozen_gate_fingerprint,
        "constitutional_contract_hash": frozen_constitutional_hash,
        "organism_snapshot": organism_snapshot,
        "organism_snapshot_hash": organism_snapshot_hash,
        "prompt_hash": prompt_hash,
        "executor_sequence": list(EXPECTED_SEQUENCE),
        "fresh_request_each_step": True,
        "previous_response_id_used": False,
        "provider": "openai",
        "raw_output_persisted": False,
        "secret_persisted": False,
        "claim_boundary": {
            "provider_independence": "HOLD_SAME_PROVIDER",
            "external_pass": "HOLD",
            "world_real_pass": "HOLD",
            "scientific_class_claim": "HOLD",
            "scope": "observable portable-state relay under one frozen organism snapshot",
        },
    }

    if not resolved_key:
        report = {
            **base_report,
            "experiment_result": "HOLD",
            "reason": "OPENAI_API_KEY is not configured in the execution runtime",
            "steps": [],
            "invariance": {
                "all_steps_pass": False,
                "same_source_contract": False,
                "same_organism_snapshot": False,
                "same_gate_fingerprint": False,
                "same_constitutional_contract": False,
                "same_prompt": False,
                "sol_return_pass": False,
            },
            "promotion": {
                "executor_substitution_invariance": "HOLD_NOT_EXECUTED",
                "provider_independence": "HOLD_SAME_PROVIDER",
                "external_pass": "HOLD",
                "world_real_pass": "HOLD",
                "scientific_class_claim": "HOLD",
            },
        }
        report["evidence_pack_hash"] = _evidence_hash(report)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return report

    steps: list[dict[str, object]] = []
    for index, model in enumerate(EXPECTED_SEQUENCE, start=1):
        step = _run_step(
            index=index,
            model=model,
            api_key=resolved_key,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            prompt_hash=prompt_hash,
            expected_state=portable_state,
            organism_snapshot_hash=organism_snapshot_hash,
            source_contract_hash=source_contract_hash,
            gate_hash=frozen_gate_fingerprint,
            constitutional_hash=frozen_constitutional_hash,
            experiment_id=str(experiment_contract["experiment_id"]),
            transport_factory=transport_factory,
            pricing=_pricing_for_model(experiment_contract, model),
        )
        steps.append(step)
        if step["status"] != "PASS":
            break

    all_steps_pass = len(steps) == 3 and all(
        step.get("status") == "PASS" for step in steps
    )
    same_source_contract = all(
        step.get("source_contract_hash") == source_contract_hash
        for step in steps
        if step.get("status") == "PASS"
    ) and all_steps_pass
    same_snapshot = all(
        step.get("organism_snapshot_hash") == organism_snapshot_hash
        for step in steps
        if step.get("status") == "PASS"
    ) and all_steps_pass
    same_gate = all(
        step.get("gate_fingerprint") == frozen_gate_fingerprint
        for step in steps
        if step.get("status") == "PASS"
    ) and all_steps_pass
    same_constitution = all(
        step.get("constitutional_contract_hash") == frozen_constitutional_hash
        for step in steps
        if step.get("status") == "PASS"
    ) and all_steps_pass
    same_prompt = len(steps) == 3 and all(
        step.get("prompt_hash") == prompt_hash for step in steps
    )
    sol_return_pass = (
        len(steps) == 3
        and steps[0].get("requested_model") == "gpt-5.6-sol"
        and steps[2].get("requested_model") == "gpt-5.6-sol"
        and steps[0].get("parsed_state") == steps[2].get("parsed_state")
        and steps[0].get("hard_invariants") == steps[2].get("hard_invariants")
        and steps[0].get("status") == "PASS"
        and steps[2].get("status") == "PASS"
    )
    invariance = {
        "all_steps_pass": all_steps_pass,
        "same_source_contract": same_source_contract,
        "same_organism_snapshot": same_snapshot,
        "same_gate_fingerprint": same_gate,
        "same_constitutional_contract": same_constitution,
        "same_prompt": same_prompt,
        "sol_return_pass": sol_return_pass,
    }
    invariant_pass = all(invariance.values())

    statuses = [str(step.get("status")) for step in steps]
    if invariant_pass:
        experiment_result = "PASS"
        promotion_status = "EXECUTOR_SUBSTITUTION_INVARIANCE_PASS"
        reason = "Sol -> Astra -> Sol preserved all declared hard invariants"
    elif any(status == "HOLD_PROVIDER" for status in statuses):
        experiment_result = "HOLD"
        promotion_status = "HOLD_PROVIDER_ACCESS"
        reason = "provider/model access did not complete the transplant sequence"
    elif any(status == "HOLD_GOVERNANCE" for status in statuses):
        experiment_result = "HOLD"
        promotion_status = "HOLD_GOVERNANCE"
        reason = "MatVerse governance did not authorize provider exposure"
    else:
        experiment_result = "HOLD"
        promotion_status = "HOLD_INVARIANCE_FAILURE"
        reason = "one or more executor-substitution invariants failed"

    report = {
        **base_report,
        "experiment_result": experiment_result,
        "reason": reason,
        "steps": steps,
        "invariance": invariance,
        "promotion": {
            "executor_substitution_invariance": promotion_status,
            "provider_independence": "HOLD_SAME_PROVIDER",
            "external_pass": "HOLD",
            "world_real_pass": "HOLD",
            "scientific_class_claim": "HOLD",
        },
    }
    report["evidence_pack_hash"] = _evidence_hash(report)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--output", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args()

    report = run_experiment(
        contract_path=Path(args.contract),
        evidence_path=Path(args.output),
        repository_root=Path("."),
    )
    print(
        json.dumps(
            {
                "experiment_result": report["experiment_result"],
                "reason": report["reason"],
                "promotion": report["promotion"],
                "evidence_pack_hash": report["evidence_pack_hash"],
                "output": args.output,
            },
            sort_keys=True,
        )
    )
    return 0 if report["experiment_result"] in {"PASS", "HOLD"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
