"""Phase 7 swarm REST + WebSocket routes mounted on the Daemon."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agent_guardian.swarm import AgentHubManager, AgentStatus, ConnectionKind

logger = logging.getLogger(__name__)


class RegisterAgentBody(BaseModel):
    agent_id: str | None = None
    agent_type: str = "generic"
    tenant_id: str = "default"
    labels: dict[str, str] = Field(default_factory=dict)


class ObserveBody(BaseModel):
    thought: str | None = None
    action: str | None = None
    screenshot_url: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TakeoverBody(BaseModel):
    instruction: str | None = None
    operator_id: str = "web:console"
    role: str = "operator"


def _meta_dict(meta: Any) -> dict[str, Any]:
    return {
        "agent_id": meta.agent_id,
        "agent_type": meta.agent_type,
        "tenant_id": meta.tenant_id,
        "status": meta.status.value if hasattr(meta.status, "value") else meta.status,
        "labels": meta.labels,
        "last_seen_at": meta.last_seen_at.isoformat(),
        "connection_kind": (
            meta.connection_kind.value if meta.connection_kind else None
        ),
        "session_id": meta.session_id,
    }


def _event_dict(ev: Any) -> dict[str, Any]:
    return {
        "agent_id": ev.agent_id,
        "tenant_id": ev.tenant_id,
        "type": ev.type.value if hasattr(ev.type, "value") else ev.type,
        "payload": ev.payload,
        "ts": ev.ts.isoformat(),
        "sequence": ev.sequence,
    }


def build_swarm_router() -> APIRouter:
    router = APIRouter(tags=["swarm"])

    @router.get("/api/swarm/agents")
    async def list_agents(
        request: Request, tenant_id: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        hub: AgentHubManager = request.app.state.swarm_hub
        st = AgentStatus(status) if status else None
        agents = hub.list_agents(tenant_id=tenant_id, status=st)
        cards = []
        for a in agents:
            recent = hub.shadow.recent(a.agent_id, limit=8)
            thought = next(
                (e.payload.get("thought") for e in reversed(recent) if e.type.value == "thought"),
                None,
            )
            action = next(
                (e.payload.get("action") for e in reversed(recent) if e.type.value == "action"),
                None,
            )
            shot = next(
                (
                    e.payload.get("image_url") or e.payload.get("image_base64")
                    for e in reversed(recent)
                    if e.type.value == "screenshot"
                ),
                None,
            )
            route = hub.resolve_channels(a.agent_id)
            cards.append(
                {
                    **_meta_dict(a),
                    "last_thought": thought,
                    "last_action": action,
                    "last_screenshot": shot,
                    "route": {
                        "channels": list(route.channels),
                        "web_room": route.web_room,
                        "reason": route.reason,
                    },
                }
            )
        return {"items": cards, "count": len(cards)}

    @router.post("/api/swarm/agents")
    async def register_agent(request: Request, body: RegisterAgentBody) -> dict[str, Any]:
        hub: AgentHubManager = request.app.state.swarm_hub
        try:
            meta = await hub.register(
                body.agent_id,
                agent_type=body.agent_type,
                tenant_id=body.tenant_id,
                labels=body.labels,
                connection_kind=ConnectionKind.HTTP_LONGPOLL,
                status=AgentStatus.IDLE,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        return _meta_dict(meta)

    @router.get("/api/swarm/agents/{agent_id}")
    async def get_agent(request: Request, agent_id: str) -> dict[str, Any]:
        hub: AgentHubManager = request.app.state.swarm_hub
        meta = hub.get(agent_id)
        if meta is None:
            raise HTTPException(status_code=404, detail={"error": "not found"})
        return {
            **_meta_dict(meta),
            "shadow": [_event_dict(e) for e in hub.shadow.recent(agent_id, limit=20)],
        }

    @router.post("/api/swarm/agents/{agent_id}/observe")
    async def observe_agent(
        request: Request, agent_id: str, body: ObserveBody
    ) -> dict[str, Any]:
        hub: AgentHubManager = request.app.state.swarm_hub
        if hub.get(agent_id) is None:
            raise HTTPException(status_code=404, detail={"error": "not found"})
        await hub.set_status(agent_id, AgentStatus.RUNNING)
        events = await hub.observe(
            agent_id,
            thought=body.thought,
            action=body.action,
            screenshot_url=body.screenshot_url,
            extra=body.extra,
        )
        await hub.heartbeat(agent_id, status=AgentStatus.RUNNING)
        return {"events": [_event_dict(e) for e in events]}

    @router.post("/api/swarm/agents/{agent_id}/takeover")
    async def takeover_agent(
        request: Request, agent_id: str, body: TakeoverBody
    ) -> dict[str, Any]:
        hub: AgentHubManager = request.app.state.swarm_hub
        if hub.get(agent_id) is None:
            raise HTTPException(status_code=404, detail={"error": "not found"})
        try:
            signal = await hub.force_takeover(
                agent_id,
                instruction=body.instruction,
                operator_id=body.operator_id,
                role=body.role,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
        return {
            "signal_id": signal.signal_id,
            "agent_id": signal.agent_id,
            "kind": signal.kind.value,
            "instruction": signal.instruction,
            "operator_id": signal.operator_id,
            "ts": signal.ts.isoformat(),
        }

    @router.get("/api/swarm/agents/{agent_id}/shadow")
    async def agent_shadow(
        request: Request, agent_id: str, limit: int = 20
    ) -> dict[str, Any]:
        hub: AgentHubManager = request.app.state.swarm_hub
        return {
            "agent_id": agent_id,
            "events": [_event_dict(e) for e in hub.shadow.recent(agent_id, limit=limit)],
        }

    @router.websocket("/ws/swarm")
    async def swarm_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        hub: AgentHubManager = websocket.app.state.swarm_hub
        tenant_id = websocket.query_params.get("tenant_id")
        queue = await hub.shadow.subscribe(tenant_id=tenant_id)
        stop = asyncio.Event()

        async def pump_shadow() -> None:
            try:
                while not stop.is_set():
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except TimeoutError:
                        continue
                    await websocket.send_json({"type": "shadow", "event": _event_dict(ev)})
            except Exception:
                stop.set()

        task = asyncio.create_task(pump_shadow())
        try:
            await websocket.send_json(
                {
                    "type": "hello",
                    "message": "swarm shadow stream connected",
                    "tenant_id": tenant_id,
                }
            )
            while True:
                raw = await websocket.receive_json()
                msg_type = raw.get("type")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "takeover":
                    agent_id = raw.get("agent_id")
                    if not agent_id:
                        await websocket.send_json(
                            {"type": "error", "message": "agent_id required"}
                        )
                        continue
                    try:
                        signal = await hub.force_takeover(
                            agent_id,
                            instruction=raw.get("instruction"),
                            operator_id=raw.get("operator_id") or "web:ws",
                            role=raw.get("role") or "operator",
                        )
                        await websocket.send_json(
                            {
                                "type": "takeover_ack",
                                "signal_id": signal.signal_id,
                                "agent_id": agent_id,
                                "kind": signal.kind.value,
                                "instruction": signal.instruction,
                            }
                        )
                    except (PermissionError, KeyError) as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                elif msg_type == "list":
                    agents = hub.list_agents(tenant_id=raw.get("tenant_id") or tenant_id)
                    await websocket.send_json(
                        {
                            "type": "agents",
                            "items": [_meta_dict(a) for a in agents],
                        }
                    )
        except WebSocketDisconnect:
            logger.debug("swarm ws disconnected")
        finally:
            stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await hub.shadow.unsubscribe(queue, tenant_id=tenant_id)

    return router
