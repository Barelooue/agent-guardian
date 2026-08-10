"""Intervention request/decision/update payloads."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_guardian.schemas.common import (
    ChannelName,
    DecisionSource,
    InterventionStatus,
    Option,
    SnapshotRef,
)
from agent_guardian.schemas.errors import ErrorPayload
from agent_guardian.schemas.spatial import SpatialAnnotation

OPEN_STATUSES: frozenset[InterventionStatus] = frozenset(
    {
        InterventionStatus.PENDING,
        InterventionStatus.NOTIFIED,
        InterventionStatus.AWAITING_HUMAN,
    }
)

TERMINAL_STATUSES: frozenset[InterventionStatus] = frozenset(
    {
        InterventionStatus.RESOLVED,
        InterventionStatus.TIMEOUT,
        InterventionStatus.CANCELLED,
        InterventionStatus.FAILED,
    }
)


class CancelReason(StrEnum):
    CLIENT_ABORTED = "client_aborted"
    CONTEXT_MANAGER_EXIT = "context_manager_exit"
    AGENT_SHUTDOWN = "agent_shutdown"
    SUPERSEDED = "superseded"
    CLIENT_TIMEOUT = "client_timeout"


class InterventionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(default="Agent 需要你的确认", max_length=200)
    reason: str = Field(min_length=1, max_length=4000)
    options: list[Option] = Field(min_length=1, max_length=10)
    context: dict[str, Any] = Field(default_factory=dict)
    snapshot: SnapshotRef | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    channels: list[ChannelName] | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    agent_id: str | None = Field(default=None, max_length=128)

    @field_validator("options")
    @classmethod
    def _unique_option_ids(cls, options: list[Option]) -> list[Option]:
        ids = [o.id for o in options]
        if len(ids) != len(set(ids)):
            raise ValueError("options[].id must be unique")
        return options


class InterventionCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    client_request_id: str
    status: InterventionStatus
    expires_at: datetime
    created_at: datetime
    reused: bool = False


class InterventionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    option_id: str = Field(min_length=1, max_length=64)
    source: DecisionSource
    decided_at: datetime
    operator_id: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)
    channel_message_id: str | None = None
    # Phase 6 — optional visual steering / rollback hints from Web UI
    spatial: SpatialAnnotation | None = None
    rollback_steps: int | None = Field(default=None, ge=1, le=100)


class InterventionCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    reason: CancelReason
    detail: str | None = Field(default=None, max_length=1000)


class InterventionUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    status: InterventionStatus
    version: int = Field(ge=1)
    updated_at: datetime
    idempotent: bool
    selected_option_id: str | None = None
    decision: InterventionDecision | None = None
    error: ErrorPayload | None = None
    active_channel: ChannelName | None = None
