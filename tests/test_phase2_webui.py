"""Phase 2 Web UI API: list open + media + decide via web_ui."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from agent_guardian.daemon.app import create_app
from agent_guardian.daemon.config import DaemonConfig
from agent_guardian.schemas import (
    DecisionSource,
    InterventionDecision,
    InterventionRequest,
    MessageType,
    Option,
    SnapshotRef,
    make_envelope,
    utc_now,
)


def _tiny_jpeg_b64() -> str:
    img = Image.new("RGB", (32, 24), color=(10, 200, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.fixture
async def client(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "p2.db",
        enable_terminal_stdin=False,
        config=DaemonConfig(default_channels=("terminal",)),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    if hasattr(app.state, "service") and app.state.service is not None:
        await app.state.service.aclose()
    if hasattr(app.state, "db") and app.state.db is not None:
        await app.state.db.close()


@pytest.mark.asyncio
async def test_list_open_and_media_and_web_decide(client: httpx.AsyncClient) -> None:
    b64 = _tiny_jpeg_b64()
    req = InterventionRequest(
        reason="phase2 ui",
        title="UI 确认",
        options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
        timeout_seconds=60,
        snapshot=SnapshotRef(
            content_type="image/jpeg",
            width=32,
            height=24,
            size_bytes=100,
            sha256="c" * 64,
            base64=b64,
        ),
    )
    env = make_envelope(MessageType.INTERVENTION_CREATE, req)
    r = await client.post("/v1/interventions", json=env.model_dump(mode="json"))
    assert r.status_code == 200, r.text
    iid = r.json()["payload"]["intervention_id"]

    listed = await client.get("/v1/interventions?status=open")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["intervention_id"] == iid
    assert items[0]["snapshot"]["url"].startswith("/v1/media/")
    assert items[0]["snapshot"]["base64"] is None

    media_url = items[0]["snapshot"]["url"]
    img = await client.get(media_url)
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")

    ui = await client.get("/ui/")
    assert ui.status_code == 200
    assert "Control Plane" in ui.text or "干预队列" in ui.text
    assert "Swarm" in ui.text or "shot-canvas" in ui.text
    assert ui.status_code == 200

    decision = InterventionDecision(
        intervention_id=iid,
        option_id="approve",
        source=DecisionSource.WEB_UI,
        decided_at=utc_now(),
        operator_id="web:test",
    )
    denv = make_envelope(MessageType.INTERVENTION_DECISION, decision)
    rd = await client.post(
        f"/v1/interventions/{iid}/decision",
        json=denv.model_dump(mode="json"),
    )
    assert rd.status_code == 200
    assert rd.json()["payload"]["status"] == "RESOLVED"

    listed2 = await client.get("/v1/interventions?status=open")
    assert listed2.json()["items"] == []
