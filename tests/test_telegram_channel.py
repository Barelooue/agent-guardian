"""Telegram channel send/backoff/callback resolve tests with mocked HTTP."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_guardian.daemon.channels.telegram import (
    TelegramCallbackCodec,
    TelegramChannel,
)
from agent_guardian.daemon.db import init_db
from agent_guardian.daemon.store import InterventionStore
from agent_guardian.schemas import InterventionRequest, Option


@pytest.mark.asyncio
async def test_send_card_uses_compact_callback_and_stores_message() -> None:
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), json.loads(request.content.decode())))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 42,
                    "chat": {"id": 123456},
                },
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, trust_env=False)
    channel = TelegramChannel(
        bot_token="TOKEN",
        chat_id="123456",
        client=client,
    )
    req = InterventionRequest(
        reason="need human",
        options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
        timeout_seconds=30,
    )
    result = await channel.send_card(
        intervention_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        request=req,
        callback_token="tok12345",
    )
    assert result.status.value == "delivered"
    assert result.channel_message_id == "42"
    assert "sendMessage" in calls[0][0]
    keyboard = calls[0][1]["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["callback_data"] == "tok12345:0"
    assert len(keyboard[0][0]["callback_data"].encode()) <= 64
    await channel.aclose()


@pytest.mark.asyncio
async def test_retryable_then_success() -> None:
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] < 3:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 7, "chat": {"id": 1}}},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, trust_env=False)
    channel = TelegramChannel(bot_token="T", chat_id="1", client=client)
    req = InterventionRequest(
        reason="r",
        options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
    )
    result = await channel.send_card(
        intervention_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        request=req,
        callback_token="abcd1234",
    )
    assert result.status.value == "delivered"
    assert n["i"] == 3
    await channel.aclose()


@pytest.mark.asyncio
async def test_callback_token_roundtrip(tmp_path) -> None:
    conn = await init_db(str(tmp_path / "cb.db"))
    store = InterventionStore(conn)
    created = await store.create(
        InterventionRequest(
            reason="cb",
            options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
        )
    )
    token = await store.allocate_callback_token(created.intervention_id, ["approve", "deny"])
    data = TelegramCallbackCodec.encode(token, 1)
    resolved = await store.resolve_callback_data(data)
    assert resolved == (created.intervention_id, "deny")
    await conn.close()


@pytest.mark.asyncio
async def test_auth_error_not_retryable_exhausts_as_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, trust_env=False)
    channel = TelegramChannel(bot_token="bad", chat_id="1", client=client)
    req = InterventionRequest(
        reason="r",
        options=[Option(id="approve", label="OK"), Option(id="deny", label="No")],
    )
    result = await channel.send_card(
        intervention_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        request=req,
        callback_token="abcd1234",
    )
    assert result.status.value == "failed"
    await channel.aclose()
