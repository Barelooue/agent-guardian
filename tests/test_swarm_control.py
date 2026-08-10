"""Phase 7 swarm control-plane unit tests."""

from __future__ import annotations

import asyncio

import pytest

from agent_guardian.swarm import (
    AgentHubManager,
    AgentStatus,
    ConnectionKind,
    RouteRule,
    ShadowEventType,
    TakeoverKind,
)


@pytest.mark.asyncio
async def test_register_heartbeat_and_list() -> None:
    hub = AgentHubManager()
    a = await hub.register(
        "browser-1",
        agent_type="browser-use",
        tenant_id="acme",
        labels={"team": "ops"},
        connection_kind=ConnectionKind.WEBSOCKET,
    )
    assert a.status == AgentStatus.IDLE
    await hub.heartbeat("browser-1", status=AgentStatus.RUNNING)
    assert hub.get("browser-1").status == AgentStatus.RUNNING
    listed = hub.list_agents(tenant_id="acme")
    assert len(listed) == 1
    assert listed[0].agent_id == "browser-1"


@pytest.mark.asyncio
async def test_capacity_100_plus_agents() -> None:
    hub = AgentHubManager(max_agents=150)
    agents = await hub.register_many(120, tenant_id="load", prefix="w")
    assert hub.size == 120
    assert len(agents) == 120
    assert len(list(hub.connected_ids())) == 120
    with pytest.raises(RuntimeError, match="capacity"):
        # fill to max then one more
        await hub.register_many(31, tenant_id="load", prefix="x")
        await hub.register("overflow")


@pytest.mark.asyncio
async def test_rbac_channel_routing_finance() -> None:
    hub = AgentHubManager()
    hub.add_route_rule(
        RouteRule(
            name="finance_telegram",
            priority=10,
            agent_type="finance",
            channels=("telegram", "web_ui"),
            web_room="finance",
            telegram_chat_id="-100111",
            roles_allowed=frozenset({"admin", "finance_ops"}),
        )
    )
    hub.add_route_rule(
        RouteRule(
            name="default_ops",
            priority=1,
            tenant_id="acme",
            channels=("terminal",),
            web_room="lobby",
            roles_allowed=frozenset({"admin", "operator", "viewer"}),
        )
    )
    await hub.register("fin-1", agent_type="finance", tenant_id="acme")
    await hub.register("web-1", agent_type="browser-use", tenant_id="acme")

    fin_route = hub.resolve_channels("fin-1")
    assert fin_route.web_room == "finance"
    assert fin_route.telegram_chat_id == "-100111"
    assert "telegram" in fin_route.channels
    assert fin_route.reason == "finance_telegram"

    web_route = hub.resolve_channels("web-1")
    assert web_route.web_room == "lobby"
    assert web_route.reason == "default_ops"

    assert hub.authorize("fin-1", role="finance_ops") is True
    assert hub.authorize("fin-1", role="viewer") is False
    assert hub.authorize("web-1", role="viewer") is True


@pytest.mark.asyncio
async def test_shadow_observer_non_blocking_fanout() -> None:
    hub = AgentHubManager()
    await hub.register("a1", tenant_id="t1")
    q = await hub.shadow.subscribe(tenant_id="t1")

    # publish without awaiting consumer — must not block
    await hub.observe(
        "a1",
        thought="I should click pay",
        action="click:#pay",
        screenshot_url="/v1/media/a1.jpg",
    )
    events = []
    for _ in range(3):
        events.append(await asyncio.wait_for(q.get(), timeout=1.0))
    types = {e.type for e in events}
    assert ShadowEventType.THOUGHT in types
    assert ShadowEventType.ACTION in types
    assert ShadowEventType.SCREENSHOT in types
    recent = hub.shadow.recent("a1")
    assert len(recent) >= 3


@pytest.mark.asyncio
async def test_shadow_queue_drops_oldest_when_full() -> None:
    hub = AgentHubManager(shadow=__import__(
        "agent_guardian.swarm.shadow_observer", fromlist=["ShadowObserver"]
    ).ShadowObserver(subscriber_queue_size=2))
    await hub.register("a1", tenant_id="t1")
    q = await hub.shadow.subscribe(agent_id="a1")
    for i in range(5):
        await hub.shadow.publish_thought("a1", "t1", f"t{i}")
    # queue kept newest; at least 2 events available
    got = [q.get_nowait(), q.get_nowait()]
    assert got[-1].payload["thought"] == "t4"


@pytest.mark.asyncio
async def test_force_takeover_pauses_agent() -> None:
    hub = AgentHubManager()
    await hub.register("bot-9", agent_type="browser-use", tenant_id="acme")
    await hub.set_status("bot-9", AgentStatus.RUNNING)

    # Agent side waits for interrupt concurrently
    wait_task = asyncio.create_task(hub.wait_takeover("bot-9", timeout=2.0))
    signal = await hub.force_takeover(
        "bot-9",
        instruction="Stop and ask human before payment",
        operator_id="web:console",
        role="operator",
    )
    got = await wait_task
    assert got.signal_id == signal.signal_id
    assert got.kind == TakeoverKind.INJECT_INSTRUCTION
    assert "payment" in (got.instruction or "")
    assert hub.get("bot-9").status == AgentStatus.TAKEOVER


@pytest.mark.asyncio
async def test_takeover_denied_by_rbac() -> None:
    hub = AgentHubManager()
    hub.add_route_rule(
        RouteRule(
            name="finance_only",
            priority=5,
            agent_type="finance",
            roles_allowed=frozenset({"finance_ops"}),
            channels=("telegram",),
        )
    )
    await hub.register("fin-2", agent_type="finance")
    with pytest.raises(PermissionError):
        await hub.force_takeover("fin-2", role="viewer")


@pytest.mark.asyncio
async def test_poll_takeover_nonblocking() -> None:
    hub = AgentHubManager()
    await hub.register("x")
    assert hub.poll_takeover("x") is None
    await hub.force_takeover("x", instruction="pause", role="admin")
    # status rule: no matching route → default authorize admin
    sig = hub.poll_takeover("x")
    assert sig is not None
    assert sig.kind in {TakeoverKind.FORCE_PAUSE, TakeoverKind.INJECT_INSTRUCTION}


@pytest.mark.asyncio
async def test_unregister_marks_offline_event() -> None:
    hub = AgentHubManager()
    await hub.register("z1", tenant_id="t")
    q = await hub.shadow.subscribe(agent_id="z1")
    assert await hub.unregister("z1") is True
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == ShadowEventType.STATUS
    assert ev.payload["status"] == AgentStatus.OFFLINE.value
    assert hub.get("z1") is None
