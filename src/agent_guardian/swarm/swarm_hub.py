"""Multi-agent hub: registry, connections, routing, shadow + takeover façade."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agent_guardian.swarm.routing import ChannelRouter
from agent_guardian.swarm.shadow_observer import ShadowObserver
from agent_guardian.swarm.takeover import TakeoverBroker
from agent_guardian.swarm.types import (
    AgentMeta,
    AgentStatus,
    ChannelRoute,
    ConnectionKind,
    RouteRule,
    ShadowEvent,
    TakeoverKind,
    TakeoverSignal,
)

if TYPE_CHECKING:
    from agent_guardian.daemon.takeover_store import TakeoverStore

logger = logging.getLogger(__name__)


class AgentHubManager:
    """
    Control-plane registry for 100+ concurrent agents.

    - register / heartbeat / unregister (WS or HTTP long-poll sessions)
    - status transitions (idle|running|intervenced|takeover|offline)
    - RBAC channel routing for interventions
    - shadow observe + force takeover (persisted when TakeoverStore attached)
    """

    def __init__(
        self,
        *,
        router: ChannelRouter | None = None,
        shadow: ShadowObserver | None = None,
        takeover: TakeoverBroker | None = None,
        takeover_store: TakeoverStore | None = None,
        max_agents: int = 500,
    ) -> None:
        self.max_agents = max_agents
        self.router = router or ChannelRouter()
        self.shadow = shadow or ShadowObserver()
        self.takeover = takeover or TakeoverBroker()
        self.takeover_store = takeover_store
        self._agents: dict[str, AgentMeta] = {}
        self._connections: dict[str, ConnectionKind] = {}
        self._lock = asyncio.Lock()

    def attach_takeover_store(self, store: TakeoverStore) -> None:
        """Wire SQLite persistence for Force Takeover (Daemon bootstrap)."""
        self.takeover_store = store

    # ------------------------------------------------------------------ registry

    async def register(
        self,
        agent_id: str | None = None,
        *,
        agent_type: str = "generic",
        tenant_id: str = "default",
        labels: dict[str, str] | None = None,
        connection_kind: ConnectionKind = ConnectionKind.INPROC,
        status: AgentStatus = AgentStatus.IDLE,
    ) -> AgentMeta:
        async with self._lock:
            if agent_id is None:
                agent_id = str(uuid4())
            if agent_id not in self._agents and len(self._agents) >= self.max_agents:
                raise RuntimeError(f"agent capacity exceeded ({self.max_agents})")
            meta = AgentMeta(
                agent_id=agent_id,
                agent_type=agent_type,
                tenant_id=tenant_id,
                status=status,
                labels=dict(labels or {}),
                connection_kind=connection_kind,
                session_id=str(uuid4()),
            )
            self._agents[agent_id] = meta
            self._connections[agent_id] = connection_kind
            return meta

    async def unregister(self, agent_id: str) -> bool:
        async with self._lock:
            meta = self._agents.pop(agent_id, None)
            self._connections.pop(agent_id, None)
            if meta is None:
                return False
            tenant_id = meta.tenant_id
        await self.shadow.publish(
            agent_id=agent_id,
            tenant_id=tenant_id,
            type="status",
            payload={"status": AgentStatus.OFFLINE.value},
        )
        return True

    async def heartbeat(self, agent_id: str, *, status: AgentStatus | None = None) -> AgentMeta:
        async with self._lock:
            meta = self._require(agent_id)
            meta.touch()
            if status is not None:
                meta.status = status
            return meta

    async def set_status(self, agent_id: str, status: AgentStatus) -> AgentMeta:
        async with self._lock:
            meta = self._require(agent_id)
            meta.status = status
            meta.touch()
            snapshot = AgentMeta(
                agent_id=meta.agent_id,
                agent_type=meta.agent_type,
                tenant_id=meta.tenant_id,
                status=meta.status,
                labels=dict(meta.labels),
                last_seen_at=meta.last_seen_at,
                connection_kind=meta.connection_kind,
                session_id=meta.session_id,
            )
        await self.shadow.publish(
            agent_id=agent_id,
            tenant_id=snapshot.tenant_id,
            type="status",
            payload={"status": status.value},
        )
        return snapshot

    def get(self, agent_id: str) -> AgentMeta | None:
        return self._agents.get(agent_id)

    def list_agents(
        self,
        *,
        tenant_id: str | None = None,
        status: AgentStatus | None = None,
    ) -> list[AgentMeta]:
        items = list(self._agents.values())
        if tenant_id is not None:
            items = [a for a in items if a.tenant_id == tenant_id]
        if status is not None:
            items = [a for a in items if a.status == status]
        return sorted(items, key=lambda a: a.agent_id)

    @property
    def size(self) -> int:
        return len(self._agents)

    # ------------------------------------------------------------------ routing

    def add_route_rule(self, rule: RouteRule) -> None:
        self.router.add_rule(rule)

    def resolve_channels(self, agent_id: str) -> ChannelRoute:
        meta = self._agents.get(agent_id)
        if meta is None:
            raise KeyError(f"unknown agent: {agent_id}")
        return self.router.resolve(meta)

    def authorize(self, agent_id: str, *, role: str) -> bool:
        meta = self._agents.get(agent_id)
        if meta is None:
            return False
        return self.router.authorize(meta, role=role)

    # ------------------------------------------------------------------ shadow

    async def observe(
        self,
        agent_id: str,
        *,
        thought: str | None = None,
        action: str | None = None,
        screenshot_url: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> list[ShadowEvent]:
        """Non-blocking multi-publish helper used by SDK agents."""
        meta = self._agents.get(agent_id)
        if meta is None:
            raise KeyError(f"unknown agent: {agent_id}")
        events: list[ShadowEvent] = []
        extra = extra or {}
        if thought is not None:
            events.append(
                await self.shadow.publish_thought(
                    agent_id, meta.tenant_id, thought, **extra
                )
            )
        if action is not None:
            events.append(
                await self.shadow.publish_action(
                    agent_id, meta.tenant_id, action, **extra
                )
            )
        if screenshot_url is not None:
            events.append(
                await self.shadow.publish_screenshot(
                    agent_id, meta.tenant_id, image_url=screenshot_url, **extra
                )
            )
        return events

    # ------------------------------------------------------------------ takeover

    async def force_takeover(
        self,
        agent_id: str,
        *,
        instruction: str | None = None,
        operator_id: str | None = None,
        role: str = "operator",
    ) -> TakeoverSignal:
        if not self.authorize(agent_id, role=role):
            raise PermissionError(f"role={role} not allowed to takeover {agent_id}")
        signal = await self.takeover.request_takeover(
            agent_id,
            instruction=instruction,
            operator_id=operator_id,
            kind=(
                TakeoverKind.INJECT_INSTRUCTION
                if instruction
                else TakeoverKind.FORCE_PAUSE
            ),
        )
        await self.set_status(agent_id, AgentStatus.TAKEOVER)
        await self._persist_takeover(signal, role=role)
        return signal

    async def _persist_takeover(self, signal: TakeoverSignal, *, role: str) -> None:
        store = self.takeover_store
        if store is None:
            return
        meta = self._agents.get(signal.agent_id)
        recent = self.shadow.recent(signal.agent_id, limit=16)
        before_thought = next(
            (
                e.payload.get("thought")
                for e in reversed(recent)
                if getattr(e.type, "value", e.type) == "thought"
            ),
            None,
        )
        before_action = next(
            (
                e.payload.get("action")
                for e in reversed(recent)
                if getattr(e.type, "value", e.type) == "action"
            ),
            None,
        )
        screenshot = next(
            (
                e.payload.get("image_url") or e.payload.get("image_base64")
                for e in reversed(recent)
                if getattr(e.type, "value", e.type) == "screenshot"
            ),
            None,
        )
        try:
            await store.insert(
                signal_id=signal.signal_id,
                agent_id=signal.agent_id,
                kind=signal.kind.value,
                instruction=signal.instruction,
                operator_id=signal.operator_id,
                before_thought=str(before_thought) if before_thought is not None else None,
                before_action=str(before_action) if before_action is not None else None,
                screenshot_path=str(screenshot) if screenshot is not None else None,
                tenant_id=meta.tenant_id if meta else None,
                agent_type=meta.agent_type if meta else None,
                meta={
                    "role": role,
                    "labels": dict(meta.labels) if meta else {},
                    "session_id": meta.session_id if meta else None,
                    "priority": signal.priority,
                },
                created_at=signal.ts.isoformat(),
            )
        except Exception:
            logger.exception(
                "failed to persist takeover signal_id=%s agent_id=%s",
                signal.signal_id,
                signal.agent_id,
            )

    async def wait_takeover(
        self, agent_id: str, *, timeout: float | None = None
    ) -> TakeoverSignal:
        return await self.takeover.wait(agent_id, timeout=timeout)

    def poll_takeover(self, agent_id: str) -> TakeoverSignal | None:
        return self.takeover.poll(agent_id)

    # ------------------------------------------------------------------ bulk

    async def register_many(
        self, count: int, *, tenant_id: str = "default", prefix: str = "agent"
    ) -> list[AgentMeta]:
        """Test / load helper: register ``count`` synthetic agents."""
        out: list[AgentMeta] = []
        for i in range(count):
            meta = await self.register(
                f"{prefix}-{i:04d}",
                tenant_id=tenant_id,
                agent_type="synthetic",
                connection_kind=ConnectionKind.INPROC,
            )
            out.append(meta)
        return out

    def connected_ids(self) -> Iterable[str]:
        return tuple(self._connections.keys())

    def _require(self, agent_id: str) -> AgentMeta:
        meta = self._agents.get(agent_id)
        if meta is None:
            raise KeyError(f"unknown agent: {agent_id}")
        return meta
