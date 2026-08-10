"""Phase 7 — multi-agent swarm control plane."""

from agent_guardian.swarm.routing import ChannelRouter
from agent_guardian.swarm.shadow_observer import ShadowObserver
from agent_guardian.swarm.swarm_hub import AgentHubManager
from agent_guardian.swarm.takeover import TakeoverBroker
from agent_guardian.swarm.types import (
    AgentMeta,
    AgentStatus,
    ChannelRoute,
    ConnectionKind,
    RouteRule,
    ShadowEvent,
    ShadowEventType,
    TakeoverKind,
    TakeoverSignal,
)

__all__ = [
    "AgentHubManager",
    "AgentMeta",
    "AgentStatus",
    "ChannelRoute",
    "ChannelRouter",
    "ConnectionKind",
    "RouteRule",
    "ShadowEvent",
    "ShadowEventType",
    "ShadowObserver",
    "TakeoverBroker",
    "TakeoverKind",
    "TakeoverSignal",
]
