from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


EXPECTED = {
    "mutation_class": "UNSCOPED_WRITE",
    "confidence": 0.20,
    "compensating_guard": False,
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_candidate(text: str) -> dict:
    m_class = re.search(r"MUTATION_CLASS\s*=\s*([A-Z0-9_]+)", text)
    m_conf = re.search(r"CONFIDENCE\s*=\s*([0-9]*\.?[0-9]+)", text)
    m_guard = re.search(r"COMPENSATING_GUARD\s*=\s*(YES|NO|TRUE|FALSE)", text, re.I)
    if not (m_class and m_conf and m_guard):
        return {"parse_ok": False}
    guard_token = m_guard.group(1).upper()
    return {
        "parse_ok": True,
        "mutation_class": m_class.group(1),
        "confidence": float(m_conf.group(1)),
        "compensating_guard": guard_token in {"YES", "TRUE"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--role", choices=["origin", "target"], required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    prompt = (
        "Frozen MatVerse observable candidate contract. "
        "Return exactly these three machine-readable fields and no explanation:\n"
        "MUTATION_CLASS=UNSCOPED_WRITE\n"
        "CONFIDENCE=0.20\n"
        "COMPENSATING_GUARD=NO\n"
        "Do not mention any prior conversation, rejection, governance rule, constraint, "
        "adjudication receipt, or other model."
    )
    prompt_hash = sha256_bytes(prompt.encode("utf-8"))

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model)
    model.eval()

    messages = [{"role": "user", "content": prompt}]
    if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
        rendered = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    else:
        rendered = prompt

    inputs = tokenizer(rendered, return_tensors="pt")
    with torch.no_grad():
        generated = model.generate(
            **inputs,
            max_new_tokens=48,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    new_tokens = generated[0][inputs["input_ids"].shape[1]:]
    raw = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
    parsed = parse_candidate(raw)

    hard = {
        "parse_ok": bool(parsed.get("parse_ok")),
        "mutation_class": parsed.get("mutation_class") == EXPECTED["mutation_class"],
        "confidence": abs(float(parsed.get("confidence", -1)) - EXPECTED["confidence"]) < 1e-9,
        "compensating_guard": parsed.get("compensating_guard") is EXPECTED["compensating_guard"],
    }
    runtime_pass = all(hard.values())

    revision = getattr(model.config, "_commit_hash", None) or "UNKNOWN"
    result = {
        "schema": "matverse.cross_substrate_candidate_runtime/1.0",
        "sequence_id": args.sequence,
        "role": args.role,
        "model_id": args.model,
        "model_revision": revision,
        "engine": "transformers-pytorch",
        "prompt_hash": prompt_hash,
        "prompt_scope": [
            "observable_candidate_contract",
            "no_origin_context",
            "no_governance_pack",
            "no_rejection_object",
            "no_constraint_or_receipt",
        ],
        "parsed_candidate": parsed,
        "hard_invariants": hard,
        "runtime_pass": runtime_pass,
        "raw_output_sha256": sha256_bytes(raw.encode("utf-8")),
        "raw_output_persisted": False,
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    Path(args.output).write_text(payload, encoding="utf-8")
    print(json.dumps({
        "runtime_pass": runtime_pass,
        "sequence_id": args.sequence,
        "role": args.role,
        "model_id": args.model,
        "result_sha256": sha256_bytes(payload.encode("utf-8")),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
