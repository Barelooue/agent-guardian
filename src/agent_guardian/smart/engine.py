"""Compose loop / risk / uncertainty evaluators into one step gate."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_guardian.smart.loop_detector import LoopDetector
from agent_guardian.smart.risk_evaluator import RiskEvaluator
from agent_guardian.smart.types import SmartReasonCode, SmartSignal
from agent_guardian.smart.uncertainty_evaluator import UncertaintyEvaluator


@dataclass
class SmartInterventionEngine:
    """
    Single entry for Phase 5 predictive intervention.

    Call :meth:`evaluate_step` before executing an agent action. If any signal
    has ``intervene=True``, the host should call ``AgentGuardian.ask_human``
    with ``signal.human_title`` / ``signal.message``.
    """

    loop: LoopDetector = field(default_factory=LoopDetector)
    risk: RiskEvaluator = field(default_factory=RiskEvaluator)
    uncertainty: UncertaintyEvaluator = field(default_factory=UncertaintyEvaluator)

    def evaluate_step(
        self,
        *,
        action_name: str | None = None,
        action_args: dict[str, Any] | str | None = None,
        target: str | None = None,
        error: str | BaseException | None = None,
        command: str | None = None,
        dom_action: str | None = None,
        selector: str | None = None,
        url: str | None = None,
        confidence: float | None = None,
        logits: Sequence[float] | None = None,
        record_action: bool = True,
    ) -> SmartSignal:
        signals: list[SmartSignal] = []

        if action_name is not None and record_action:
            signals.append(
                self.loop.observe(
                    action_name,
                    args=action_args,
                    target=target,
                    error=error,
                )
            )
        elif action_name is not None:
            # Evaluate history without recording (rare)
            signals.append(
                self.loop.evaluate_recent()
                or SmartSignal(False, SmartReasonCode.OK, "no loop", 0.0)
            )

        if any(x is not None for x in (command, dom_action, selector, url)):
            signals.append(
                self.risk.should_intervene(
                    command=command,
                    dom_action=dom_action or action_name,
                    selector=selector or target,
                    url=url,
                )
            )

        if confidence is not None or logits is not None:
            signals.append(
                self.uncertainty.evaluate(confidence=confidence, logits=logits)
            )

        for sig in signals:
            if sig.intervene:
                return sig
        if not signals:
            return SmartSignal(
                intervene=False,
                code=SmartReasonCode.OK,
                message="no smart checks configured for this step",
            )
        # Prefer highest score among OK signals for telemetry
        best = max(signals, key=lambda s: s.score)
        return best
