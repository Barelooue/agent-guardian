"""SDK exception tree aligned with docs/protocol.md §8.3."""

from __future__ import annotations

from typing import Any


class AgentGuardianError(Exception):
    """Base error for Agent Guardian SDK / protocol failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        intervention_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.intervention_id = intervention_id
        self.details = details or {}


class InterventionTimeoutError(AgentGuardianError):
    """Intervention reached TIMEOUT or local wait expired."""


class InterventionDeniedError(AgentGuardianError):
    """Human selected a deny option / explicit rejection."""


class InterventionCancelledError(AgentGuardianError):
    """Intervention was cancelled."""


class InterventionFailedError(AgentGuardianError):
    """Intervention entered FAILED or unrecoverable daemon error."""


class ProtocolError(AgentGuardianError):
    """Protocol version mismatch or payload validation failure."""
