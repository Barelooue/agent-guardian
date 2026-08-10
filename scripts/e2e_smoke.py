"""In-process e2e smoke using httpx ASGI transport (no subprocess)."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx

from agent_guardian.daemon.app import create_app
from agent_guardian.schemas import (
    ChannelName,
    DecisionSource,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    MessageType,
    Option,
    make_envelope,
    utc_now,
)


async def main() -> None:
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    app = create_app(db_path=db.name, enable_terminal_stdin=False)

    # Older httpx has no lifespan=; app middleware bootstraps state on first request.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        assert health.status_code == 200, health.text

        req = InterventionRequest(
            client_request_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            reason="e2e smoke",
            options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
            timeout_seconds=30,
            channels=[ChannelName.TERMINAL],
        )
        env = make_envelope(MessageType.INTERVENTION_CREATE, req)
        r1 = await client.post("/v1/interventions", json=env.model_dump(mode="json"))
        assert r1.status_code == 200, r1.text
        created = r1.json()["payload"]
        assert created["reused"] is False
        iid = created["intervention_id"]

        r2 = await client.post("/v1/interventions", json=env.model_dump(mode="json"))
        assert r2.status_code == 200, r2.text
        assert r2.json()["payload"]["reused"] is True
        assert r2.json()["payload"]["intervention_id"] == iid

        # allow background deliver to mark AWAITING_HUMAN
        for _ in range(20):
            st = await client.get(f"/v1/interventions/{iid}")
            status = st.json()["payload"]["status"]
            if status == InterventionStatus.AWAITING_HUMAN.value:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(f"not awaiting: {st.text}")

        decision = InterventionDecision(
            intervention_id=iid,
            option_id="approve",
            source=DecisionSource.TERMINAL,
            decided_at=utc_now(),
        )
        denv = make_envelope(MessageType.INTERVENTION_DECISION, decision)
        rd = await client.post(
            f"/v1/interventions/{iid}/decision",
            json=denv.model_dump(mode="json"),
        )
        assert rd.status_code == 200, rd.text
        assert rd.json()["payload"]["status"] == "RESOLVED"

        # same decision → idempotent 200 (protocol §4.4)
        rd2 = await client.post(
            f"/v1/interventions/{iid}/decision",
            json=denv.model_dump(mode="json"),
        )
        assert rd2.status_code == 200, rd2.text
        assert rd2.json()["payload"]["idempotent"] is True

        # conflicting decision after RESOLVED → AG_ALREADY_TERMINAL
        conflict = decision.model_copy(update={"option_id": "deny"})
        cenv = make_envelope(MessageType.INTERVENTION_DECISION, conflict)
        rd3 = await client.post(
            f"/v1/interventions/{iid}/decision",
            json=cenv.model_dump(mode="json"),
        )
        assert rd3.status_code == 409, rd3.text
        assert rd3.json()["payload"]["code"] == "AG_ALREADY_TERMINAL"

        # cancel on fresh intervention (sdk timeout sync path)
        req2 = InterventionRequest(
            reason="cancel path",
            options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
            timeout_seconds=30,
            channels=[ChannelName.TERMINAL],
        )
        cenv = make_envelope(MessageType.INTERVENTION_CREATE, req2)
        rc = await client.post("/v1/interventions", json=cenv.model_dump(mode="json"))
        iid2 = rc.json()["payload"]["intervention_id"]
        from agent_guardian.schemas import CancelReason, InterventionCancel

        cancel = InterventionCancel(
            intervention_id=iid2,
            reason=CancelReason.CLIENT_TIMEOUT,
            detail="e2e",
        )
        xenv = make_envelope(MessageType.INTERVENTION_CANCEL, cancel)
        rx = await client.post(
            f"/v1/interventions/{iid2}/cancel",
            json=xenv.model_dump(mode="json"),
        )
        assert rx.status_code == 200, rx.text
        assert rx.json()["payload"]["status"] == "CANCELLED"

        # Close background tasks + SQLite worker so the process can exit cleanly
        if hasattr(app.state, "service") and app.state.service is not None:
            await app.state.service.aclose()
        if hasattr(app.state, "db") and app.state.db is not None:
            await app.state.db.close()
            app.state.service = None

    print("E2E OK: reused + CAS + cancel sync paths")


if __name__ == "__main__":
    asyncio.run(main())
