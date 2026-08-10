"""Phase 5 guard_step + Phase 6 spatial / rollback unit tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from agent_guardian import AgentGuardian
from agent_guardian.client.checkpoints import CheckpointStack
from agent_guardian.client.guardian import GuardStepResult
from agent_guardian.daemon.app import create_app
from agent_guardian.daemon.config import DaemonConfig
from agent_guardian.schemas import (
    DecisionSource,
    InterventionDecision,
    Option,
    SpatialAnnotation,
    utc_now,
)
from agent_guardian.smart import LoopDetector, SmartInterventionEngine
from agent_guardian.ui import SpatialPromptInjector


@pytest.fixture
async def asgi_ready(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "g6.db",
        enable_terminal_stdin=False,
        config=DaemonConfig(default_channels=("terminal",)),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        yield app, transport
    if getattr(app.state, "service", None) is not None:
        await app.state.service.aclose()
    if getattr(app.state, "db", None) is not None:
        await app.state.db.close()


def test_checkpoint_rollback() -> None:
    stack = CheckpointStack()
    stack.push({"n": 0})
    stack.push({"n": 1})
    stack.push({"n": 2})
    assert stack.rollback(1) == {"n": 1}
    assert stack.rollback(1) == {"n": 0}
    with pytest.raises(IndexError):
        stack.rollback(1)


def test_spatial_prompt_point_and_bbox() -> None:
    point = SpatialAnnotation(type="point", x=0.32, y=0.15, label="click_here")
    text = SpatialPromptInjector.to_prompt(point)
    assert "POINT" in text
    assert "0.3200" in text
    assert "click_here" in text

    box = SpatialAnnotation(type="bbox", x=0.1, y=0.2, x2=0.5, y2=0.6, label="focus_region")
    btext = SpatialPromptInjector.to_prompt(box, som_id=2)
    assert "BBOX" in btext
    assert "[2]" in btext
    struct = SpatialPromptInjector.to_structured(box)
    assert struct["cx"] == pytest.approx(0.3)


def test_ui_serves_canvas_markup() -> None:
    html = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_guardian"
        / "daemon"
        / "static"
        / "ui"
        / "index.html"
    ).read_text(encoding="utf-8")
    assert "shot-canvas" in html
    assert "mode-bbox" in html
    assert "rollback_steps" in html
    assert "spatial" in html


@pytest.mark.asyncio
async def test_guard_step_auto_ask_on_loop(asgi_ready) -> None:
    app, transport = asgi_ready
    store = app.state.store
    engine = SmartInterventionEngine(loop=LoopDetector(repeat_threshold=3))

    async with AgentGuardian(
        "http://test",
        transport=transport,
        local_terminal_prompt=False,
        poll_interval=0.05,
        prefer_websocket=False,
        smart_engine=engine,
    ) as guardian:

        async def approve_when_open() -> None:
            from agent_guardian.schemas import InterventionStatus

            for _ in range(100):
                opens = await store.list_open()
                for rec in opens:
                    if rec.status in (
                        InterventionStatus.AWAITING_HUMAN,
                        InterventionStatus.NOTIFIED,
                    ):
                        await guardian._http.decide(
                            InterventionDecision(
                                intervention_id=rec.intervention_id,
                                option_id="approve",
                                source=DecisionSource.WEB_UI,
                                decided_at=utc_now(),
                                spatial=SpatialAnnotation(
                                    type="point", x=0.4, y=0.5, label="click_here"
                                ),
                            )
                        )
                        return
                await asyncio.sleep(0.05)
            raise AssertionError("no open intervention")

        # First two: no intervene
        r1 = await guardian.guard_step(action_name="click", target="#a")
        r2 = await guardian.guard_step(action_name="click", target="#a")
        assert r1.proceeded and not r1.signal.intervene
        assert r2.proceeded and not r2.signal.intervene

        task = asyncio.create_task(approve_when_open())
        r3 = await guardian.guard_step(action_name="click", target="#a")
        await task
        assert isinstance(r3, GuardStepResult)
        assert r3.signal.intervene is True
        assert r3.proceeded is True
        assert r3.spatial is not None
        assert r3.spatial_prompt is not None
        assert "POINT" in r3.spatial_prompt


@pytest.mark.asyncio
async def test_guard_step_risk_and_rollback_on_deny(asgi_ready) -> None:
    app, transport = asgi_ready
    store = app.state.store

    async with AgentGuardian(
        "http://test",
        transport=transport,
        local_terminal_prompt=False,
        poll_interval=0.05,
        prefer_websocket=False,
    ) as guardian:
        guardian.checkpoint({"step": 0})
        guardian.checkpoint({"step": 1})
        guardian.checkpoint({"step": 2})

        async def deny_with_rollback() -> None:
            from agent_guardian.schemas import InterventionStatus

            for _ in range(100):
                opens = await store.list_open()
                for rec in opens:
                    if rec.status in (
                        InterventionStatus.AWAITING_HUMAN,
                        InterventionStatus.NOTIFIED,
                    ):
                        await guardian._http.decide(
                            InterventionDecision(
                                intervention_id=rec.intervention_id,
                                option_id="deny",
                                source=DecisionSource.WEB_UI,
                                decided_at=utc_now(),
                                rollback_steps=1,
                            )
                        )
                        return
                await asyncio.sleep(0.05)

        task = asyncio.create_task(deny_with_rollback())
        result = await guardian.guard_step(
            action_name="run",
            command="rm -rf /tmp/data",
        )
        await task
        assert result.proceeded is False
        assert result.signal.intervene is True
        assert result.restored_state == {"step": 1}


@pytest.mark.asyncio
async def test_decision_persists_spatial(asgi_ready) -> None:
    """Web UI payload shape: spatial on InterventionDecision round-trips via store."""
    app, transport = asgi_ready
    async with AgentGuardian(
        "http://test",
        transport=transport,
        local_terminal_prompt=False,
        poll_interval=0.05,
        prefer_websocket=False,
        enable_smart=False,
    ) as guardian:
        from agent_guardian.schemas import InterventionStatus

        async def decide() -> None:
            for _ in range(80):
                opens = await app.state.store.list_open()
                if opens and opens[0].status in (
                    InterventionStatus.AWAITING_HUMAN,
                    InterventionStatus.NOTIFIED,
                ):
                    await guardian._http.decide(
                        InterventionDecision(
                            intervention_id=opens[0].intervention_id,
                            option_id="approve",
                            source=DecisionSource.WEB_UI,
                            decided_at=utc_now(),
                            spatial=SpatialAnnotation(
                                type="bbox", x=0.1, y=0.1, x2=0.4, y2=0.5, label="box"
                            ),
                            note="canvas mark",
                        )
                    )
                    return
                await asyncio.sleep(0.05)

        task = asyncio.create_task(decide())
        # force path via ask_human directly
        updated = await guardian.ask_human(
            reason="mark me",
            options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
            title="canvas",
            client_request_id=str(uuid4()),
        )
        await task
        assert updated.decision is not None
        assert updated.decision.spatial is not None
        assert updated.decision.spatial.type == "bbox"
