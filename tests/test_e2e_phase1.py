"""Phase 1 acceptance: guard deny, CAS race, Daemon persistence restart."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from agent_guardian import AgentGuardian, InterventionDeniedError
from agent_guardian.daemon.app import create_app
from agent_guardian.daemon.db import init_db
from agent_guardian.daemon.service import InterventionService
from agent_guardian.daemon.store import InterventionStore, StoreError
from agent_guardian.schemas import (
    ChannelName,
    DecisionSource,
    ErrorCode,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    Option,
    utc_now,
)


def _options() -> list[Option]:
    return [
        Option(id="approve", label="确认"),
        Option(id="deny", label="拒绝并回滚"),
        Option(id="retry_later", label="稍后重试"),
    ]


@pytest.fixture
async def asgi_app(tmp_path: Path):
    db = tmp_path / "e2e.db"
    app = create_app(db_path=db, enable_terminal_stdin=False)
    yield app
    if hasattr(app.state, "service") and app.state.service is not None:
        await app.state.service.aclose()
    if hasattr(app.state, "db") and app.state.db is not None:
        await app.state.db.close()


# ---------------------------------------------------------------------------
# Test 1: Happy Path & Context Manager Exception (deny → InterventionDeniedError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_deny_raises_intervention_denied(asgi_app) -> None:
    transport = httpx.ASGITransport(app=asgi_app)
    async with AgentGuardian(
        "http://test",
        transport=transport,
        local_terminal_prompt=False,
        poll_interval=0.05,
    ) as guardian:

        async def _human_denies() -> None:
            # Wait until intervention exists, then POST deny (simulates Telegram tap)
            for _ in range(40):
                # Discover open intervention via create idempotency is hard;
                # instead: race create inside guard — poll by posting after short delay
                await asyncio.sleep(0.05)
                # Use direct ASGI client against app state store
                store: InterventionStore | None = getattr(asgi_app.state, "store", None)
                if store is None:
                    continue
                opens = await store.list_open()
                if not opens:
                    continue
                iid = opens[0].intervention_id
                await guardian._http.decide(
                    InterventionDecision(
                        intervention_id=iid,
                        option_id="deny",
                        source=DecisionSource.TELEGRAM,
                        decided_at=utc_now(),
                        operator_id="tg:e2e",
                    )
                )
                return
            raise AssertionError("no open intervention for deny callback")

        deny_task = asyncio.create_task(_human_denies())
        with pytest.raises(InterventionDeniedError) as exc_info:
            async with guardian.guard(
                reason="高风险支付，请确认",
                options=_options(),
                timeout=10,
                channels=["terminal"],
                deny_option_ids={"deny"},
            ) as _decision:
                raise AssertionError("guard body must not run on deny")
        await deny_task
        assert exc_info.value.code == "AG_DENIED"
        assert "deny" in str(exc_info.value).lower() or "denied" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test 2: Timeout vs Decision Race (CAS / AG_ALREADY_TERMINAL)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_vs_telegram_decision_cas_race(tmp_path: Path) -> None:
    conn = await init_db(str(tmp_path / "race.db"))
    store = InterventionStore(conn)
    try:
        created = await store.create(
            InterventionRequest(
                reason="race condition probe",
                options=_options(),
                timeout_seconds=60,
                channels=[ChannelName.TERMINAL],
            )
        )
        iid = created.intervention_id
        await store.mark_awaiting(iid, channel=ChannelName.TERMINAL)

        decision = InterventionDecision(
            intervention_id=iid,
            option_id="approve",
            source=DecisionSource.TELEGRAM,
            decided_at=utc_now(),
            operator_id="tg:race",
            channel_message_id="cb:1",
        )

        async def _decide() -> tuple[str, object]:
            try:
                updated = await store.cas_update(
                    iid,
                    target=InterventionStatus.RESOLVED,
                    decision=decision,
                )
                return ("ok", updated)
            except StoreError as exc:
                return ("err", exc)

        async def _timeout() -> tuple[str, object]:
            try:
                updated = await store.cas_update(
                    iid,
                    target=InterventionStatus.TIMEOUT,
                )
                return ("ok", updated)
            except StoreError as exc:
                return ("err", exc)

        # Fire "simultaneously" — lock serializes; exactly one must win.
        results = await asyncio.gather(_decide(), _timeout())
        oks = [r for r in results if r[0] == "ok"]
        errs = [r for r in results if r[0] == "err"]

        assert len(oks) == 1, results
        assert len(errs) == 1, results

        winner = oks[0][1]
        loser = errs[0][1]
        assert isinstance(loser, StoreError)
        assert loser.code == ErrorCode.AG_ALREADY_TERMINAL

        final = await store.get(iid)
        assert final is not None
        assert final.status in {
            InterventionStatus.RESOLVED,
            InterventionStatus.TIMEOUT,
        }
        assert final.status == winner.status
        # Exactly one terminal write
        assert final.version >= 2
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Test 3: Daemon Restarts with Persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daemon_restart_recovers_pending_and_expired(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.db"

    # --- Boot #1: create an open intervention, then "crash" (close conn) ---
    conn1 = await init_db(str(db_path))
    store1 = InterventionStore(conn1)
    created = await store1.create(
        InterventionRequest(
            client_request_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            reason="persist me across restart",
            options=_options(),
            timeout_seconds=300,
            channels=[ChannelName.TERMINAL],
        )
    )
    iid = created.intervention_id
    await store1.mark_awaiting(iid, channel=ChannelName.TERMINAL)
    open_before = await store1.get(iid)
    assert open_before is not None
    assert open_before.status == InterventionStatus.AWAITING_HUMAN
    await conn1.close()

    # --- Boot #2: new process recovers open state ---
    app = create_app(db_path=db_path, enable_terminal_stdin=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # First request bootstraps lifespan-equivalent state + recover_open
        health = await client.get("/health")
        assert health.status_code == 200

        got = await client.get(f"/v1/interventions/{iid}")
        assert got.status_code == 200, got.text
        payload = got.json()["payload"]
        assert payload["intervention_id"] == iid
        assert payload["status"] in {
            InterventionStatus.PENDING.value,
            InterventionStatus.NOTIFIED.value,
            InterventionStatus.AWAITING_HUMAN.value,
        }

        # --- Create a second intervention already expired, restart recovery → TIMEOUT ---
        store: InterventionStore = app.state.store
        expired = await store.create(
            InterventionRequest(
                reason="already expired",
                options=_options(),
                timeout_seconds=1,
                channels=[ChannelName.TERMINAL],
            )
        )
        eid = expired.intervention_id
        await store.mark_awaiting(eid, channel=ChannelName.TERMINAL)
        # Force expires_at into the past (WAL row rewrite)
        past = (utc_now() - timedelta(seconds=30)).isoformat()
        await store._conn.execute(
            "UPDATE interventions SET expires_at = ? WHERE intervention_id = ?",
            (past, eid),
        )
        await store._conn.commit()

    # Close app #2
    if hasattr(app.state, "service") and app.state.service is not None:
        await app.state.service.aclose()
    if hasattr(app.state, "db") and app.state.db is not None:
        await app.state.db.close()
        app.state.service = None

    # --- Boot #3: recover_open should CAS-timeout the expired row ---
    app3 = create_app(db_path=db_path, enable_terminal_stdin=False)
    transport3 = httpx.ASGITransport(app=app3)
    try:
        async with httpx.AsyncClient(transport=transport3, base_url="http://test") as client:
            await client.get("/health")
            # Explicit recover (also runs on bootstrap)
            service: InterventionService = app3.state.service
            await service.recover_open()

            timed = await client.get(f"/v1/interventions/{eid}")
            assert timed.status_code == 200, timed.text
            assert timed.json()["payload"]["status"] == InterventionStatus.TIMEOUT.value

            # Original non-expired intervention still queryable (open or re-delivered)
            still = await client.get(f"/v1/interventions/{iid}")
            assert still.status_code == 200
            assert still.json()["payload"]["intervention_id"] == iid
    finally:
        if hasattr(app3.state, "service") and app3.state.service is not None:
            await app3.state.service.aclose()
        if hasattr(app3.state, "db") and app3.state.db is not None:
            await app3.state.db.close()
