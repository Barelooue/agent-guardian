"""Confidence / logits based uncertainty gate."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from agent_guardian.smart.types import SmartReasonCode, SmartSignal


def softmax(logits: Sequence[float]) -> list[float]:
    if not logits:
        return []
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    s = sum(exps) or 1.0
    return [e / s for e in exps]


def confidence_from_logits(logits: Sequence[float]) -> float:
    """Max-softmax probability as a simple confidence proxy."""
    probs = softmax(logits)
    return max(probs) if probs else 0.0


@dataclass
class UncertaintyEvaluator:
    """
    Intervene when model confidence falls below an (optionally adaptive) threshold.

    ``should_intervene(confidence=...)`` or ``should_intervene(logits=...)``.
    Adaptive mode nudges the threshold after outcomes:
    - human overturned a low-confidence continue → raise threshold (intervene more)
    - human agreed that intervention was unnecessary → lower threshold slightly
    """

    base_threshold: float = 0.55
    min_threshold: float = 0.35
    max_threshold: float = 0.85
    adaptive: bool = True
    _threshold: float = field(init=False)
    _observations: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not 0.0 < self.base_threshold < 1.0:
            raise ValueError("base_threshold must be in (0, 1)")
        self._threshold = self.base_threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    def resolve_confidence(
        self,
        *,
        confidence: float | None = None,
        logits: Sequence[float] | None = None,
    ) -> float:
        if confidence is not None:
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("confidence must be in [0, 1]")
            return float(confidence)
        if logits is not None:
            return confidence_from_logits(logits)
        raise ValueError("provide confidence= or logits=")

    def should_intervene(
        self,
        confidence: float | None = None,
        logits: Sequence[float] | None = None,
    ) -> bool:
        """Return True when confidence is below the current threshold."""
        conf = self.resolve_confidence(confidence=confidence, logits=logits)
        return conf < self._threshold

    def evaluate(
        self,
        *,
        confidence: float | None = None,
        logits: Sequence[float] | None = None,
    ) -> SmartSignal:
        conf = self.resolve_confidence(confidence=confidence, logits=logits)
        intervene = conf < self._threshold
        if intervene:
            return SmartSignal(
                intervene=True,
                code=SmartReasonCode.UNCERTAINTY_LOW_CONFIDENCE,
                message=(
                    f"低置信度决策：confidence={conf:.3f} < threshold={self._threshold:.3f}，"
                    "需要人工确认"
                ),
                score=1.0 - conf,
                details={
                    "confidence": conf,
                    "threshold": self._threshold,
                    "adaptive": self.adaptive,
                },
            )
        return SmartSignal(
            intervene=False,
            code=SmartReasonCode.OK,
            message="confidence acceptable",
            score=1.0 - conf,
            details={"confidence": conf, "threshold": self._threshold},
        )

    def update_from_outcome(
        self,
        *,
        intervened: bool,
        human_confirmed_needed: bool,
        step: float = 0.02,
    ) -> float:
        """
        Adjust adaptive threshold from human feedback.

        ``human_confirmed_needed=True`` means the human agreed the pause was useful
        (or rejected the agent's intended action).
        """
        if not self.adaptive:
            return self._threshold
        self._observations += 1
        if intervened and human_confirmed_needed:
            # Good catch — keep threshold or nudge up slightly
            self._threshold = min(self.max_threshold, self._threshold + step * 0.5)
        elif intervened and not human_confirmed_needed:
            # False alarm — intervene less often
            self._threshold = max(self.min_threshold, self._threshold - step)
        elif not intervened and human_confirmed_needed:
            # Missed — should have asked; raise threshold
            self._threshold = min(self.max_threshold, self._threshold + step)
        return self._threshold
