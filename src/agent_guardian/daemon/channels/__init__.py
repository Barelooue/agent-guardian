"""Pluggable notification channels."""

from __future__ import annotations

import logging
from pathlib import Path

from agent_guardian.daemon.channels.bark import BarkChannel, WebhookChannel
from agent_guardian.daemon.channels.base import Channel, DeliveryResult, DeliveryStatus
from agent_guardian.daemon.channels.telegram import TelegramCallbackCodec, TelegramChannel
from agent_guardian.daemon.channels.terminal import TerminalChannel
from agent_guardian.daemon.config import DaemonConfig
from agent_guardian.schemas import ChannelName

logger = logging.getLogger(__name__)


def build_channels(
    config: DaemonConfig,
    *,
    media_root: Path | None = None,
    public_base_url: str | None = None,
) -> dict[ChannelName, Channel]:
    channels: dict[ChannelName, Channel] = {
        ChannelName.TERMINAL: TerminalChannel(),
    }
    if config.telegram_enabled:
        assert config.telegram_bot_token and config.telegram_chat_id
        if not config.telegram_proxy:
            logger.warning(
                "Telegram enabled without TELEGRAM_PROXY — "
                "direct api.telegram.org often fails in CN; "
                "set TELEGRAM_PROXY=http://127.0.0.1:<port>"
            )
        channels[ChannelName.TELEGRAM] = TelegramChannel(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            api_base=config.telegram_api_base,
            proxy=config.telegram_proxy,
            media_root=media_root,
            public_base_url=public_base_url,
        )
    if config.bark_enabled and config.bark_url:
        channels[ChannelName.BARK] = BarkChannel(config.bark_url)
    if config.webhook_enabled and config.webhook_url:
        channels[ChannelName.WEBHOOK] = WebhookChannel(config.webhook_url)
    return channels


__all__ = [
    "BarkChannel",
    "Channel",
    "DeliveryResult",
    "DeliveryStatus",
    "TelegramCallbackCodec",
    "TelegramChannel",
    "TerminalChannel",
    "WebhookChannel",
    "build_channels",
]
