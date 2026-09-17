from __future__ import annotations

from typing import Any

from .organism_loop import GovernedOrganism


class CachedRootGovernedOrganism(GovernedOrganism):
    """Experimental drop-in GovernedOrganism with exact state-root memoization.

    This class does not change the state payload, hash algorithm, lineage, receipts,
    constitutional bindings, or gate behavior. It only avoids recomputing a root
    when the private state structure has not changed since the previous call.

    The cache marker relies on GovernedOrganism's encapsulation contract: lineage
    and constraints are mutated internally by append/add operations and external
    callers receive cloned/immutable representations rather than mutable internal
    references. If that contract changes, this optimization must be re-evaluated.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._cached_state_root: str | None = None
        self._cached_state_marker: tuple[int, int] | None = None
        self._state_root_recomputations = 0
        super().__init__(*args, **kwargs)

    def _state_marker(self) -> tuple[int, int]:
        return (len(self._lineage), len(self._constraints))

    @property
    def state_root_recomputations(self) -> int:
        return self._state_root_recomputations

    def state_root(self) -> str:
        marker = self._state_marker()
        if self._cached_state_root is None or self._cached_state_marker != marker:
            self._cached_state_root = super().state_root()
            self._cached_state_marker = marker
            self._state_root_recomputations += 1
        return self._cached_state_root
