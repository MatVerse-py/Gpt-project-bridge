from __future__ import annotations

import uuid
from typing import Any, Mapping

EXPERIMENT_ID = "QEX-SUBSTRATE-01"
SCHEMA_VERSION = "matverse.iaquantumchain.experiment.v1"
ALLOWED_PAYLOAD_KEYS = frozenset({"bit"})


class InvalidExperiment(ValueError):
    pass


def validate_submission(
    *, experiment_id: str,
    payload: Mapping[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """Validate the only experiment exposed by Core v0.1.

    This is deliberately not a general-purpose job runner. No shell command,
    file path, image, Python source, or arbitrary Qiskit circuit is accepted.
    """
    if experiment_id != EXPERIMENT_ID:
        raise InvalidExperiment(f"unsupported experiment_id: {experiment_idr}")
    if not isinstance(payload, Mapping) or set(payload.keys()) != ALLOWED_PAYLOAD_KEYS:
        raise InvalidExperiment("payload must contain exactly one field: bit")
    bit = payload.get("bit")
    if type(bit) is not int or bit not in (0, 1):
        raise InvalidExperiment("payload.bit must be integer 0 or 1")
    if not isinstance(request_id, str) or not request_id.strip():
        raise InvalidExperiment("request_id required")
    try:
        normalized_request_id = str(uuid.UUID(request_id))
    except (ValueError, AttributeError) as exc:
        raise InvalidExperiment("request_id must be a UUID") from exc
    return {
        "schema": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "request_id": normalized_request_id,
        "payload": {"bit": bit},
    }
