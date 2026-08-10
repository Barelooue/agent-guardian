"""Daemon configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DaemonConfig:
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    # e.g. http://127.0.0.1:7890 — required in many CN networks
    telegram_proxy: str | None = None
    bark_url: str | None = None
    webhook_url: str | None = None
    # Used in Telegram captions for snapshot preview links
    public_base_url: str = "http://127.0.0.1:8787"
    default_channels: tuple[str, ...] = ("terminal",)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def bark_enabled(self) -> bool:
        return bool(self.bark_url)

    @property
    def webhook_enabled(self) -> bool:
        return bool(self.webhook_url)


def load_config() -> DaemonConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("AG_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("AG_TELEGRAM_CHAT_ID")
    api_base = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    proxy = (
        os.getenv("TELEGRAM_PROXY")
        or os.getenv("AG_TELEGRAM_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("http_proxy")
    )
    bark = os.getenv("BARK_URL") or os.getenv("AG_BARK_URL")
    webhook = os.getenv("WEBHOOK_URL") or os.getenv("AG_WEBHOOK_URL")
    public_base = os.getenv("AG_PUBLIC_BASE_URL", "http://127.0.0.1:8787").rstrip("/")

    channels: list[str] = []
    if token and chat_id:
        channels.append("telegram")
    if bark:
        channels.append("bark")
    if webhook:
        channels.append("webhook")
    channels.append("terminal")

    return DaemonConfig(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        telegram_api_base=api_base,
        telegram_proxy=proxy,
        bark_url=bark,
        webhook_url=webhook,
        public_base_url=public_base,
        default_channels=tuple(channels),
    )
