"""Dynamic takeover / force-interrupt signals for swarm agents."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from uuid import uuid4

from agent_guardian.swarm.types import TakeoverKind, TakeoverSignal, utc_now


class TakeoverBroker:
    """
    High-priority interrupt channel per agent.

    Web console Force Takeover → enqueue signal → agent await/poll and pause.
    Compatible with WS fan-out or SSE (consumer pulls from queue).
    """

    def __init__(self, *, queue_size: int = 16) -> None:
        self.queue_size = queue_size
        self._lock = asyncio.Lock()
        self._queues: dict[str, asyncio.Queue[TakeoverSignal]] = {}
        self._pending: dict[str, TakeoverSignal | None] = defaultdict(lambda: None)

    def _ensure_queue(self, agent_id: str) -> asyncio.Queue[TakeoverSignal]:
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue(maxsize=self.queue_size)
        return self._queues[agent_id]

    async def request_takeover(
        self,
        agent_id: str,
        *,
        instruction: str | None = None,
        operator_id: str | None = None,
        kind: TakeoverKind = TakeoverKind.FORCE_PAUSE,
        priority: int = 100,
    ) -> TakeoverSignal:
        signal = TakeoverSignal(
            agent_id=agent_id,
            kind=kind,
            instruction=instruction,
            operator_id=operator_id,
            priority=priority,
            ts=utc_now(),
            signal_id=str(uuid4()),
        )
        async with self._lock:
            q = self._ensure_queue(agent_id)
            self._pending[agent_id] = signal
        try:
            q.put_nowait(signal)
        except asyncio.QueueFull:
            # Drop oldest, keep newest high-priority interrupt
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            q.put_nowait(signal)
        return signal

    async def resume(
        self, agent_id: str, *, operator_id: str | None = None
    ) -> TakeoverSignal:
        return await self.request_takeover(
            agent_id,
            kind=TakeoverKind.RESUME,
            operator_id=operator_id,
            instruction=None,
            priority=50,
        )

    async def wait(self, agent_id: str, *, timeout: float | None = None) -> TakeoverSignal:
        async with self._lock:
            q = self._ensure_queue(agent_id)
        if timeout is None:
            return await q.get()
        return await asyncio.wait_for(q.get(), timeout=timeout)

    def poll(self, agent_id: str) -> TakeoverSignal | None:
        q = self._queues.get(agent_id)
        if q is None:
            return None
        try:
            return q.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def pending(self, agent_id: str) -> TakeoverSignal | None:
        return self._pending.get(agent_id)

    def clear_pending(self, agent_id: str) -> None:
        self._pending[agent_id] = None

    async def stream(self, agent_id: str) -> AsyncIterator[TakeoverSignal]:
        while True:
            yield await self.wait(agent_id)
