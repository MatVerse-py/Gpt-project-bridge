from __future__ import annotations

import inspect
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .core import Decision, evaluate_hdb, omega_gate, stable_hash
from .evidence import canonical_json, evidence_receipt

SCHEMA_VERSION = "matverse.organism-loop.v1"
BINDING_VERSION = "matverse.constitutional-binding.v1"


@dataclass(frozen=True)
class GuardBinding:
    guard_id: str
    function_name: str
    function_fingerprint: str
    semantics: str


@dataclass(frozen=True)
class ConstraintCandidate:
    candidate_id: str
    source_event_id: str
    source_receipt_hash: str
    generator_id: str
    match_json: str
    reason: str

    @property
    def match(self) -> dict[str, Any]:
        return json.loads(self.match_json)


@dataclass(frozen=True)
class InheritedConstraint:
    constraint_id: str
    candidate_id: str
    source_event_id: str
    source_receipt_hash: str
    generator_id: str
    authorizer_id: str
    match_json: str
    reason: str
    authority_receipt: str

    @property
    def match(self) -> dict[str, Any]:
        return json.loads(self.match_json)


@dataclass(frozen=True)
class LoopResult:
    event_id: str
    decision: Decision
    reason: str
    matched_constraint_id: str | None
    state_root: str
    evidence: Mapping[str, Any]


def _json_clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _function_fingerprint(fn: Any) -> str:
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        code = getattr(fn, "__code__", None)
        if code is None:
            raise ValueError(f"cannot fingerprint function {fn!r}")
        source = repr((code.co_code.hex(), code.co_consts, code.co_names))
    return stable_hash({
        "module": getattr(fn, "__module__", ""),
        "qualname": getattr(fn, "__qualname__", getattr(fn, "__name__", "")),
        "source": source,
    })


def canonical_guard_bindings() -> tuple[GuardBinding, ...]:
    return (
        GuardBinding(
            guard_id="HDB_BOUNDARY",
            function_name="app.core.evaluate_hdb",
            function_fingerprint=_function_fingerprint(evaluate_hdb),
            semantics="human-data boundary is evaluated before admissibility",
        ),
        GuardBinding(
            guard_id="OMEGA_ADMISSIBILITY",
            function_name="app.core.omega_gate",
            function_fingerprint=_function_fingerprint(omega_gate),
            semantics="ontology, HDB, signature, and transition checks gate critical actions",
        ),
    )


def gate_fingerprint(bindings: Sequence[GuardBinding] | None = None) -> str:
    chosen = tuple(bindings or canonical_guard_bindings())
    if not chosen:
        raise ValueError("at least one production guard binding is required")
    ids = [item.guard_id for item in chosen]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate guard_id")
    return stable_hash({"binding_version": BINDING_VERSION, "guards": [asdict(item) for item in chosen]})


def constitutional_contract_hash(*, frozen_contract_hash: str, bindings: Sequence[GuardBinding] | None = None) -> str:
    if len(frozen_contract_hash) != 64:
        raise ValueError("frozen_contract_hash must be a SHA-256 hex digest")
    try:
        int(frozen_contract_hash, 16)
    except ValueError as exc:
        raise ValueError("frozen_contract_hash must be a SHA-256 hex digest") from exc
    fingerprint = gate_fingerprint(bindings)
    return stable_hash({
        "schema": BINDING_VERSION,
        "frozen_contract_hash": frozen_contract_hash,
        "gate_fingerprint": fingerprint,
    })


def _matches(match_json: str, proposal: Mapping[str, Any]) -> bool:
    match = json.loads(match_json)
    return bool(match) and all(proposal.get(key) == value for key, value in match.items())


class GovernedOrganism:
    """Headless governed causal loop with externalizable state and receipts."""

    def __init__(self, *, organism_id: str, frozen_contract_hash: str, runtime_id: str, state: Mapping[str, Any] | None = None) -> None:
        if not organism_id or not runtime_id:
            raise ValueError("organism_id and runtime_id are required")
        self.organism_id = organism_id
        self.runtime_id = runtime_id
        self.frozen_contract_hash = frozen_contract_hash
        self.gate_fingerprint = gate_fingerprint()
        self.constitutional_contract_hash = constitutional_contract_hash(frozen_contract_hash=frozen_contract_hash)
        self._constraints: dict[str, InheritedConstraint] = {}
        self._lineage: list[dict[str, Any]] = []
        if state is not None:
            self._restore(state)

    def _restore(self, state: Mapping[str, Any]) -> None:
        if state.get("schema") != SCHEMA_VERSION:
            raise ValueError("unsupported organism state schema")
        if state.get("organism_id") != self.organism_id:
            raise ValueError("organism identity mismatch")
        if state.get("constitutional_contract_hash") != self.constitutional_contract_hash:
            raise ValueError("constitutional contract mismatch")
        constraints = state.get("constraints", [])
        if not isinstance(constraints, list):
            raise ValueError("constraints must be a list")
        for raw in constraints:
            item = InheritedConstraint(**raw)
            self._validate_constraint(item)
            self._constraints[item.constraint_id] = item
        lineage = state.get("lineage", [])
        if not isinstance(lineage, list):
            raise ValueError("lineage must be a list")
        self._lineage = _json_clone(lineage)
        self._validate_lineage()
        expected_root = state.get("state_root")
        if expected_root != self.state_root():
            raise ValueError("state root mismatch")

    def _validate_lineage(self) -> None:
        event_ids: set[str] = set()
        for item in self._lineage:
            if item.get("type") != "EVALUATION":
                continue
            event_id = item.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError("invalid evaluation event id in lineage")
            if event_id in event_ids:
                raise ValueError("duplicate evaluation event id in lineage")
            event_ids.add(event_id)
            if item.get("decision") not in {member.value for member in Decision}:
                raise ValueError("invalid evaluation decision in lineage")
            if not isinstance(item.get("proposal"), dict):
                raise ValueError("evaluation proposal missing from lineage")
            if not isinstance(item.get("receipt_hash"), str):
                raise ValueError("evaluation receipt hash missing from lineage")

    def state_payload(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "organism_id": self.organism_id,
            "constitutional_contract_hash": self.constitutional_contract_hash,
            "gate_fingerprint": self.gate_fingerprint,
            "constraints": [asdict(self._constraints[key]) for key in sorted(self._constraints)],
            "lineage": _json_clone(self._lineage),
        }

    def state_root(self) -> str:
        return stable_hash(self.state_payload())

    def export_state(self) -> dict[str, Any]:
        payload = self.state_payload()
        payload["state_root"] = self.state_root()
        return payload

    def _evaluation(self, event_id: str) -> dict[str, Any]:
        matches = [
            item for item in self._lineage
            if item.get("type") == "EVALUATION" and item.get("event_id") == event_id
        ]
        if len(matches) != 1:
            raise ValueError("source event must identify exactly one prior evaluation")
        return _json_clone(matches[0])

    def observe_rejection(
        self,
        *,
        event_id: str,
        generator_id: str,
        causal_keys: Sequence[str],
    ) -> ConstraintCandidate:
        if not event_id or not generator_id:
            raise ValueError("event_id and generator_id are required")
        source = self._evaluation(event_id)
        if source["decision"] != Decision.BLOCK.value:
            raise ValueError("source event is not a verified BLOCK rejection")
        if source.get("matched_constraint_id") is not None:
            raise ValueError("an inherited BLOCK cannot recursively seed another constraint")
        proposal = source["proposal"]
        match = {key: proposal[key] for key in causal_keys if key in proposal}
        if not match:
            raise ValueError("causal attribution produced an empty match")
        match_json = canonical_json(match)
        reason = str(source["reason"])
        source_receipt_hash = str(source["receipt_hash"])
        candidate_id = stable_hash({
            "schema": SCHEMA_VERSION,
            "source_event_id": event_id,
            "source_receipt_hash": source_receipt_hash,
            "generator_id": generator_id,
            "match_json": match_json,
            "reason": reason,
        })
        return ConstraintCandidate(candidate_id, event_id, source_receipt_hash, generator_id, match_json, reason)

    def _validate_constraint(self, constraint: InheritedConstraint) -> None:
        core = {
            "candidate_id": constraint.candidate_id,
            "source_event_id": constraint.source_event_id,
            "source_receipt_hash": constraint.source_receipt_hash,
            "generator_id": constraint.generator_id,
            "authorizer_id": constraint.authorizer_id,
            "match_json": constraint.match_json,
            "reason": constraint.reason,
            "constitutional_contract_hash": self.constitutional_contract_hash,
        }
        expected_receipt = evidence_receipt(
            "CONSTRAINT_AUTHORIZATION", core, {"decision": "PROMOTE"}
        )["receipt_hash"]
        if constraint.authority_receipt != expected_receipt:
            raise ValueError("constraint authority receipt mismatch")
        expected_id = stable_hash({**core, "authority_receipt": expected_receipt})
        if constraint.constraint_id != expected_id:
            raise ValueError("constraint id mismatch")
        if constraint.authorizer_id == constraint.generator_id:
            raise ValueError("constraint violates generator/authorizer separation")

    def authorize_constraint(self, candidate: ConstraintCandidate, *, authorizer_id: str) -> InheritedConstraint:
        if not authorizer_id:
            raise ValueError("authorizer_id is required")
        if authorizer_id == candidate.generator_id:
            raise PermissionError("generator cannot authorize its own inherited constraint")
        source = self._evaluation(candidate.source_event_id)
        if source["decision"] != Decision.BLOCK.value or source["receipt_hash"] != candidate.source_receipt_hash:
            raise ValueError("candidate is not bound to a verified local rejection")
        core = {
            "candidate_id": candidate.candidate_id,
            "source_event_id": candidate.source_event_id,
            "source_receipt_hash": candidate.source_receipt_hash,
            "generator_id": candidate.generator_id,
            "authorizer_id": authorizer_id,
            "match_json": candidate.match_json,
            "reason": candidate.reason,
            "constitutional_contract_hash": self.constitutional_contract_hash,
        }
        authority_receipt = evidence_receipt("CONSTRAINT_AUTHORIZATION", core, {"decision": "PROMOTE"})["receipt_hash"]
        constraint_id = stable_hash({**core, "authority_receipt": authority_receipt})
        constraint = InheritedConstraint(
            constraint_id=constraint_id,
            candidate_id=candidate.candidate_id,
            source_event_id=candidate.source_event_id,
            source_receipt_hash=candidate.source_receipt_hash,
            generator_id=candidate.generator_id,
            authorizer_id=authorizer_id,
            match_json=candidate.match_json,
            reason=candidate.reason,
            authority_receipt=authority_receipt,
        )
        self._constraints[constraint.constraint_id] = constraint
        self._lineage.append({
            "type": "CONSTRAINT_PROMOTED",
            "source_event_id": candidate.source_event_id,
            "source_receipt_hash": candidate.source_receipt_hash,
            "constraint_id": constraint.constraint_id,
            "authorizer_id": authorizer_id,
        })
        return constraint

    def evaluate(
        self,
        *,
        event_id: str,
        proposal: Mapping[str, Any],
        human: Mapping[str, Any] | None = None,
        ontology_ok: bool = True,
        signature_valid: bool = True,
        transition_valid: bool = True,
    ) -> LoopResult:
        if not event_id:
            raise ValueError("event_id is required")
        if any(item.get("type") == "EVALUATION" and item.get("event_id") == event_id for item in self._lineage):
            raise ValueError("event_id must be unique")
        proposal_copy = _json_clone(dict(proposal))
        matched = next((item for item in self._constraints.values() if _matches(item.match_json, proposal_copy)), None)
        if matched is not None:
            decision, reason = Decision.BLOCK, f"inherited constraint: {matched.reason}"
            matched_id = matched.constraint_id
        else:
            hdb = evaluate_hdb(dict(human) if human is not None else None)
            decision, reason = omega_gate(
                hdb=hdb,
                action=str(proposal_copy.get("action", "")),
                ontology_ok=ontology_ok,
                signature_valid=signature_valid,
                transition_valid=transition_valid,
            )
            matched_id = None
        before = self.state_root()
        event_core = {
            "event_id": event_id,
            "runtime_id": self.runtime_id,
            "proposal": proposal_copy,
            "decision": decision.value,
            "reason": reason,
            "matched_constraint_id": matched_id,
            "state_root_before": before,
            "constitutional_contract_hash": self.constitutional_contract_hash,
        }
        receipt = evidence_receipt("ORGANISM_EVALUATION", event_core, {"decision": decision.value, "reason": reason})
        self._lineage.append({
            "type": "EVALUATION",
            "event_id": event_id,
            "runtime_id": self.runtime_id,
            "proposal": proposal_copy,
            "decision": decision.value,
            "reason": reason,
            "matched_constraint_id": matched_id,
            "receipt_hash": receipt["receipt_hash"],
        })
        return LoopResult(
            event_id=event_id,
            decision=decision,
            reason=reason,
            matched_constraint_id=matched_id,
            state_root=self.state_root(),
            evidence=receipt,
        )
