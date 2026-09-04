from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from .evidence import canonical_json, evidence_receipt
from .physiology import ExecutionResult, TelemetrySample

SCHEMA_VERSION = "matverse.deterministic-lab.v1"


class FaultKind(str, Enum):
    EXECUTOR_ERROR = "EXECUTOR_ERROR"
    DEGRADED_DISK = "DEGRADED_DISK"
    CRITICAL_DISK = "CRITICAL_DISK"


@dataclass(frozen=True)
class FaultDirective:
    cycle_seq: int
    kind: FaultKind


@dataclass(frozen=True)
class DeterministicFaultPlan:
    seed: int
    cycles: int
    directives: tuple[FaultDirective, ...]

    @classmethod
    def generate(
        cls,
        *,
        seed: int,
        cycles: int,
        fault_probability: float = 0.10,
        allowed_faults: Sequence[FaultKind] = tuple(FaultKind),
    ) -> "DeterministicFaultPlan":
        if cycles < 1:
            raise ValueError("cycles must be >= 1")
        if not 0.0 <= fault_probability <= 1.0:
            raise ValueError("fault_probability must be between 0 and 1")
        faults = tuple(allowed_faults)
        if not faults:
            raise ValueError("allowed_faults cannot be empty")
        rng = random.Random(seed)
        directives: list[FaultDirective] = []
        for cycle_seq in range(1, cycles + 1):
            if rng.random() < fault_probability:
                directives.append(FaultDirective(cycle_seq=cycle_seq, kind=rng.choice(faults)))
        return cls(seed=seed, cycles=cycles, directives=tuple(directives))

    def for_cycle(self, cycle_seq: int) -> tuple[FaultDirective, ...]:
        return tuple(item for item in self.directives if item.cycle_seq == cycle_seq)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "seed": self.seed,
            "cycles": self.cycles,
            "directives": [
                {"cycle_seq": item.cycle_seq, "kind": item.kind.value}
                for item in self.directives
            ],
        }

    def receipt(self) -> Mapping[str, Any]:
        payload = self.as_dict()
        return evidence_receipt("DETERMINISTIC_FAULT_PLAN", payload, {"frozen": True})


class FaultInjectingExecutor:
    """Wraps a real executor and injects deterministic executor failures by invocation index."""

    def __init__(
        self,
        delegate: Callable[[Mapping[str, Any]], ExecutionResult],
        plan: DeterministicFaultPlan,
    ) -> None:
        self.delegate = delegate
        self.plan = plan
        self.invocation = 0

    def __call__(self, proposal: Mapping[str, Any]) -> ExecutionResult:
        self.invocation += 1
        if any(item.kind is FaultKind.EXECUTOR_ERROR for item in self.plan.for_cycle(self.invocation)):
            raise RuntimeError(f"deterministic injected executor fault at invocation {self.invocation}")
        return self.delegate(proposal)


class DeterministicTelemetry:
    """A deterministic, instrument-labelled telemetry source for replayable stress tests.

    It does not masquerade as world-real telemetry: every sample is explicitly labelled
    deterministic_lab.* and should be classified as SIMULATED by higher evidence layers.
    """

    def __init__(
        self,
        *,
        plan: DeterministicFaultPlan,
        disk_total_bytes: int = 1_000_000_000,
        normal_free_ratio: float = 0.50,
        degraded_free_ratio: float = 0.075,
        critical_free_ratio: float = 0.025,
        rss_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if disk_total_bytes <= 0:
            raise ValueError("disk_total_bytes must be positive")
        for ratio in (normal_free_ratio, degraded_free_ratio, critical_free_ratio):
            if not 0.0 <= ratio <= 1.0:
                raise ValueError("disk ratios must be between 0 and 1")
        self.plan = plan
        self.disk_total_bytes = disk_total_bytes
        self.normal_free_ratio = normal_free_ratio
        self.degraded_free_ratio = degraded_free_ratio
        self.critical_free_ratio = critical_free_ratio
        self.rss_bytes = rss_bytes
        self.cycle = 0

    def sample(self) -> TelemetrySample:
        self.cycle += 1
        directives = self.plan.for_cycle(self.cycle)
        ratio = self.normal_free_ratio
        if any(item.kind is FaultKind.CRITICAL_DISK for item in directives):
            ratio = self.critical_free_ratio
        elif any(item.kind is FaultKind.DEGRADED_DISK for item in directives):
            ratio = self.degraded_free_ratio
        free = int(self.disk_total_bytes * ratio)
        used = self.disk_total_bytes - free
        return TelemetrySample(
            monotonic_ns=self.cycle * 1_000_000_000,
            process_time_ns=self.cycle * 1_000_000,
            disk_total_bytes=self.disk_total_bytes,
            disk_used_bytes=used,
            disk_free_bytes=free,
            rss_bytes=self.rss_bytes,
            load_1m=0.0,
            instruments={
                "monotonic_ns": "deterministic_lab.logical_clock",
                "process_time_ns": "deterministic_lab.logical_cpu",
                "disk": "deterministic_lab.synthetic_disk",
                "rss": "deterministic_lab.synthetic_rss",
                "load_1m": "deterministic_lab.synthetic_load",
            },
        )


def plan_fingerprint(plan: DeterministicFaultPlan) -> str:
    return evidence_receipt(
        "DETERMINISTIC_PLAN_FINGERPRINT",
        {"plan": canonical_json(plan.as_dict())},
        {"frozen": True},
    )["receipt_hash"]
