from __future__ import annotations

PROTOCOL_VERSION = "matverse.institutional-http.v1"
AUTH_METHOD = "HMAC-SHA256"
RUNTIME_SCHEMA_VERSION = "matverse.institutional-runtime.v1"

# This is an acceptance vocabulary only. Membership in this set authorizes
# neither execution nor maturity promotion; institutional_service persists an
# authenticated commitment and leaves execution at HOLD/PENDING_EVALUATION.
INTENT_OPERATIONS = frozenset(
    {
        "REGISTER_ARTIFACT",
        "REGISTER_CLAIM",
        "REGISTER_EVIDENCE",
        "VALIDATE_EVIDENCE",
        "REGISTER_RELATION",
        "REGISTER_ACTOR",
        "ASSIGN_AUTHORITY",
        "REGISTER_METRIC",
        "REGISTER_INVENTORY_ITEM",
        "REGISTER_GAP",
        "EVALUATE_MATURITY_GATE",
        "VERIFY_IDENTIFIER",
        "REGISTER_TWIN_FINDING",
        "REGISTER_COUNTEREXAMPLE",
        "REQUEST_REPRODUCTION",
        "REQUEST_POLICY_REVIEW",
        "REQUEST_REPLAY_CHECK",
        "REQUEST_REGRESSION_FORMALIZATION",
        "REQUEST_BENCHMARK",
        "RESOLVE_FINDING",
        # Existing canonical request names preserved for backward compatibility.
        "REQUEST_MATURITY_EVALUATION",
        "REQUEST_EXTERNAL_REPRODUCTION",
        "REQUEST_WORLD_REAL_EVALUATION",
        "REQUEST_PUBLICATION",
        "REQUEST_ANCHOR",
        "REQUEST_AUTHORIZATION",
        "OTHER",
    }
)

TARGET_KINDS = frozenset(
    {
        "SYSTEM",
        "SUBJECT",
        "ACTOR",
        "ARTIFACT",
        "CLAIM",
        "EVIDENCE",
        "EXPERIMENT",
        "RELATION",
        "METRIC",
        "GAP",
        "TWIN",
        "LIVING_UNIT",
        "MATURITY",
        "OTHER",
    }
)
