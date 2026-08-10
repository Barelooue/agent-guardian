"""Sliding-window action history + loop / deadlock detection."""

from __future__ import annotations

import hashlib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from agent_guardian.smart.types import SmartReasonCode, SmartSignal

_WS = re.compile(r"\s+")


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub(" ", value.strip().lower())


def fingerprint_action(
    name: str,
    *,
    args: dict[str, Any] | str | None = None,
    target: str | None = None,
) -> str:
    """Stable fingerprint for similarity checks (name + target + sorted args)."""
    parts = [_normalize_text(name), _normalize_text(target)]
    if isinstance(args, str):
        parts.append(_normalize_text(args))
    elif isinstance(args, dict):
        items = sorted((_normalize_text(str(k)), _normalize_text(str(v))) for k, v in args.items())
        parts.append("|".join(f"{k}={v}" for k, v in items))
    raw = "\0".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class ActionEvent:
    name: str
    fingerprint: str
    error: str | None = None
    target: str | None = None
    ts: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)


class ActionHistory:
    """Ring buffer of recent agent actions / errors."""

    def __init__(self, window_size: int = 32) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        self.window_size = window_size
        self._events: deque[ActionEvent] = deque(maxlen=window_size)

    def __len__(self) -> int:
        return len(self._events)

    def clear(self) -> None:
        self._events.clear()

    def record(
        self,
        name: str,
        *,
        args: dict[str, Any] | str | None = None,
        target: str | None = None,
        error: str | BaseException | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ActionEvent:
        err = None
        if error is not None:
            err = _normalize_text(str(error))
        event = ActionEvent(
            name=_normalize_text(name) or "unknown",
            fingerprint=fingerprint_action(name, args=args, target=target),
            error=err,
            target=_normalize_text(target) or None,
            meta=dict(meta or {}),
        )
        self._events.append(event)
        return event

    def recent(self, n: int | None = None) -> list[ActionEvent]:
        if n is None or n >= len(self._events):
            return list(self._events)
        if n <= 0:
            return []
        return list(self._events)[-n:]


class LoopDetector:
    """
    Detect repeated similar actions or identical exceptions in a sliding window.

    When the last ``repeat_threshold`` events share the same action fingerprint
    (or the same error string), emit an intervention signal.
    """

    def __init__(
        self,
        *,
        repeat_threshold: int = 3,
        history: ActionHistory | None = None,
        window_size: int = 32,
    ) -> None:
        if repeat_threshold < 2:
            raise ValueError("repeat_threshold must be >= 2")
        self.repeat_threshold = repeat_threshold
        self.history = history or ActionHistory(window_size=window_size)

    def observe(
        self,
        name: str,
        *,
        args: dict[str, Any] | str | None = None,
        target: str | None = None,
        error: str | BaseException | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SmartSignal:
        event = self.history.record(
            name, args=args, target=target, error=error, meta=meta
        )
        return self.evaluate_recent() or SmartSignal(
            intervene=False,
            code=SmartReasonCode.OK,
            message="no loop detected",
            score=0.0,
            details={"fingerprint": event.fingerprint},
        )

    def evaluate_recent(self) -> SmartSignal | None:
        need = self.repeat_threshold
        recent = self.history.recent(need)
        if len(recent) < need:
            return None

        # Identical errors → deadlock / retry spin
        errors = [e.error for e in recent]
        if all(errors) and len(set(errors)) == 1:
            return SmartSignal(
                intervene=True,
                code=SmartReasonCode.LOOP_REPEATED_ERROR,
                message=(
                    f"检测到 Agent 进入死循环：连续 {need} 次捕获相同异常 "
                    f"「{errors[0]}」"
                ),
                score=1.0,
                details={"error": errors[0], "count": need},
            )

        # Similar / repeated actions (same fingerprint)
        fps = [e.fingerprint for e in recent]
        if len(set(fps)) == 1:
            sample = recent[-1]
            return SmartSignal(
                intervene=True,
                code=SmartReasonCode.LOOP_REPEATED_ACTION,
                message=(
                    f"检测到 Agent 进入死循环：连续 {need} 次执行相似动作 "
                    f"「{sample.name}」"
                ),
                score=1.0,
                details={
                    "action": sample.name,
                    "fingerprint": sample.fingerprint,
                    "target": sample.target,
                    "count": need,
                },
            )
        return None
