"""Non-blocking shadow observation bus for swarm agents."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

from agent_guardian.swarm.types import ShadowEvent, ShadowEventType


class ShadowObserver:
    """
    Silent observing: agents publish thoughts / screenshots / actions without
    blocking the automation loop. Web consoles subscribe per-tenant or globally.
    """

    def __init__(
        self,
        *,
        per_agent_history: int = 64,
        subscriber_queue_size: int = 256,
    ) -> None:
        self.per_agent_history = per_agent_history
        self.subscriber_queue_size = subscriber_queue_size
        self._seq = 0
        self._lock = asyncio.Lock()
        self._history: dict[str, deque[ShadowEvent]] = defaultdict(
            lambda: deque(maxlen=per_agent_history)
        )
        self._subs: set[asyncio.Queue[ShadowEvent]] = set()
        self._tenant_subs: dict[str, set[asyncio.Queue[ShadowEvent]]] = defaultdict(set)
        self._agent_subs: dict[str, set[asyncio.Queue[ShadowEvent]]] = defaultdict(set)

    async def publish(
        self,
        *,
        agent_id: str,
        tenant_id: str,
        type: ShadowEventType | str,
        payload: dict[str, Any] | None = None,
    ) -> ShadowEvent:
        async with self._lock:
            self._seq += 1
            event = ShadowEvent(
                agent_id=agent_id,
                tenant_id=tenant_id,
                type=ShadowEventType(type),
                payload=dict(payload or {}),
                sequence=self._seq,
            )
            self._history[agent_id].append(event)
            targets: list[asyncio.Queue[ShadowEvent]] = list(self._subs)
            targets.extend(self._tenant_subs.get(tenant_id, set()))
            targets.extend(self._agent_subs.get(agent_id, set()))

        for q in targets:
            self._offer(q, event)
        return event

    async def publish_thought(
        self, agent_id: str, tenant_id: str, thought: str, **extra: Any
    ) -> ShadowEvent:
        return await self.publish(
            agent_id=agent_id,
            tenant_id=tenant_id,
            type=ShadowEventType.THOUGHT,
            payload={"thought": thought, **extra},
        )

    async def publish_action(
        self, agent_id: str, tenant_id: str, action: str, **extra: Any
    ) -> ShadowEvent:
        return await self.publish(
            agent_id=agent_id,
            tenant_id=tenant_id,
            type=ShadowEventType.ACTION,
            payload={"action": action, **extra},
        )

    async def publish_screenshot(
        self,
        agent_id: str,
        tenant_id: str,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
        **extra: Any,
    ) -> ShadowEvent:
        return await self.publish(
            agent_id=agent_id,
            tenant_id=tenant_id,
            type=ShadowEventType.SCREENSHOT,
            payload={
                "image_url": image_url,
                "image_base64": image_base64,
                **extra,
            },
        )

    def recent(self, agent_id: str, *, limit: int = 20) -> list[ShadowEvent]:
        hist = self._history.get(agent_id)
        if not hist:
            return []
        items = list(hist)
        return items[-limit:]

    async def subscribe(
        self,
        *,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> asyncio.Queue[ShadowEvent]:
        queue: asyncio.Queue[ShadowEvent] = asyncio.Queue(maxsize=self.subscriber_queue_size)
        async with self._lock:
            if agent_id is not None:
                self._agent_subs[agent_id].add(queue)
            elif tenant_id is not None:
                self._tenant_subs[tenant_id].add(queue)
            else:
                self._subs.add(queue)
        return queue

    async def unsubscribe(
        self,
        queue: asyncio.Queue[ShadowEvent],
        *,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        async with self._lock:
            if agent_id is not None:
                self._agent_subs[agent_id].discard(queue)
            elif tenant_id is not None:
                self._tenant_subs[tenant_id].discard(queue)
            else:
                self._subs.discard(queue)

    async def stream(
        self,
        *,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> AsyncIterator[ShadowEvent]:
        queue = await self.subscribe(tenant_id=tenant_id, agent_id=agent_id)
        try:
            while True:
                yield await queue.get()
        finally:
            await self.unsubscribe(queue, tenant_id=tenant_id, agent_id=agent_id)

    @staticmethod
    def _offer(queue: asyncio.Queue[ShadowEvent], event: ShadowEvent) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
