"""Unit tests for Browser-Use adapter (no browser-use / playwright required)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_guardian import AgentGuardian, InterventionDeniedError
from agent_guardian.adapters.browser_use import (
    GuardianBrowserHook,
    bytes_to_snapshot,
)
from agent_guardian.daemon.app import create_app
from agent_guardian.daemon.config import DaemonConfig
from agent_guardian.schemas import DecisionSource, InterventionDecision, utc_now


@pytest.fixture
async def asgi_ready(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "bu.db",
        enable_terminal_stdin=False,
        config=DaemonConfig(default_channels=("terminal",)),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        yield app, transport
    if hasattr(app.state, "service") and app.state.service is not None:
        await app.state.service.aclose()
    if hasattr(app.state, "db") and app.state.db is not None:
        await app.state.db.close()


def test_bytes_to_snapshot_png_magic() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"xxxx"
    snap = bytes_to_snapshot(png, content_type="image/png")
    assert snap.content_type == "image/png"
    assert snap.base64


@pytest.mark.asyncio
async def test_hook_approve_with_screenshot(asgi_ready) -> None:
    app, transport = asgi_ready
    store = app.state.store

    async with AgentGuardian(
        "http://test",
        transport=transport,
        local_terminal_prompt=False,
        poll_interval=0.05,
        prefer_websocket=False,
    ) as guardian:
        hook = GuardianBrowserHook(guardian, timeout=30)

        async def decide() -> None:
            import asyncio

            from agent_guardian.schemas import InterventionStatus

            for _ in range(80):
                opens = await store.list_open()
                for rec in opens:
                    if rec.request.context.get("action") != "click_pay":
                        continue
                    if rec.status not in (
                        InterventionStatus.AWAITING_HUMAN,
                        InterventionStatus.NOTIFIED,
                    ):
                        continue
                    await guardian._http.decide(
                        InterventionDecision(
                            intervention_id=rec.intervention_id,
                            option_id="approve",
                            source=DecisionSource.WEB_UI,
                            decided_at=utc_now(),
                        )
                    )
                    return
                await asyncio.sleep(0.05)
            raise AssertionError("no open intervention")

        import asyncio

        task = asyncio.create_task(decide())
        option = await hook.confirm_sensitive_action(
            reason="pay?",
            action="click_pay",
            url="file:///checkout",
            screenshot=b"\xff\xd8\xff" + b"jpegdemo",
        )
        await task
        assert option == "approve"


@pytest.mark.asyncio
async def test_hook_deny_raises(asgi_ready) -> None:
    app, transport = asgi_ready
    store = app.state.store

    async with AgentGuardian(
        "http://test",
        transport=transport,
        local_terminal_prompt=False,
        poll_interval=0.05,
        prefer_websocket=False,
    ) as guardian:
        hook = GuardianBrowserHook(guardian, timeout=30)
        import asyncio

        async def decide() -> None:
            from agent_guardian.schemas import InterventionStatus

            for _ in range(80):
                opens = await store.list_open()
                for rec in opens:
                    if rec.status not in (
                        InterventionStatus.AWAITING_HUMAN,
                        InterventionStatus.NOTIFIED,
                    ):
                        continue
                    await guardian._http.decide(
                        InterventionDecision(
                            intervention_id=rec.intervention_id,
                            option_id="deny",
                            source=DecisionSource.WEB_UI,
                            decided_at=utc_now(),
                        )
                    )
                    return
                await asyncio.sleep(0.05)

        task = asyncio.create_task(decide())
        with pytest.raises(InterventionDeniedError):
            await hook.confirm_sensitive_action(
                reason="deny me",
                action="click_pay",
            )
        await task
