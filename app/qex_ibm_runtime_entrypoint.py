from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Callable

from qiskit_ibm_runtime import QiskitRuntimeService

from app.core import Decision
from app.qex_experiment import qex_substrate_01_contract
from app.qex_live_calibration_adapter import _backend_name, _is_nonlive_backend
from app.qex_physical_qpu_adapter import (
    PhysicalExecutionAuthorization,
    PhysicalQPUAdapter,
    PhysicalQPUExecution,
    PhysicalQPUPreparation,
    prepare_physical_qpu,
)


@dataclass(frozen=True)
class RuntimeBackendResolution:
    decision: Decision
    reason: str
    backend: Any | None = None
    backend_name: str | None = None
    active_instance: str | None = None


@dataclass(frozen=True)
class RuntimePhysicalOutcome:
    decision: Decision
    stage: str
    reason: str
    backend_name: str | None = None
    active_instance: str | None = None
    preparation: PhysicalQPUPreparation | None = None
    execution: PhysicalQPUExecution | None = None


def resolve_ibm_runtime_backend(
    *,
    backend_name: str | None = None,
    token: str | None = None,
    instance: str | None = None,
    channel: str = "ibm_quantum_platform",
    service_factory: Callable[..., Any] | None = None,
) -> RuntimeBackendResolution:
    """Resolve one authenticated physical backend without ever persisting secrets.

    If ``token`` is omitted, QiskitRuntimeService is allowed to load its saved
    default account. Any authentication, service, backend, status, or simulator
    problem becomes HOLD. This function never submits a quantum job.
    """
    factory = service_factory or QiskitRuntimeService
    kwargs: dict[str, Any] = {"channel": channel}
    if token:
        kwargs["token"] = token
    if instance:
        kwargs["instance"] = instance

    try:
        service = factory(**kwargs)
    except Exception as exc:
        return RuntimeBackendResolution(
            Decision.HOLD,
            f"IBM Runtime authentication/service initialization failed: {exc.__class__.__name__}",
        )

    try:
        if backend_name:
            backend = service.backend(name=backend_name, instance=instance) if instance else service.backend(name=backend_name)
        else:
            backend = service.least_busy(min_num_qubits=1, operational=True, simulator=False)
    except Exception as exc:
        return RuntimeBackendResolution(
            Decision.HOLD,
            f"IBM Runtime physical backend resolution failed: {exc.__class__.__name__}",
            active_instance=_safe_active_instance(service),
        )

    resolved_name = _backend_name(backend)
    if _is_nonlive_backend(backend):
        return RuntimeBackendResolution(
            Decision.HOLD,
            "resolved backend is simulator/fake and cannot satisfy PHYSICAL_QPU_EXECUTION",
            backend_name=resolved_name,
            active_instance=_safe_active_instance(service),
        )

    status_method = getattr(backend, "status", None)
    if not callable(status_method):
        return RuntimeBackendResolution(
            Decision.HOLD,
            "resolved backend does not expose status()",
            backend_name=resolved_name,
            active_instance=_safe_active_instance(service),
        )
    try:
        status = status_method()
    except Exception as exc:
        return RuntimeBackendResolution(
            Decision.HOLD,
            f"resolved backend status query failed: {exc.__class__.__name__}",
            backend_name=resolved_name,
            active_instance=_safe_active_instance(service),
        )
    if getattr(status, "operational", None) is not True:
        return RuntimeBackendResolution(
            Decision.HOLD,
            "resolved physical backend is not operational",
            backend_name=resolved_name,
            active_instance=_safe_active_instance(service),
        )

    return RuntimeBackendResolution(
        Decision.PASS,
        "authenticated operational physical backend resolved",
        backend=backend,
        backend_name=resolved_name,
        active_instance=_safe_active_instance(service),
    )


def run_physical_qpu_from_runtime(
    *,
    authority: str,
    purpose: str,
    shots: int,
    execute: bool,
    allow_resource_consumption: bool,
    backend_name: str | None = None,
    token: str | None = None,
    instance: str | None = None,
    channel: str = "ibm_quantum_platform",
    bit: int = 0,
    service_factory: Callable[..., Any] | None = None,
    compiler_factory: Callable[[Any], Any] | None = None,
    sampler_factory: Callable[[Any], Any] | None = None,
) -> RuntimePhysicalOutcome:
    """Resolve, prepare and optionally execute QEX-SUBSTRATE-01 on a QPU.

    Real resource consumption requires BOTH ``execute`` and
    ``allow_resource_consumption``. Omitting either produces HOLD and no job
    submission. The token is accepted only as an in-memory argument so callers
    can source it from an environment or secret store without persistence.
    """
    resolution = resolve_ibm_runtime_backend(
        backend_name=backend_name,
        token=token,
        instance=instance,
        channel=channel,
        service_factory=service_factory,
    )
    if resolution.decision is not Decision.PASS or resolution.backend is None:
        return RuntimePhysicalOutcome(
            resolution.decision,
            "BACKEND_RESOLUTION",
            resolution.reason,
            backend_name=resolution.backend_name,
            active_instance=resolution.active_instance,
        )

    authorization = PhysicalExecutionAuthorization(
        authorized=execute,
        authority=authority,
        purpose=purpose,
        max_shots=shots,
        allow_resource_consumption=allow_resource_consumption,
    )
    preparation = prepare_physical_qpu(resolution.backend, authorization)
    if preparation.decision is not Decision.PASS:
        return RuntimePhysicalOutcome(
            preparation.decision,
            "PHYSICAL_PREPARATION",
            preparation.reason,
            backend_name=resolution.backend_name,
            active_instance=resolution.active_instance,
            preparation=preparation,
        )

    if not execute or not allow_resource_consumption:
        return RuntimePhysicalOutcome(
            Decision.HOLD,
            "RESOURCE_CONSUMPTION_GATE",
            "physical execution requires --execute and --allow-resource-consumption",
            backend_name=resolution.backend_name,
            active_instance=resolution.active_instance,
            preparation=preparation,
        )

    adapter = PhysicalQPUAdapter(
        preparation,
        shots=shots,
        compiler_factory=compiler_factory,
        sampler_factory=sampler_factory,
    )
    execution = adapter.execute(qex_substrate_01_contract(), {"bit": bit})
    return RuntimePhysicalOutcome(
        Decision.PASS,
        "PHYSICAL_RESULT_OBSERVED",
        "physical provider job completed and evidence was captured",
        backend_name=resolution.backend_name,
        active_instance=resolution.active_instance,
        preparation=preparation,
        execution=execution,
    )


def outcome_to_public_dict(outcome: RuntimePhysicalOutcome) -> dict[str, Any]:
    """Serialize only non-secret execution/evidence metadata."""
    payload: dict[str, Any] = {
        "decision": outcome.decision.value,
        "stage": outcome.stage,
        "reason": outcome.reason,
        "backend_name": outcome.backend_name,
        "active_instance": outcome.active_instance,
    }
    if outcome.preparation is not None:
        payload["preparation"] = {
            "decision": outcome.preparation.decision.value,
            "reason": outcome.preparation.reason,
            "backend_name": outcome.preparation.backend_name,
            "calibration_id": outcome.preparation.calibration_id,
            "calibration_snapshot_hash": outcome.preparation.calibration_snapshot_hash,
            "properties_last_update": outcome.preparation.properties_last_update,
            "pending_jobs": outcome.preparation.pending_jobs,
            "authorization_hash": outcome.preparation.authorization_hash,
            "max_shots": outcome.preparation.max_shots,
        }
    if outcome.execution is not None:
        payload["execution"] = {
            "job_id": outcome.execution.job_id,
            "raw_counts": dict(outcome.execution.raw_counts),
            "raw_counts_hash": outcome.execution.raw_counts_hash,
            "isa_circuit_hash": outcome.execution.isa_circuit_hash,
            "job_metrics_hash": outcome.execution.job_metrics_hash,
            "usage_estimation_hash": outcome.execution.usage_estimation_hash,
            "receipt_hash": outcome.execution.execution.receipt["receipt_hash"],
            "contract_hash": outcome.execution.execution.contract_hash,
        }
    return payload


def _safe_active_instance(service: Any) -> str | None:
    method = getattr(service, "active_instance", None)
    if not callable(method):
        return None
    try:
        value = method()
    except Exception:
        return None
    return str(value) if value is not None else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Governed QEX-SUBSTRATE-01 IBM physical-QPU runner")
    parser.add_argument("--backend", default=os.getenv("IBM_QUANTUM_BACKEND"))
    parser.add_argument("--instance", default=os.getenv("IBM_QUANTUM_INSTANCE"))
    parser.add_argument("--channel", default=os.getenv("IBM_QUANTUM_CHANNEL", "ibm_quantum_platform"))
    parser.add_argument("--shots", type=int, default=128)
    parser.add_argument("--bit", type=int, choices=(0, 1), default=0)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-resource-consumption", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    outcome = run_physical_qpu_from_runtime(
        authority=args.authority,
        purpose=args.purpose,
        shots=args.shots,
        bit=args.bit,
        execute=args.execute,
        allow_resource_consumption=args.allow_resource_consumption,
        backend_name=args.backend,
        token=os.getenv("IBM_QUANTUM_API_KEY"),
        instance=args.instance,
        channel=args.channel,
    )
    print(json.dumps(outcome_to_public_dict(outcome), sort_keys=True, indent=2))
    return 0 if outcome.decision is Decision.PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
