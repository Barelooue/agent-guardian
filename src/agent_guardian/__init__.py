"""Agent Guardian — Human-in-the-Loop middleware for agents."""

from agent_guardian.client.guardian import AgentGuardian, GuardStepResult, ask_human
from agent_guardian.exceptions import (
    AgentGuardianError,
    InterventionCancelledError,
    InterventionDeniedError,
    InterventionFailedError,
    InterventionTimeoutError,
    ProtocolError,
)
from agent_guardian.schemas import InterventionDecision, InterventionUpdated, Option

__all__ = [
    "AgentGuardian",
    "AgentGuardianError",
    "GuardStepResult",
    "InterventionCancelledError",
    "InterventionDecision",
    "InterventionDeniedError",
    "InterventionFailedError",
    "InterventionTimeoutError",
    "InterventionUpdated",
    "Option",
    "ProtocolError",
    "ask_human",
]

__version__ = "0.2.0"
PROTOCOL_VERSION = "1.0.0"
