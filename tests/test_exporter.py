"""DPO JSONL export tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_guardian.daemon.db import init_db
from agent_guardian.daemon.store import InterventionStore
from agent_guardian.exporter import export_dpo_jsonl, record_to_dpo
from agent_guardian.schemas import (
    DecisionSource,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    Option,
    OptionStyle,
    utc_now,
)


@pytest.fixture
async def seeded_db(tmp_path: Path):
    db = tmp_path / "export.db"
    media = tmp_path / "agent_guardian_media"
    media.mkdir()
    conn = await init_db(str(db))
    store = InterventionStore(conn)

    req = InterventionRequest(
        reason="即将点击支付按钮",
        title="Browser-Use · click_pay",
        options=[
            Option(id="approve", label="继续", style=OptionStyle.PRIMARY),
            Option(id="deny", label="拒绝", style=OptionStyle.DANGER, destructive=True),
            Option(id="retry_later", label="稍后"),
        ],
        context={"url": "https://shop.example/checkout", "action": "click_pay"},
        agent_id="browser-use",
    )
    created = await store.create(req)
    await store.mark_awaiting(
        created.intervention_id,
        channel=__import__("agent_guardian.schemas", fromlist=["ChannelName"]).ChannelName.TERMINAL,
    )
    await store.cas_update(
        created.intervention_id,
        target=InterventionStatus.RESOLVED,
        decision=InterventionDecision(
            intervention_id=created.intervention_id,
            option_id="approve",
            source=DecisionSource.WEB_UI,
            decided_at=utc_now(),
            operator_id="tester",
        ),
    )
    # Drop a JPEG next to the intervention id
    img = media / f"{created.intervention_id}.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"fakejpeg")

    # Non-resolved should be skipped
    await store.create(
        InterventionRequest(
            reason="pending only",
            options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
        )
    )

    await conn.close()
    return db, media, created.intervention_id


@pytest.mark.asyncio
async def test_export_dpo_jsonl(seeded_db, tmp_path: Path) -> None:
    db, media, iid = seeded_db
    out = tmp_path / "dataset.jsonl"
    count = await export_dpo_jsonl(db, out, media_root=media)
    assert count == 1
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["id"] == iid
    assert row["chosen"] == "approve"
    assert set(row["rejected"]) == {"deny", "retry_later"}
    assert "即将点击支付" in row["prompt"]
    assert row["image_path"] and iid in row["image_path"]
    assert "image_base64" not in row


@pytest.mark.asyncio
async def test_export_embed_images(seeded_db, tmp_path: Path) -> None:
    db, media, _iid = seeded_db
    out = tmp_path / "with_b64.jsonl"
    await export_dpo_jsonl(db, out, media_root=media, embed_images=True)
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["image_base64"]


@pytest.mark.asyncio
async def test_record_to_dpo_skips_non_resolved(tmp_path: Path) -> None:
    conn = await init_db(str(tmp_path / "x.db"))
    store = InterventionStore(conn)
    created = await store.create(
        InterventionRequest(
            reason="x",
            options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
        )
    )
    rec = await store.get(created.intervention_id)
    assert rec is not None
    assert record_to_dpo(rec, media_root=tmp_path) is None
    await conn.close()
