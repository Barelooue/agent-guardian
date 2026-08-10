"""Open-intervention summary for Web UI listing."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from agent_guardian.schemas.common import ChannelName, InterventionStatus, Option, SnapshotRef


class InterventionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    client_request_id: str
    status: InterventionStatus
    title: str
    reason: str
    options: list[Option]
    snapshot: SnapshotRef | None = None
    active_channel: ChannelName | None = None
    expires_at: datetime
    created_at: datetime
    version: int
