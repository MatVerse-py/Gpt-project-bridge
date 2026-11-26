# Ingress ontology and transition contracts v1

## Purpose

POST /bridge/ingress must not infer ontology or transition validity from hardcoded booleans. The endpoint resolves immutable artifacts from the contract registry and records an admissibility decision. It does not execute the requested action.

## Registry artifacts

Register both artifacts through POST /registry/contracts using an authenticated principal with registry:write.

Ontology artifact (kind=ontology):

```json
{
  "ontology_id": "matverse.bridge.ingress",
  "version": "1",
  "allowed_actions": ["READ", "COMMIT", "EXECUTE", "EXPORT", "PUBLISH"]
}
```

Transition artifact (kind=transition):

```json
{
  "transition_id": "matverse.bridge.ingress.transitions",
  "version": "1",
  "transitions": [
    {"action": "EXECUTE", "from_state": "AUTHORIZED", "to_state": "EXECUTION_REQUESTED"}
  ]
}
```

The artifact hash is SHA-256 over the canonical JSON content used by the registry. Artifacts are immutable by content hash. Unknown or missing artifacts produce HOLD; malformed, wrong-kind, tampered or incompatible artifacts produce BLOCK.

## Ingress request

The request must include ontology_hash, transition_hash, source_state and target_state. The authenticated principal is the only source of signature validity; caller-supplied validity booleans are not accepted.

PASS means that the ontology allows the action and the declared action/state edge exists in the registered transition contract, subject to HDB and authenticated-principal checks. HOLD means a required artifact or state assertion is unresolved. BLOCK means a known contract violation exists.

## Epistemic boundary

The source and target states in this ingress envelope are declarations supplied by the caller. This validator checks that the declared edge exists in the registered contract; it does not prove that the entity currently occupies the declared source state, does not mutate canonical state, and does not execute the requested action. A future stateful executor must independently read canonical state and perform an atomic compare-and-set before applying an effect.

## Compatibility and rollout

The new request fields are optional at schema level to permit a controlled migration, but omission results in HOLD. Existing clients must register ontology and transition artifacts, then send their hashes and declared state edge. The response retains its existing fields and adds contract_validation. executed remains false for ingress.

This change is scoped to /bridge/ingress. Model-session creation and model-handoff gate call sites are separate follow-up work and must not be represented as fixed by this change.
