"""Error payload and codes."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    AG_OK = "AG_OK"
    AG_INVALID_REQUEST = "AG_INVALID_REQUEST"
    AG_UNSUPPORTED_VERSION = "AG_UNSUPPORTED_VERSION"
    AG_NOT_FOUND = "AG_NOT_FOUND"
    AG_STATE_CONFLICT = "AG_STATE_CONFLICT"
    AG_ALREADY_TERMINAL = "AG_ALREADY_TERMINAL"
    AG_TIMEOUT = "AG_TIMEOUT"
    AG_CANCELLED = "AG_CANCELLED"
    AG_DENIED = "AG_DENIED"
    AG_CHANNEL_UNAVAILABLE = "AG_CHANNEL_UNAVAILABLE"
    AG_CHANNEL_RETRY_EXHAUSTED = "AG_CHANNEL_RETRY_EXHAUSTED"
    AG_PERSISTENCE_ERROR = "AG_PERSISTENCE_ERROR"
    AG_INTERNAL = "AG_INTERNAL"


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)
    intervention_id: str | None = None
    current_status: str | None = None
