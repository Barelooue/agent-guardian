"""CAS / idempotent client_request_id tests against SQLite."""

from __future__ import annotations

import pytest

from agent_guardian.daemon.db import init_db
from agent_guardian.daemon.store import InterventionStore, StoreError
from agent_guardian.schemas import (
    DecisionSource,
    ErrorCode,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    Option,
    utc_now,
)


@pytest.fixture
async def store(tmp_path):
    conn = await init_db(str(tmp_path / "test.db"))
    s = InterventionStore(conn)
    yield s
    await conn.close()


@pytest.mark.asyncio
async def test_client_request_id_reused(store: InterventionStore) -> None:
    req = InterventionRequest(
        client_request_id="11111111-1111-4111-8111-111111111111",
        reason="test",
        options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
        timeout_seconds=30,
    )
    first = await store.create(req)
    second = await store.create(req)
    assert first.reused is False
    assert second.reused is True
    assert first.intervention_id == second.intervention_id


@pytest.mark.asyncio
async def test_decision_vs_timeout_cas(store: InterventionStore) -> None:
    req = InterventionRequest(
        reason="race",
        options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
        timeout_seconds=30,
    )
    created = await store.create(req)
    iid = created.intervention_id
    await store.mark_awaiting(
        iid,
        channel=__import__("agent_guardian.schemas", fromlist=["ChannelName"]).ChannelName.TERMINAL,
    )

    decision = InterventionDecision(
        intervention_id=iid,
        option_id="approve",
        source=DecisionSource.TERMINAL,
        decided_at=utc_now(),
    )
    won = await store.cas_update(iid, target=InterventionStatus.RESOLVED, decision=decision)
    assert won.status == InterventionStatus.RESOLVED
    assert won.idempotent is False

    with pytest.raises(StoreError) as exc:
        await store.cas_update(iid, target=InterventionStatus.TIMEOUT)
    assert exc.value.code == ErrorCode.AG_ALREADY_TERMINAL
