"""Legal state transitions (protocol §4.3)."""

from __future__ import annotations

from agent_guardian.schemas import OPEN_STATUSES, TERMINAL_STATUSES, InterventionStatus

_ALLOWED: dict[InterventionStatus, frozenset[InterventionStatus]] = {
    InterventionStatus.PENDING: frozenset(
        {
            InterventionStatus.NOTIFIED,
            InterventionStatus.AWAITING_HUMAN,
            InterventionStatus.TIMEOUT,
            InterventionStatus.CANCELLED,
            InterventionStatus.FAILED,
        }
    ),
    InterventionStatus.NOTIFIED: frozenset(
        {
            InterventionStatus.AWAITING_HUMAN,
            InterventionStatus.RESOLVED,
            InterventionStatus.TIMEOUT,
            InterventionStatus.CANCELLED,
            InterventionStatus.FAILED,
        }
    ),
    InterventionStatus.AWAITING_HUMAN: frozenset(
        {
            InterventionStatus.RESOLVED,
            InterventionStatus.TIMEOUT,
            InterventionStatus.CANCELLED,
            InterventionStatus.FAILED,
        }
    ),
}


def is_open(status: InterventionStatus) -> bool:
    return status in OPEN_STATUSES


def is_terminal(status: InterventionStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(current: InterventionStatus, target: InterventionStatus) -> bool:
    if current == target:
        return False
    if is_terminal(current):
        return False
    return target in _ALLOWED.get(current, frozenset())
