"""Contextual resolution of overloaded PQC acronym (advisory text only).

No scientific claim, source, or authority is validated by this classifier.
"""
from __future__ import annotations

import re

_CIRCUIT = (
    "parameterized quantum circuit", "parameterised quantum circuit",
    "parametrized quantum circuit", "parametrised quantum circuit",
    "variational quantum circuit",
)
_CRYPTO = (
    "post-quantum cryptography", "post quantum cryptography",
    "post-quantum cryptographic", "post-quantum signature",
    "post-quantum key", "ml-kem", "ml-dsa", "slh-dsa",
    "dilithium", "kyber",
)


def resolve_pqc(text: str) -> str:
    """Expand PQC only when supported by explicit context."""
    if not isinstance(text, str):
        raise ValueError("PQC context must be text")
    words = text.lower()
    circuit = any(x in words for x in _CIRCUIT)
    cryptography = any(x in words for x in _CRYPTO)
    if circuit and cryptography:
        return "CONFLICTING_EXPANSIONS_REVIEW"
    if circuit:
        return "PARAMETERIZED_QUANTUM_CIRCUIT"
    if cryptography:
        return "POST_QUANTUM_CRYPTOGRAPHY"
    return "AMBIGUOUS_PQC" if re.search(r"\bpqc\b", words) else "NOT_PRESENT"


def candidate_qtn_relations(
    title: str, summary: str, url: str, qtn_rules: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """Keep topic matches, but never treat bare PQC as cryptographic evidence.

    URL is included for compatibility with legacy QTN mapping. Acronym context
    is checked only against title and summary: a URL path is not its expansion.
    """
    semantic = f"{title} {summary}"
    haystack = f"{semantic} {url}".lower()
    meaning = resolve_pqc(semantic)
    relations = []
    for qtn, terms in qtn_rules.items():
        matches = [term for term in terms if term in haystack]
        if qtn in ("QTN-007", "QTN-011") and meaning != "POST_QUANTUM_CRYPTOGRAPHY":
            matches = [term for term in matches if term != "pqc"]
        if matches:
            relations.append(qtn)
    return tuple(relations)
