"""Phase 7 Daemon swarm API + Phase 8 align tests."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from agent_guardian.align import AgentBenchmark, DatasetCurator, TakeoverTrace, write_train_recipe
from agent_guardian.daemon.app import create_app
from agent_guardian.daemon.config import DaemonConfig
from agent_guardian.daemon.db import init_db
from agent_guardian.daemon.store import InterventionStore
from agent_guardian.schemas import (
    ChannelName,
    DecisionSource,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    Option,
    OptionStyle,
    SpatialAnnotation,
    utc_now,
)


@pytest.fixture
async def asgi_app(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "swarm.db",
        enable_terminal_stdin=False,
        config=DaemonConfig(default_channels=("terminal",)),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert "swarm_agents" in (await client.get("/health")).json()
        yield app, client
    if getattr(app.state, "service", None):
        await app.state.service.aclose()
    if getattr(app.state, "db", None):
        await app.state.db.close()


@pytest.mark.asyncio
async def test_swarm_rest_register_observe_takeover(asgi_app) -> None:
    _app, client = asgi_app
    r = await client.post(
        "/api/swarm/agents",
        json={"agent_id": "browser-1", "agent_type": "browser-use", "tenant_id": "demo"},
    )
    assert r.status_code == 200
    assert r.json()["agent_id"] == "browser-1"

    o = await client.post(
        "/api/swarm/agents/browser-1/observe",
        json={
            "thought": "see captcha",
            "action": "click:#solve",
            "screenshot_url": "/v1/media/demo.jpg",
        },
    )
    assert o.status_code == 200
    assert len(o.json()["events"]) == 3

    listed = await client.get("/api/swarm/agents")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert any(i["agent_id"] == "browser-1" for i in items)
    card = next(i for i in items if i["agent_id"] == "browser-1")
    assert card["last_thought"] == "see captcha"
    assert card["last_action"] == "click:#solve"

    t = await client.post(
        "/api/swarm/agents/browser-1/takeover",
        json={"instruction": "Stop before pay", "role": "operator"},
    )
    assert t.status_code == 200
    assert t.json()["kind"] in {"force_pause", "inject_instruction"}
    detail = await client.get("/api/swarm/agents/browser-1")
    assert detail.json()["status"] == "takeover"

    # Force Takeover auto-persisted for export-dpo
    stored = await _app.state.takeover_store.get(t.json()["signal_id"])
    assert stored is not None
    assert stored.instruction == "Stop before pay"
    assert stored.before_action == "click:#solve"
    assert stored.screenshot_path == "/v1/media/demo.jpg"

    out = Path(_app.state.db_path).parent / "from_api_dpo.jsonl"
    stats = await DatasetCurator(
        media_root=Path(_app.state.db_path).parent / "media",
        format="qwen2_vl",
    ).export_from_db(_app.state.db_path, out)
    assert stats.takeovers >= 1
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    takeover_rows = [r for r in rows if r.get("source") == "agent_guardian_takeover"]
    assert any("Stop before pay" in r["chosen"] for r in takeover_rows)


@pytest.mark.asyncio
async def test_swarm_ws_with_starlette_client(tmp_path: Path) -> None:
    app = create_app(
        db_path=tmp_path / "ws.db",
        enable_terminal_stdin=False,
        config=DaemonConfig(default_channels=("terminal",)),
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        client.post(
            "/api/swarm/agents",
            json={"agent_id": "ws-1", "tenant_id": "demo"},
        )
        with client.websocket_connect("/ws/swarm?tenant_id=demo") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            client.post(
                "/api/swarm/agents/ws-1/observe",
                json={"thought": "hello from agent", "action": "noop"},
            )
            found_thought = False
            for _ in range(5):
                msg = ws.receive_json()
                if msg.get("type") == "shadow" and msg["event"]["type"] == "thought":
                    found_thought = True
                    break
            assert found_thought
            ws.send_json(
                {
                    "type": "takeover",
                    "agent_id": "ws-1",
                    "instruction": "pause now",
                    "role": "operator",
                }
            )
            ack = None
            for _ in range(8):
                msg = ws.receive_json()
                if msg.get("type") == "takeover_ack":
                    ack = msg
                    break
            assert ack is not None
            assert ack["agent_id"] == "ws-1"


def test_ui_contains_swarm_dashboard() -> None:
    html = (
        Path(__file__).resolve().parents[1]
        / "src/agent_guardian/daemon/static/ui/index.html"
    ).read_text(encoding="utf-8")
    assert "Swarm 大厅" in html
    assert "Force Takeover" in html
    assert "/ws/swarm" in html
    assert "swarm-grid" in html


@pytest.mark.asyncio
async def test_dataset_curator_spatial_and_takeover(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    media = tmp_path / "media"
    media.mkdir()
    conn = await init_db(str(db))
    store = InterventionStore(conn)
    from agent_guardian.daemon.takeover_store import TakeoverStore

    created = await store.create(
        InterventionRequest(
            reason="pay?",
            title="risk",
            options=[
                Option(id="approve", label="OK", style=OptionStyle.PRIMARY),
                Option(id="deny", label="No", style=OptionStyle.DANGER, destructive=True),
            ],
            agent_id="browser-1",
            metadata={"smart_code": "risk_high"},
        )
    )
    await store.mark_awaiting(
        created.intervention_id,
        channel=ChannelName.TERMINAL,
    )
    await store.cas_update(
        created.intervention_id,
        target=InterventionStatus.RESOLVED,
        decision=InterventionDecision(
            intervention_id=created.intervention_id,
            option_id="approve",
            source=DecisionSource.WEB_UI,
            decided_at=utc_now(),
            spatial=SpatialAnnotation(type="point", x=0.3, y=0.2, label="click_here"),
            rollback_steps=None,
        ),
    )
    img = media / f"{created.intervention_id}.jpg"
    img.write_bytes(b"\xff\xd8\xffjpeg")
    await TakeoverStore(conn).insert(
        signal_id="sig-db-1",
        agent_id="browser-1",
        kind="inject_instruction",
        instruction="Stop before pay",
        before_action="click:#pay",
        before_thought="about to pay",
        screenshot_path=str(img),
        operator_id="web:console",
        tenant_id="demo",
        agent_type="browser-use",
        meta={"role": "operator"},
    )
    await conn.close()

    out = tmp_path / "swarm_dpo.jsonl"
    curator = DatasetCurator(media_root=media, format="qwen2_vl")
    # In-memory traces still supported; DB takeovers export automatically
    curator.add_takeover(
        TakeoverTrace(
            agent_id="browser-1",
            instruction="Manual extra",
            before_action="noop",
            signal_id="sig-mem-1",
        )
    )
    stats = await curator.export_from_db(db, out)
    assert stats.written == 3
    assert stats.spatial == 1
    assert stats.takeovers == 2
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert any("POINT" in r["prompt"] or "click_here" in r["chosen"] for r in rows)
    takeover_rows = [r for r in rows if r["source"] == "agent_guardian_takeover"]
    assert any("Stop before pay" in r["chosen"] for r in takeover_rows)
    assert any(r["id"] == "takeover:sig-db-1" for r in takeover_rows)


def test_benchmark_three_modes_ordering() -> None:
    bench = AgentBenchmark()
    report = bench.compare(seed=1)
    none_r = report["modes"]["no_guardian"]["success_rate"]
    guard_r = report["modes"]["with_guardian"]["success_rate"]
    dpo_r = report["modes"]["after_dpo"]["success_rate"]
    assert guard_r >= none_r
    assert dpo_r >= guard_r
    assert report["tasks"] == 10
    assert report["delta"]["guardian_vs_none"] >= 0


def test_write_train_recipe(tmp_path: Path) -> None:
    ds = tmp_path / "dpo.jsonl"
    ds.write_text("{}\n", encoding="utf-8")
    path = write_train_recipe(ds, tmp_path / "out", backend="unsloth")
    assert path.is_file()
    assert "unsloth" in path.name
