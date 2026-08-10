"""Shared enums and value objects."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "1.0.0"


def utc_now() -> datetime:
    return datetime.now(UTC)


class InterventionStatus(StrEnum):
    PENDING = "PENDING"
    NOTIFIED = "NOTIFIED"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    RESOLVED = "RESOLVED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class OptionStyle(StrEnum):
    PRIMARY = "primary"
    DANGER = "danger"
    NEUTRAL = "neutral"


class ChannelName(StrEnum):
    TELEGRAM = "telegram"
    BARK = "bark"
    WEBHOOK = "webhook"
    TERMINAL = "terminal"


class DecisionSource(StrEnum):
    TELEGRAM = "telegram"
    BARK = "bark"
    WEBHOOK = "webhook"
    TERMINAL = "terminal"
    WEB_UI = "web_ui"
    SDK = "sdk"


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    style: OptionStyle = OptionStyle.NEUTRAL
    destructive: bool = False


class SnapshotRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    url: str | None = None
    base64: str | None = None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


JsonObject = dict[str, Any]
