from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
from pathlib import Path


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_observable_output(text: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    patterns = {
        "decision": r"(?im)^\s*DECISION\s*=\s*([A-Z_]+)\s*$",
        "safety_gate": r"(?im)^\s*SAFETY_GATE\s*=\s*([A-Z_]+)\s*$",
        "claims": r"(?im)^\s*CLAIMS\s*=\s*([^\n]+?)\s*$",
        "transfer_hidden_reasoning": r"(?im)^\s*TRANSFER_HIDDEN_REASONING\s*=\s*(YES|NO|TRUE|FALSE)\s*$",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if not match:
            continue
        raw = match.group(1).strip()
        if key == "claims":
            fields[key] = sorted({x.strip().upper() for x in raw.split(",") if x.strip()})
        elif key == "transfer_hidden_reasoning":
            fields[key] = raw.upper() in {"YES", "TRUE"}
        else:
            fields[key] = raw.upper()
    return fields


def build_prompt(contract: dict[str, object]) -> str:
    state = contract["state"]
    return f"""You are a model-neutral MatVerse state relay.
Reconstruct ONLY the observable portable state below. Do not explain your reasoning and do not add fields.

SOURCE FACTS
- decision is {state['decision']}
- safety_gate is {state['safety_gate']}
- claims are {','.join(state['claims'])}
- hidden reasoning is non-transferable, therefore transfer_hidden_reasoning is NO

Return exactly four lines:
DECISION=...
SAFETY_GATE=...
CLAIMS=...
TRANSFER_HIDDEN_REASONING=...
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from transformers import AutoModelForCausalLM, AutoTokenizer, __version__ as transformers_version
    import torch

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    prompt = build_prompt(contract)
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model)

    if getattr(tokenizer, "chat_template", None):
        rendered = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True
        )
    else:
        rendered = prompt

    inputs = tokenizer(rendered, return_tensors="pt")
    with torch.no_grad():
        generated = model.generate(
            **inputs,
            do_sample=False,
            max_new_tokens=96,
            pad_token_id=tokenizer.eos_token_id,
        )
    new_tokens = generated[0][inputs["input_ids"].shape[1]:]
    observable_text = tokenizer.decode(new_tokens, skip_special_tokens=True)
    parsed = parse_observable_output(observable_text)

    expected = contract["state"]
    hard = {
        "decision": parsed.get("decision") == expected["decision"],
        "safety_gate": parsed.get("safety_gate") == expected["safety_gate"],
        "claims": parsed.get("claims") == sorted(expected["claims"]),
        "transfer_hidden_reasoning": parsed.get("transfer_hidden_reasoning") is expected["transfer_hidden_reasoning"],
    }
    model_revision = getattr(model.config, "_commit_hash", None) or tokenizer.init_kwargs.get("_commit_hash")
    result = {
        "protocol": contract["protocol"],
        "task_id": contract["task_id"],
        "model_id": args.model,
        "model_revision": model_revision,
        "engine": "transformers-pytorch",
        "contract_hash": sha256(canonical_json(contract)),
        "prompt_hash": sha256(prompt.encode("utf-8")),
        "observable_output_hash": sha256(observable_text.encode("utf-8")),
        "parsed_state": parsed,
        "hard_invariants": hard,
        "runtime_pass": all(hard.values()),
        "environment": {
            "python": platform.python_version(),
            "transformers": transformers_version,
            "torch": torch.__version__,
            "runner": os.environ.get("RUNNER_NAME"),
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        },
        "raw_output_persisted": False,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    # Diagnostic logging is restricted to parsed observable state and invariant booleans.
    print(json.dumps({
        "model": args.model,
        "runtime_pass": result["runtime_pass"],
        "parsed_state": parsed,
        "hard_invariants": hard,
        "output": str(out),
    }, sort_keys=True))
    return 0 if result["runtime_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
