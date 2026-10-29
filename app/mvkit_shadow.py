"""Read-only, opt-in evaluation bridge for a pinned MVKit v2 archive.

This file intentionally has NO invocation from production routes, no writes,
no delegated authority and no replacement for app.core.omega_gate.
"""
from __future__ import annotations

from hashlib import sha256
import io
import math
import sys
from types import ModuleType
from typing import Any, Callable, Sequence
import zipfile

from .core import Decision, stable_hash
from .organism_loop import gate_fingerprint as constitutional_gate_fingerprint

SCHEMA = "matverse.mvkit-shadow.v1"
ARCHIVE_SHA256 = "f14a7c72fbffcc2ac2bef13ea84960680e62f276d6329a16bee80200fd8a6da0"
OMEGA_SOURCE_SHA256 = "300e57c6d86c44bfdd57ce103f2f2e52b39a61336001e01ce45f6709033a2995"
MAX_ARCHIVE_BYTES = 1_000_000
MAX_MEMBER_BYTES = 150_000


def _decision_value(decision: Decision) -> str:
    if not isinstance(decision, Decision):
        raise TypeError("canonical_decision requires app.core.Decision")
    return decision.value


def compare_shadow(
    *,
    canonical_decision: Decision,
    samples: Sequence[float],
    theta_hat: float,
    cvar: float,
    pole: bool,
    evaluator: Callable[..., Any] | None,
    source_digest: str,
    seed: int = 0,
) -> dict[str, Any]:
    """Compute a NON-AUTHORITATIVE comparison; never produce an action grant.

    If the scorer is unavailable, erroneous or malformed, classification is HOLD.
    This routine does not execute, store or authorize anything.
    """
    canon = _decision_value(canonical_decision)
    base = {
        "schema": SCHEMA,
        "canonical_decision": canon,
        "constitutional_gate_fingerprint": constitutional_gate_fingerprint(),
        "mvkit_artifact_sha256": source_digest,
        "authoritative": False,
        "may_authorize_execution": False,
        "may_promote_policy": False,
        "sample_count": len(samples),
    }
    try:
        if evaluator is None:
            raise ValueError("experimental scorer unavailable")
        candidate = evaluator(samples, theta_hat, cvar, pole, seed=seed)
        polarity = getattr(getattr(candidate, "polarity", None), "value", None)
        if polarity not in {"PASS", "BLOCK", "ESCALATE"}:
            raise ValueError("invalid experimental polarity")
        fp = getattr(candidate, "fingerprint", None)
        score = getattr(candidate, "omega", None)
        if not isinstance(fp, str) or len(fp) != 64 or not all(c in "0123456789abcdef" for c in fp):
            raise ValueError("invalid experimental fingerprint")
        if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("invalid experimental score")
        body = {**base, "status": "SHADOW_OBSERVED", "shadow_polarity": polarity,
                "shadow_omega": score, "shadow_policy_fingerprint": fp,
                "divergence": canon != polarity,
                "review_required": canon != polarity or polarity != "PASS"}
    except Exception as exc:
        body = {**base, "status": "SHADOW_HOLD", "shadow_polarity": None,
                "divergence": None, "review_required": True,
                "reason": f"shadow_unavailable:{type(exc).__name__}"}
    return {**body, "comparison_hash": stable_hash(body)}


def load_pinned_scorer(archive_bytes: bytes) -> Callable[..., Any]:
    """Load only the exact reviewed archive; requires optional NumPy.

    Pinned code is executed in the calling Python process. Use only in a
    disposable test runtime, never in the production API process.
    """
    if not isinstance(archive_bytes, bytes) or len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("invalid archive size/type")
    if sha256(archive_bytes).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("MVKit archive differs from audited digest")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        files = archive.namelist()
        if len(files) != len(set(files)) or any(
            name.startswith("/") or "\\" in name or ".." in name.split("/") for name in files
        ):
            raise ValueError("unsafe or duplicate ZIP member")
        name = "mvkit/omega_gate.py"
        info = archive.getinfo(name)
        if info.file_size > MAX_MEMBER_BYTES:
            raise ValueError("oversized scorer module")
        source = archive.read(name)
    if sha256(source).hexdigest() != OMEGA_SOURCE_SHA256:
        raise ValueError("scorer module differs from audited digest")
    module_name = "_matverse_pinned_mvkit_v2_omega_shadow"
    module = ModuleType(module_name)
    module.__file__ = "<pinned-mvkit-omega>"
    sys.modules[module_name] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        return module.decide
    except BaseException:
        sys.modules.pop(module_name, None)
        raise


def compare_archive(*, archive_bytes: bytes, canonical_decision: Decision,
                    samples: Sequence[float], theta_hat: float, cvar: float,
                    pole: bool, seed: int = 0) -> dict[str, Any]:
    """Only call in a disposable runner, never on a request path."""
    try:
        scorer = load_pinned_scorer(archive_bytes)
    except Exception:
        scorer = None
    return compare_shadow(canonical_decision=canonical_decision, samples=samples,
                          theta_hat=theta_hat, cvar=cvar, pole=pole,
                          evaluator=scorer, source_digest=sha256(archive_bytes).hexdigest(),
                          seed=seed)
