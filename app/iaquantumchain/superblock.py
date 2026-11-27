from __future__ import annotations

import json
from typing import Any, Callable, Mapping


class SuperblockUnavailable(RuntimeError):
    pass


class SuperblockLedgerAdapter:
    """Writes to the existing MatVerse append-only ledger; creates no new chain."""

    def __init__(
        self,
        *,
        append_event_fn: Callable[..., Any] | None = None,
        verify_chain_fn: Callable[..., Any] | None = None,
        read_ledger_fn: Callable[..., Any] | None = None,
    ) -> None:
        if append_event_fn is None or verify_chain_fn is None or read_ledger_fn is None:
            try:
                from app.storage import append_event, read_ledger, verify_chain
            except ImportError as exc:  # pragma: no cover - exercised by integration install
                raise SuperblockUnavailable("canonical app.storage ledger is unavailable") from exc
            append_event_fn = append_event
            verify_chain_fn = verify_chain
            read_ledger_fn = read_ledger
        self._append_event = append_event_fn
        self._verify_chain = verify_chain_fn
        self._read_ledger = read_ledger_fn

    def append(self, event: Mapping[str, Any], decision: str) -> dict[str, Any]:
        receipt = self._append_event(dict(event), decision)
        integrity = self._verify_chain()
        if not isinstance(integrity, Mapping) or integrity.get("ok") is not True:
            raise SuperblockUnavailable("shared MatVerse ledger integrity check failed after append")
        return {
            "ledger_receipt": receipt,
            "ledger_integrity": dict(integrity),
            "onchain_anchor": "HOLD_NOT_CONFIGURED",
        }

    def events_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._read_ledger()
        found: list[dict[str, Any]] = []
        for row in rows:
            raw = row.get("event_json")
            if not isinstance(raw, str):
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("run_id") == run_id:
                found.append({"ledger": row, "event": event})
        return found

    def verify(self) -> dict[str, Any]:
        return dict(self._verify_chain())
