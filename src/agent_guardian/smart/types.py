"""Phase 5 — predictive / smart intervention signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SmartReasonCode(StrEnum):
    LOOP_REPEATED_ACTION = "loop_repeated_action"
    LOOP_REPEATED_ERROR = "loop_repeated_error"
    RISK_HIGH = "risk_high"
    UNCERTAINTY_LOW_CONFIDENCE = "uncertainty_low_confidence"
    OK = "ok"


@dataclass(frozen=True)
class SmartSignal:
    """Outcome of one smart evaluator."""

    intervene: bool
    code: SmartReasonCode
    message: str
    score: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def human_title(self) -> str:
        if self.code == SmartReasonCode.LOOP_REPEATED_ACTION:
            return "检测到 Agent 进入死循环"
        if self.code == SmartReasonCode.LOOP_REPEATED_ERROR:
            return "检测到 Agent 重复失败（疑似死锁）"
        if self.code == SmartReasonCode.RISK_HIGH:
            return "高危操作预警"
        if self.code == SmartReasonCode.UNCERTAINTY_LOW_CONFIDENCE:
            return "低置信度决策，需要人工确认"
        return "继续执行"
