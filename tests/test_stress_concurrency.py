"""Hardening: concurrent ask_human / CAS stress + screenshot degrade."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from agent_guardian import AgentGuardian, InterventionDeniedError
from agent_guardian.client.guardian import AgentGuardian as AG
from agent_guardian.daemon.app import create_app
from agent_guardian.daemon.config import DaemonConfig
from agent_guardian.schemas import (
    ChannelName,
    DecisionSource,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    Option,
    utc_now,
)
from agent_guardian.snapshot import CaptureError, try_capture_snapshot


@pytest.fixture
async def asgi_ready(tmp_path: Path):
    """ASGI app with Daemon state bootstrapped (avoids concurrent init_db races)."""
    app = create_app(
        db_path=tmp_path / "stress.db",
        enable_terminal_stdin=False,
        config=DaemonConfig(default_channels=("terminal",)),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert hasattr(app.state, "store")
        yield app, transport
    if hasattr(app.state, "service") and app.state.service is not None:
        await app.state.service.aclose()
    if hasattr(app.state, "db") and app.state.db is not None:
        await app.state.db.close()


@pytest.mark.asyncio
async def test_concurrent_ask_human_cas_no_deadlock(asgi_ready) -> None:
    """12 coroutines create interventions; humans resolve/deny concurrently."""
    app, transport = asgi_ready
    n = 12
    store = app.state.store

    async with AgentGuardian(
        "http://test",
        transport=transport,
        local_terminal_prompt=False,
        poll_interval=0.05,
        prefer_websocket=False,
    ) as guardian:

        async def one(i: int) -> str:
            crid = str(uuid4())
            task = asyncio.create_task(
                guardian.ask_human(
                    reason=f"stress-{i}",
                    title=f"Stress {i}",
                    options=[
                        Option(id="approve", label="OK"),
                        Option(id="deny", label="No"),
                    ],
                    timeout=30,
                    channels=["terminal"],
                    client_request_id=crid,
                    deny_option_ids={"deny"},
                )
            )
            # Wait until NOTIFIED/AWAITING_HUMAN (PENDING cannot CAS→RESOLVED)
            iid = None
            for _ in range(100):
                opens = await store.list_open()
                for rec in opens:
                    if rec.client_request_id == crid and rec.status in (
                        InterventionStatus.AWAITING_HUMAN,
                        InterventionStatus.NOTIFIED,
                    ):
                        iid = rec.intervention_id
                        break
                if iid:
                    break
                await asyncio.sleep(0.05)
            assert iid, f"intervention not ready for stress-{i}"

            option = "deny" if i % 2 == 0 else "approve"
            await guardian._http.decide(
                InterventionDecision(
                    intervention_id=iid,
                    option_id=option,
                    source=DecisionSource.WEB_UI,
                    decided_at=utc_now(),
                    operator_id=f"stress:{i}",
                )
            )
            try:
                updated = await task
                return f"ok:{updated.selected_option_id}"
            except InterventionDeniedError:
                return "denied"

        results = await asyncio.gather(*(one(i) for i in range(n)))

    assert len(results) == n
    assert all(r.startswith("ok:") or r == "denied" for r in results)
    assert sum(1 for r in results if r == "denied") == n // 2
    assert sum(1 for r in results if r.startswith("ok:")) == n - n // 2

    left = await store.list_open()
    assert left == []

    async with app.state.db.execute(
        "SELECT status, COUNT(*) AS c FROM interventions GROUP BY status"
    ) as cur:
        rows = await cur.fetchall()
    statuses = {r["status"]: r["c"] for r in rows}
    assert sum(statuses.values()) == n
    assert InterventionStatus.PENDING.value not in statuses
    assert InterventionStatus.AWAITING_HUMAN.value not in statuses
    assert statuses.get(InterventionStatus.RESOLVED.value, 0) == n


@pytest.mark.asyncio
async def test_concurrent_duplicate_client_request_id_reused(asgi_ready) -> None:
    """Same client_request_id under concurrency → single intervention, reused=true."""
    app, transport = asgi_ready
    crid = str(uuid4())

    async with AgentGuardian(
        "http://test",
        transport=transport,
        local_terminal_prompt=False,
        prefer_websocket=False,
    ) as g:

        async def create_once() -> str:
            created = await g._http.create(
                InterventionRequest(
                    client_request_id=crid,
                    reason="dup",
                    options=[
                        Option(id="approve", label="OK"),
                        Option(id="deny", label="No"),
                    ],
                    timeout_seconds=30,
                    channels=[ChannelName.TERMINAL],
                )
            )
            return created.intervention_id

        ids = await asyncio.gather(*(create_once() for _ in range(10)))

    assert len(set(ids)) == 1

    async with app.state.db.execute(
        "SELECT COUNT(*) AS c FROM interventions WHERE client_request_id = ?",
        (crid,),
    ) as cur:
        row = await cur.fetchone()
    assert row["c"] == 1


def test_try_capture_snapshot_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kwargs):
        raise CaptureError("no display")

    monkeypatch.setattr("agent_guardian.snapshot.capture_snapshot", _boom)
    assert try_capture_snapshot() is None


def test_include_screenshot_degrades_without_unhandled(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kwargs):
        raise CaptureError("permission denied")

    monkeypatch.setattr("agent_guardian.snapshot.capture_snapshot", _boom)
    req = AG._build_request(
        reason="x",
        options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
        title="t",
        context={},
        timeout=30,
        channels=["terminal"],
        client_request_id=str(uuid4()),
        agent_id=None,
        include_screenshot=True,
    )
    assert req.snapshot is None
    assert req.metadata.get("snapshot_error") == "capture_unavailable"
