"""Message envelope helpers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_guardian.schemas.common import PROTOCOL_VERSION, utc_now


class MessageType(StrEnum):
    INTERVENTION_CREATE = "intervention.create"
    INTERVENTION_CREATED = "intervention.created"
    INTERVENTION_DECISION = "intervention.decision"
    INTERVENTION_CANCEL = "intervention.cancel"
    INTERVENTION_UPDATED = "intervention.updated"
    ERROR = "error"


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: str = PROTOCOL_VERSION
    message_type: MessageType
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any]

    @field_validator("protocol_version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not value.startswith("1.0."):
            raise ValueError(f"unsupported protocol_version: {value}")
        return value


def make_envelope(message_type: MessageType, payload: BaseModel | dict[str, Any]) -> Envelope:
    if isinstance(payload, BaseModel):
        body = payload.model_dump(mode="json", exclude_none=False)
    else:
        body = payload
    return Envelope(message_type=message_type, payload=body)
