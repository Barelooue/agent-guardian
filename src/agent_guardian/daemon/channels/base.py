"""Channel interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from agent_guardian.schemas import ChannelName, InterventionRequest


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    RETRYABLE_FAILURE = "retryable_failure"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


@dataclass
class DeliveryResult:
    status: DeliveryStatus
    channel: ChannelName
    detail: str | None = None
    channel_message_id: str | None = None


class Channel(ABC):
    name: ChannelName

    @abstractmethod
    async def send_card(
        self,
        *,
        intervention_id: str,
        request: InterventionRequest,
        callback_token: str | None = None,
    ) -> DeliveryResult:
        raise NotImplementedError

    async def revoke_card(self, *, intervention_id: str, channel_message_id: str | None) -> None:
        """Best-effort remove remote controls after cancel/timeout."""
        return
