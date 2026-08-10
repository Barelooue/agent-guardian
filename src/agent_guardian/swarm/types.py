"""Phase 7 swarm control-plane shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class AgentStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    INTERVENCED = "intervenced"  # human gate active
    OFFLINE = "offline"
    TAKEOVER = "takeover"  # force-paused by operator


class ConnectionKind(StrEnum):
    WEBSOCKET = "websocket"
    HTTP_LONGPOLL = "http"
    INPROC = "inproc"


class ShadowEventType(StrEnum):
    THOUGHT = "thought"
    ACTION = "action"
    SCREENSHOT = "screenshot"
    STATUS = "status"
    LOG = "log"


class TakeoverKind(StrEnum):
    FORCE_PAUSE = "force_pause"
    INJECT_INSTRUCTION = "inject_instruction"
    RESUME = "resume"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AgentMeta:
    agent_id: str
    agent_type: str = "generic"
    tenant_id: str = "default"
    status: AgentStatus = AgentStatus.IDLE
    labels: dict[str, str] = field(default_factory=dict)
    last_seen_at: datetime = field(default_factory=utc_now)
    connection_kind: ConnectionKind | None = None
    session_id: str | None = None

    def touch(self) -> None:
        self.last_seen_at = utc_now()


@dataclass(frozen=True)
class ChannelRoute:
    """Resolved notification targets for an intervention."""

    channels: tuple[str, ...]
    web_room: str | None = None
    telegram_chat_id: str | None = None
    reason: str = "default"


@dataclass(frozen=True)
class RouteRule:
    """
    RBAC-ish routing rule.

    Matching is AND across non-None fields. Higher ``priority`` wins.
    ``roles_allowed`` gates whether an operator role may view/intervene.
    """

    name: str
    priority: int = 0
    tenant_id: str | None = None
    agent_type: str | None = None
    agent_id: str | None = None
    label_equals: dict[str, str] = field(default_factory=dict)
    channels: tuple[str, ...] = ("terminal",)
    web_room: str | None = None
    telegram_chat_id: str | None = None
    roles_allowed: frozenset[str] = frozenset({"admin", "operator"})


@dataclass
class ShadowEvent:
    agent_id: str
    tenant_id: str
    type: ShadowEventType
    payload: dict[str, Any]
    ts: datetime = field(default_factory=utc_now)
    sequence: int = 0


@dataclass(frozen=True)
class TakeoverSignal:
    agent_id: str
    kind: TakeoverKind
    instruction: str | None = None
    operator_id: str | None = None
    priority: int = 100
    ts: datetime = field(default_factory=utc_now)
    signal_id: str = ""
