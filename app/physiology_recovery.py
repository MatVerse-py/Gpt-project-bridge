from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .physiology import DurableEventJournal, PhysiologyEngine

TERMINAL_EVENT_TYPES = {"MEMORY_COMMIT", "CYCLE_ABORTED"}


@dataclass(frozen=True)
class IncompleteCycle:
    cycle_id: str
    first_seq: int
    last_seq: int
    last_event_id: str
    event_types: tuple[str, ...]


def _all_events(journal: DurableEventJournal, *, topic: str = "physiology"):
    after = 0
    while True:
        batch = journal.read(after_seq=after, limit=10_000, topic=topic)
        if not batch:
            break
        for event in batch:
            yield event
        after = batch[-1].seq


def find_incomplete_cycles(journal: DurableEventJournal) -> tuple[IncompleteCycle, ...]:
    grouped: dict[str, list[Any]] = {}
    for event in _all_events(journal):
        cycle_id = event.correlation_id
        if not cycle_id:
            continue
        grouped.setdefault(cycle_id, []).append(event)

    incomplete: list[IncompleteCycle] = []
    for cycle_id, events in grouped.items():
        ordered = sorted(events, key=lambda item: item.seq)
        types = tuple(item.event_type for item in ordered)
        if "TICK" not in types:
            continue
        if any(item.event_type in TERMINAL_EVENT_TYPES for item in ordered):
            continue
        incomplete.append(
            IncompleteCycle(
                cycle_id=cycle_id,
                first_seq=ordered[0].seq,
                last_seq=ordered[-1].seq,
                last_event_id=ordered[-1].event_id,
                event_types=types,
            )
        )
    return tuple(sorted(incomplete, key=lambda item: item.first_seq))


def seal_incomplete_cycles(
    journal: DurableEventJournal,
    *,
    reason: str = "runtime_restart_recovery",
) -> tuple[str, ...]:
    if not reason.strip():
        raise ValueError("reason must be non-empty")
    sealed: list[str] = []
    for cycle in find_incomplete_cycles(journal):
        event = journal.append(
            event_id=f"{cycle.cycle_id}:aborted",
            topic="physiology",
            event_type="CYCLE_ABORTED",
            payload={
                "cycle_id": cycle.cycle_id,
                "reason": reason,
                "first_seq": cycle.first_seq,
                "last_seq": cycle.last_seq,
                "observed_event_types": list(cycle.event_types),
                "closed": False,
                "terminal": True,
            },
            causation_id=cycle.last_event_id,
            correlation_id=cycle.cycle_id,
        )
        sealed.append(event.event_id)
    return tuple(sealed)


class RestartSafePhysiologyEngine(PhysiologyEngine):
    """Physiology engine that seals prior incomplete cycles before resuming.

    Recovery never fabricates a successful memory commit. An interrupted cycle is terminally
    marked `CYCLE_ABORTED`, preserving the distinction between completed and recovered history.
    """

    def __init__(self, *args: Any, recovery_reason: str = "runtime_restart_recovery", **kwargs: Any) -> None:
        journal = kwargs.get("journal")
        if journal is None:
            raise TypeError("journal must be provided as a keyword argument")
        if not isinstance(journal, DurableEventJournal):
            raise TypeError("journal must be DurableEventJournal")
        self.recovered_event_ids = seal_incomplete_cycles(journal, reason=recovery_reason)
        super().__init__(*args, **kwargs)
