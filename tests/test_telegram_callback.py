from agent_guardian.daemon.channels.telegram import TelegramCallbackCodec


def test_callback_data_within_64_bytes() -> None:
    token = "a1B2c3d4"
    data = TelegramCallbackCodec.encode(token, 0)
    assert data == "a1B2c3d4:0"
    assert len(data.encode("utf-8")) <= 64


def test_inline_keyboard_compact() -> None:
    kb = TelegramCallbackCodec.build_inline_keyboard(
        "tok12345",
        ["approve", "deny"],
        ["确认支付", "拒绝并回滚"],
    )
    assert kb[0][0]["callback_data"] == "tok12345:0"
    assert kb[0][1]["callback_data"] == "tok12345:1"
    for row in kb:
        for btn in row:
            assert len(btn["callback_data"].encode("utf-8")) <= 64
