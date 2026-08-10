"""In-process pub/sub for intervention updates (WebSocket fan-out)."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from agent_guardian.schemas import InterventionUpdated


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[InterventionUpdated]]] = defaultdict(set)
        self._global: set[asyncio.Queue[InterventionUpdated]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(
        self, intervention_id: str | None = None
    ) -> asyncio.Queue[InterventionUpdated]:
        queue: asyncio.Queue[InterventionUpdated] = asyncio.Queue(maxsize=64)
        async with self._lock:
            if intervention_id is None:
                self._global.add(queue)
            else:
                self._subs[intervention_id].add(queue)
        return queue

    async def unsubscribe(
        self, queue: asyncio.Queue[InterventionUpdated], intervention_id: str | None = None
    ) -> None:
        async with self._lock:
            if intervention_id is None:
                self._global.discard(queue)
            else:
                self._subs[intervention_id].discard(queue)

    async def publish(self, update: InterventionUpdated) -> None:
        async with self._lock:
            targets = list(self._global)
            targets.extend(self._subs.get(update.intervention_id, set()))
        for q in targets:
            try:
                q.put_nowait(update)
            except asyncio.QueueFull:
                # Drop oldest-style: ignore if consumer is stuck
                pass
