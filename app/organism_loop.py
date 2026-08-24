from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import re
from dataclasses import asdict, dataclass
from types import CodeType
from typing import Any, Mapping, Sequence

from . import core as core_module
from .core import Decision, evaluate_hdb, omega_gate, stable_hash
from .evidence import canonical_json, evidence_receipt

SCHEMA_VERSION = "matverse.organism-loop.v1"
BINDING_VERSION = "matverse.constitutional-binding.v1"
AUTH_CAPABILITY = "organism.constraint.authorize"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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
class AuthorizationGrant:
    principal_id: str
    capability: str
    candidate_id: str
    signature: str


@dataclass(frozen=True)
class InheritedConstraint:
    constraint_id: str
    candidate_id: str
    source_event_id: str
    source_receipt_hash: str
    generator_id: str
    authorizer_id: str
    authorization_capability: str
    authorization_signature: str
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


def _normalize_code_const(value: Any) -> Any:
    if isinstance(value, CodeType):
        return {
            "co_code": value.co_code.hex(),
            "co_consts": [_normalize_code_const(item) for item in value.co_consts],
            "co_names": list(value.co_names),
            "co_varnames": list(value.co_varnames),
            "co_freevars": list(value.co_freevars),
            "co_cellvars": list(value.co_cellvars),
        }
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, tuple):
        return [_normalize_code_const(item) for item in value]
    return {"type": type(value).__qualname__}


def _function_fingerprint(fn: Any) -> str:
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError) as exc:
        code = getattr(fn, "__code__", None)
        if code is None:
            raise ValueError(f"cannot fingerprint function {fn!r}") from exc
        source = canonical_json(_normalize_code_const(code))
    return stable_hash({
        "module": getattr(fn, "__module__", ""),
        "qualname": getattr(fn, "__qualname__", getattr(fn, "__name__", "")),
        "source": source,
    })


def _core_artifact_fingerprint() -> str:
    try:
        source = inspect.getsource(core_module)
        return stable_hash({"module": core_module.__name__, "source": source})
    except (OSError, TypeError):
        return stable_hash({
            "module": core_module.__name__,
            "decision_values": [member.value for member in Decision],
            "blocked_actions": sorted(core_module.BLOCKED_ACTIONS),
            "evaluate_hdb": _function_fingerprint(evaluate_hdb),
            "omega_gate": _function_fingerprint(omega_gate),
            "stable_hash": _function_fingerprint(stable_hash),
        })


def canonical_guard_bindings() -> tuple[GuardBinding, ...]:
    core_fingerprint = _core_artifact_fingerprint()
    return (
        GuardBinding(
            guard_id="HDB_BOUNDARY",
            function_name="app.core.evaluate_hdb",
            function_fingerprint=stable_hash({"core_artifact": core_fingerprint, "function": _function_fingerprint(evaluate_hdb)}),
            semantics="human-data boundary is evaluated before admissibility",
        ),
        GuardBinding(
            guard_id="OMEGA_ADMISSIBILITY",
            function_name="app.core.omega_gate",
            function_fingerprint=stable_hash({"core_artifact": core_fingerprint, "function": _function_fingerprint(omega_gate)}),
            semantics="ontology, HDB, signature, and transition checks gate critical actions",
        ),
    )


def gate_fingerprint(bindings: Sequence[GuardBinding] | None = None) -> str:
    chosen = canonical_guard_bindings() if bindings is None else tuple(bindings)
    if not chosen:
        raise ValueError("at least one production guard binding is required")
    ids = [item.guard_id for item in chosen]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate guard_id")
    return stable_hash({"binding_version": BINDING_VERSION, "guards": [asdict(item) for item in chosen]})


def constitutional_contract_hash(*, frozen_contract_hash: str, bindings: Sequence[GuardBinding] | None = None) -> str:
    if _SHA256_RE.fullmatch(frozen_contract_hash) is None:
        raise ValueError("frozen_contract_hash must be exactly 64 hexadecimal characters")
    fingerprint = gate_fingerprint(bindings)
    return stable_hash({
        "schema": BINDING_VERSION,
        "frozen_contract_hash": frozen_contract_hash.lower(),
        "gate_fingerprint": fingerprint,
    })


def _matches(match_json: str, proposal: Mapping[str, Any]) -> bool:
    match = json.loads(match_json)
    return bool(match) and all(key in proposal and proposal[key] == value for key, value in match.items())


def _grant_payload(principal_id: str, capability: str, candidate_id: str) -> bytes:
    return "\n".join([principal_id, capability, candidate_id]).encode("utf-8")


def sign_authorization_grant(*, secret: str, principal_id: str, candidate_id: str, capability: str = AUTH_CAPABILITY) -> AuthorizationGrant:
    if not secret or not principal_id or not candidate_id:
        raise ValueError("secret, principal_id, and candidate_id are required")
    signature = hmac.new(secret.encode("utf-8"), _grant_payload(principal_id, capability, candidate_id), hashlib.sha256).hexdigest()
    return AuthorizationGrant(principal_id=principal_id, capability=capability, candidate_id=candidate_id, signature=signature)


class GovernedOrganism:
    """Headless governed causal loop with authenticated external state and authority grants."""

    def __init__(
        self,
        *,
        organism_id: str,
        frozen_contract_hash: str,
        runtime_id: str,
        state_secret: str,
        authority_secrets: Mapping[str, str],
        state: Mapping[str, Any] | None = None,
    ) -> None:
        if not organism_id or not runtime_id or not state_secret:
            raise ValueError("organism_id, runtime_id, and state_secret are required")
        if not authority_secrets or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in authority_secrets.items()):
            raise ValueError("authority_secrets must contain non-empty principal secrets")
        self.organism_id = organism_id
        self.runtime_id = runtime_id
        self.frozen_contract_hash = frozen_contract_hash
        self._state_secret = state_secret
        self._authority_secrets = dict(authority_secrets)
        self.gate_fingerprint = gate_fingerprint()
        self.constitutional_contract_hash = constitutional_contract_hash(frozen_contract_hash=frozen_contract_hash)
        self._constraints: dict[str, InheritedConstraint] = {}
        self._lineage: list[dict[str, Any]] = []
        if state is not None:
            self._restore(state)

    def _state_mac(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(self._state_secret.encode("utf-8"), canonical_json(dict(payload)).encode("utf-8"), hashlib.sha256).hexdigest()

    def _restore(self, state: Mapping[str, Any]) -> None:
        raw = _json_clone(dict(state))
        supplied_mac = raw.pop("state_mac", None)
        supplied_root = raw.pop("state_root", None)
        if not isinstance(supplied_mac, str) or not hmac.compare_digest(self._state_mac(raw), supplied_mac):
            raise ValueError("state authentication failed")
        if raw.get("schema") != SCHEMA_VERSION:
            raise ValueError("unsupported organism state schema")
        if raw.get("organism_id") != self.organism_id:
            raise ValueError("organism identity mismatch")
        if raw.get("constitutional_contract_hash") != self.constitutional_contract_hash:
            raise ValueError("constitutional contract mismatch")
        constraints = raw.get("constraints", [])
        if not isinstance(constraints, list):
            raise ValueError("constraints must be a list")
        for item_raw in constraints:
            item = InheritedConstraint(**item_raw)
            self._validate_constraint(item)
            self._constraints[item.constraint_id] = item
        lineage = raw.get("lineage", [])
        if not isinstance(lineage, list):
            raise ValueError("lineage must be a list")
        self._lineage = _json_clone(lineage)
        self._validate_lineage()
        if supplied_root != self.state_root():
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
        return {**payload, "state_root": stable_hash(payload), "state_mac": self._state_mac(payload)}

    def _evaluation(self, event_id: str) -> dict[str, Any]:
        matches = [item for item in self._lineage if item.get("type") == "EVALUATION" and item.get("event_id") == event_id]
        if len(matches) != 1:
            raise ValueError("source event must identify exactly one prior evaluation")
        return _json_clone(matches[0])

    def observe_rejection(self, *, event_id: str, generator_id: str, causal_keys: Sequence[str]) -> ConstraintCandidate:
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

    def _verify_grant(self, grant: AuthorizationGrant, candidate_id: str) -> None:
        if grant.capability != AUTH_CAPABILITY or grant.candidate_id != candidate_id:
            raise PermissionError("authorization grant does not cover this candidate")
        secret = self._authority_secrets.get(grant.principal_id)
        if secret is None:
            raise PermissionError("unknown authorization principal")
        expected = hmac.new(secret.encode("utf-8"), _grant_payload(grant.principal_id, grant.capability, candidate_id), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, grant.signature):
            raise PermissionError("invalid authorization grant signature")

    def _validate_constraint(self, constraint: InheritedConstraint) -> None:
        self._verify_grant(
            AuthorizationGrant(
                principal_id=constraint.authorizer_id,
                capability=constraint.authorization_capability,
                candidate_id=constraint.candidate_id,
                signature=constraint.authorization_signature,
            ),
            constraint.candidate_id,
        )
        core = {
            "candidate_id": constraint.candidate_id,
            "source_event_id": constraint.source_event_id,
            "source_receipt_hash": constraint.source_receipt_hash,
            "generator_id": constraint.generator_id,
            "authorizer_id": constraint.authorizer_id,
            "authorization_capability": constraint.authorization_capability,
            "authorization_signature": constraint.authorization_signature,
            "match_json": constraint.match_json,
            "reason": constraint.reason,
            "constitutional_contract_hash": self.constitutional_contract_hash,
        }
        expected_receipt = evidence_receipt("CONSTRAINT_AUTHORIZATION", core, {"decision": "PROMOTE"})["receipt_hash"]
        if constraint.authority_receipt != expected_receipt:
            raise ValueError("constraint authority receipt mismatch")
        expected_id = stable_hash({**core, "authority_receipt": expected_receipt})
        if constraint.constraint_id != expected_id:
            raise ValueError("constraint id mismatch")
        if constraint.authorizer_id == constraint.generator_id:
            raise ValueError("constraint violates generator/authorizer separation")

    def authorize_constraint(self, candidate: ConstraintCandidate, *, grant: AuthorizationGrant) -> InheritedConstraint:
        self._verify_grant(grant, candidate.candidate_id)
        if grant.principal_id == candidate.generator_id:
            raise PermissionError("generator cannot authorize its own inherited constraint")
        source = self._evaluation(candidate.source_event_id)
        if source["decision"] != Decision.BLOCK.value or source["receipt_hash"] != candidate.source_receipt_hash:
            raise ValueError("candidate is not bound to a verified local rejection")
        core = {
            "candidate_id": candidate.candidate_id,
            "source_event_id": candidate.source_event_id,
            "source_receipt_hash": candidate.source_receipt_hash,
            "generator_id": candidate.generator_id,
            "authorizer_id": grant.principal_id,
            "authorization_capability": grant.capability,
            "authorization_signature": grant.signature,
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
            authorizer_id=grant.principal_id,
            authorization_capability=grant.capability,
            authorization_signature=grant.signature,
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
            "authorizer_id": grant.principal_id,
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
        human_copy = None if human is None else _json_clone(dict(human))
        matched = next(
            (self._constraints[key] for key in sorted(self._constraints) if _matches(self._constraints[key].match_json, proposal_copy)),
            None,
        )
        if matched is not None:
            decision, reason = Decision.BLOCK, f"inherited constraint: {matched.reason}"
            matched_id = matched.constraint_id
        else:
            hdb = evaluate_hdb(human_copy)
            decision, reason = omega_gate(
                hdb=hdb,
                action=str(proposal_copy.get("action", "")),
                ontology_ok=ontology_ok,
                signature_valid=signature_valid,
                transition_valid=transition_valid,
            )
            matched_id = None
        before = self.state_root()
        gate_inputs = {
            "human": human_copy,
            "ontology_ok": bool(ontology_ok),
            "signature_valid": bool(signature_valid),
            "transition_valid": bool(transition_valid),
        }
        event_core = {
            "event_id": event_id,
            "runtime_id": self.runtime_id,
            "proposal": proposal_copy,
            "gate_inputs": gate_inputs,
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
            "gate_inputs": gate_inputs,
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
