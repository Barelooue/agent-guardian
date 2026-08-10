"""In-process agent state checkpoints for human-driven rollback."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Checkpoint:
    state: Any
    label: str | None = None
    ts: float = field(default_factory=time.time)


class CheckpointStack:
    """LIFO stack of agent state snapshots."""

    def __init__(self, *, maxlen: int = 64) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        self.maxlen = maxlen
        self._items: list[Checkpoint] = []

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()

    def push(self, state: Any, *, label: str | None = None) -> Checkpoint:
        cp = Checkpoint(state=state, label=label)
        self._items.append(cp)
        if len(self._items) > self.maxlen:
            self._items = self._items[-self.maxlen :]
        return cp

    def rollback(self, steps: int = 1) -> Any:
        """
        Discard the newest ``steps`` checkpoints and return the restored state.

        Example: stack [s0, s1, s2] → rollback(1) → stack [s0, s1], returns s1.
        """
        if steps < 1:
            raise ValueError("steps must be >= 1")
        if len(self._items) <= steps:
            raise IndexError(
                f"not enough checkpoints to rollback {steps} "
                f"(have {len(self._items)})"
            )
        for _ in range(steps):
            self._items.pop()
        return self._items[-1].state

    def peek(self) -> Any | None:
        return self._items[-1].state if self._items else None
