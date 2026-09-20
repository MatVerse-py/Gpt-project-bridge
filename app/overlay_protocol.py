"""Bounded, signed text transformation contract shared by overlay endpoints."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

PROTOCOL = "matverse.overlay-execution.v1"
CAPABILITY = "text:normalize:v1"
MAX_TEXT_BYTES = 64 * 1024
MAX_WIRE_BYTES = 512 * 1024
CONTRACT = {
    "protocol": PROTOCOL,
    "capability": CAPABILITY,
    "input": "UTF-8 text without unpaired surrogates; at most 65536 bytes",
    "transform": "replace CRLF with LF, then remaining CR with LF",
    "output": "text, sha256 of normalized UTF-8 bytes, utf8_bytes",
    "loss": "original CR and CRLF line-ending distinctions",
    "preserved": ["all other Unicode code points", "whitespace", "case"],
    "effects": "pure computation with a transactional local result commit",
}


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


CONTRACT_HASH = digest(CONTRACT)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TextInput(StrictModel):
    text: str

    @field_validator("text")
    @classmethod
    def bounded_utf8(cls, value: str) -> str:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("unpaired_surrogate") from exc
        if size > MAX_TEXT_BYTES:
            raise ValueError("text_too_large")
        return value


class Job(StrictModel):
    protocol: Literal["matverse.overlay-execution.v1"] = PROTOCOL
    task_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    organism_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    source_domain: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    target_domain: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    relation_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability: Literal["text:normalize:v1"] = CAPABILITY
    issued_at: int = Field(ge=0)
    expires_at: int = Field(gt=0)
    input: TextInput
    human: dict | None = None


class SignedMessage(StrictModel):
    kind: Literal["request", "result", "discovery"]
    body: dict
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")


def sign(kind: str, body: dict, key: Ed25519PrivateKey) -> dict:
    message = {"kind": kind, "body": body}
    signature = key.sign(PROTOCOL.encode() + b"\0" + canonical(message)).hex()
    return {**message, "signature": signature}


def verify(message: dict, kind: str, public_key: str) -> dict:
    parsed = SignedMessage.model_validate(message)
    if parsed.kind != kind:
        raise PermissionError("message_kind_mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(parsed.signature),
            PROTOCOL.encode() + b"\0" + canonical({"kind": parsed.kind, "body": parsed.body}),
        )
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("invalid_message_signature") from exc
    return parsed.body


def normalized_result(text: str) -> dict:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw = normalized.encode("utf-8")
    return {"text": normalized, "sha256": hashlib.sha256(raw).hexdigest(), "utf8_bytes": len(raw)}


class TextExecutor:
    """Two real runtimes implementing the same bounded, pure capability."""

    def __init__(self, backend: str, node_binary: str | None = None):
        if backend not in {"python", "node"}:
            raise ValueError("unsupported_executor")
        self.backend = backend
        self.script = Path(__file__).with_name("overlay_executor.mjs")
        self.binary = str(Path(sys.executable).resolve())
        version = sys.version.split()[0]
        if backend == "node":
            candidate = node_binary or shutil.which("node")
            if not candidate:
                raise RuntimeError("node_runtime_unavailable")
            self.binary = str(Path(candidate).resolve(strict=True))
            version = subprocess.run([self.binary, "--version"], check=True, capture_output=True, text=True, timeout=5).stdout.strip()
        source = self.script if backend == "node" else Path(__file__)
        self._source_path = source
        self._source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        self._runtime_hash = hashlib.sha256(Path(self.binary).read_bytes()).hexdigest()
        self.binding = {"backend": backend, "runtime_version": version, "runtime_sha256": self._runtime_hash, "source_sha256": self._source_hash, "contract_hash": CONTRACT_HASH}
        self.binding_hash = digest(self.binding)
        # Real execution establishes that the selected runtime speaks this contract.
        sample = "MatVerse\r\nα\r🙂"
        if self.execute(sample) != normalized_result(sample):
            raise RuntimeError("executor_preflight_failed")

    def execute(self, text: str) -> dict:
        TextInput(text=text)
        if hashlib.sha256(self._source_path.read_bytes()).hexdigest() != self._source_hash:
            raise RuntimeError("executor_source_changed")
        if hashlib.sha256(Path(self.binary).read_bytes()).hexdigest() != self._runtime_hash:
            raise RuntimeError("executor_runtime_changed")
        if self.backend == "python":
            return normalized_result(text)
        proc = subprocess.run([self.binary, str(self.script)], input=canonical({"text": text}), capture_output=True, timeout=5, check=True)
        if len(proc.stdout) > MAX_WIRE_BYTES:
            raise RuntimeError("executor_output_too_large")
        result = json.loads(proc.stdout)
        if result != normalized_result(text):
            raise RuntimeError("executor_contract_violation")
        return result
