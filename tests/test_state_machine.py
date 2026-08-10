from agent_guardian.daemon.state_machine import can_transition
from agent_guardian.schemas import InterventionStatus


def test_legal_happy_path() -> None:
    assert can_transition(InterventionStatus.PENDING, InterventionStatus.NOTIFIED)
    assert can_transition(InterventionStatus.NOTIFIED, InterventionStatus.AWAITING_HUMAN)
    assert can_transition(InterventionStatus.AWAITING_HUMAN, InterventionStatus.RESOLVED)


def test_terminal_blocks_further_writes() -> None:
    assert not can_transition(InterventionStatus.TIMEOUT, InterventionStatus.RESOLVED)
    assert not can_transition(InterventionStatus.RESOLVED, InterventionStatus.CANCELLED)


def test_pending_can_timeout_or_cancel() -> None:
    assert can_transition(InterventionStatus.PENDING, InterventionStatus.TIMEOUT)
    assert can_transition(InterventionStatus.PENDING, InterventionStatus.CANCELLED)
