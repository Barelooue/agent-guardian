"""Rule + light semantic risk scoring for commands / DOM actions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from agent_guardian.smart.types import SmartReasonCode, SmartSignal


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(frozen=True)
class RiskRule:
    name: str
    pattern: re.Pattern[str]
    score: float
    level: RiskLevel
    category: str = "general"


@dataclass
class RiskAssessment:
    score: float
    level: RiskLevel
    matched_rules: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def is_high_risk(self) -> bool:
        return self.level >= RiskLevel.HIGH or self.score >= 0.7


_DEFAULT_RULES: tuple[RiskRule, ...] = (
    RiskRule(
        "shell_rm_rf",
        re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b", re.I),
        0.95,
        RiskLevel.CRITICAL,
        "shell",
    ),
    RiskRule(
        "shell_dd_disk",
        re.compile(r"\bdd\b.*\bof=/dev/", re.I),
        0.98,
        RiskLevel.CRITICAL,
        "shell",
    ),
    RiskRule(
        "shell_mkfs",
        re.compile(r"\bmkfs(\.|$|\s)|format\s+[a-z]:", re.I),
        0.9,
        RiskLevel.CRITICAL,
        "shell",
    ),
    RiskRule(
        "sql_drop",
        re.compile(r"\bdrop\s+(table|database|schema)\b", re.I),
        0.92,
        RiskLevel.CRITICAL,
        "sql",
    ),
    RiskRule(
        "sql_truncate",
        re.compile(r"\btruncate\s+table\b", re.I),
        0.85,
        RiskLevel.HIGH,
        "sql",
    ),
    RiskRule(
        "sql_delete_all",
        re.compile(r"\bdelete\s+from\b(?![^\n]*\bwhere\b)", re.I),
        0.8,
        RiskLevel.HIGH,
        "sql",
    ),
    RiskRule(
        "dom_pay",
        re.compile(r"\b(pay|payment|checkout|purchase|confirm.?pay|支付|付款|结算)\b", re.I),
        0.78,
        RiskLevel.HIGH,
        "dom",
    ),
    RiskRule(
        "dom_submit_sensitive",
        re.compile(
            r"\b(submit|transfer|wire|delete.?account|注销|转账|提交订单)\b",
            re.I,
        ),
        0.72,
        RiskLevel.HIGH,
        "dom",
    ),
    RiskRule(
        "dom_auth_change",
        re.compile(r"\b(password|2fa|otp|api.?key|secret|token)\b", re.I),
        0.65,
        RiskLevel.MEDIUM,
        "dom",
    ),
    RiskRule(
        "git_force_push",
        re.compile(r"\bgit\s+push\b.*(--force|-f)\b", re.I),
        0.75,
        RiskLevel.HIGH,
        "vcs",
    ),
    RiskRule(
        "chmod_777",
        re.compile(r"\bchmod\s+(-R\s+)?777\b", re.I),
        0.7,
        RiskLevel.HIGH,
        "shell",
    ),
)


_SEMANTIC_KEYWORDS: dict[str, float] = {
    "production": 0.15,
    "prod": 0.15,
    "master": 0.08,
    "main": 0.05,
    "irreversible": 0.2,
    "destructive": 0.2,
    "payment": 0.25,
    "captcha": 0.05,
}


def _level_for_score(score: float) -> RiskLevel:
    if score >= 0.9:
        return RiskLevel.CRITICAL
    if score >= 0.7:
        return RiskLevel.HIGH
    if score >= 0.4:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


class RiskEvaluator:
    """
    Score command / DOM / URL text with built-in rules + light semantic boosts.

    When score >= ``intervene_threshold`` (default 0.7), callers should suspend
    the agent and warn the human of a high-risk operation.
    """

    def __init__(
        self,
        *,
        rules: Sequence[RiskRule] | None = None,
        intervene_threshold: float = 0.7,
        semantic_boost: bool = True,
    ) -> None:
        self.rules = list(rules) if rules is not None else list(_DEFAULT_RULES)
        self.intervene_threshold = intervene_threshold
        self.semantic_boost = semantic_boost

    def evaluate(
        self,
        *,
        command: str | None = None,
        dom_action: str | None = None,
        selector: str | None = None,
        url: str | None = None,
        extra_text: str | None = None,
    ) -> RiskAssessment:
        blob_parts = [command, dom_action, selector, url, extra_text]
        blob = " \n ".join(p for p in blob_parts if p)
        matched: list[str] = []
        reasons: list[str] = []
        best = 0.0
        best_level = RiskLevel.LOW

        if blob.strip():
            for rule in self.rules:
                if rule.pattern.search(blob):
                    matched.append(rule.name)
                    reasons.append(f"{rule.category}:{rule.name} (+{rule.score:.2f})")
                    if rule.score > best:
                        best = rule.score
                        best_level = rule.level

            if self.semantic_boost:
                lower = blob.lower()
                boost = 0.0
                for kw, w in _SEMANTIC_KEYWORDS.items():
                    if kw in lower:
                        boost += w
                        reasons.append(f"semantic:{kw} (+{w:.2f})")
                best = min(1.0, best + boost)

        level = max(best_level, _level_for_score(best))
        return RiskAssessment(
            score=round(best, 4),
            level=level,
            matched_rules=matched,
            reasons=reasons,
            features={
                "command": command,
                "dom_action": dom_action,
                "selector": selector,
                "url": url,
            },
        )

    def should_intervene(self, assessment: RiskAssessment | None = None, **kwargs: Any) -> SmartSignal:
        result = assessment if assessment is not None else self.evaluate(**kwargs)
        if result.score >= self.intervene_threshold or result.level >= RiskLevel.HIGH:
            return SmartSignal(
                intervene=True,
                code=SmartReasonCode.RISK_HIGH,
                message=(
                    f"高危操作预警：风险分 {result.score:.2f}（{result.level.name}）。"
                    + (" 命中: " + ", ".join(result.matched_rules) if result.matched_rules else "")
                ),
                score=result.score,
                details={
                    "level": result.level.name,
                    "matched_rules": result.matched_rules,
                    "reasons": result.reasons,
                    "features": result.features,
                },
            )
        return SmartSignal(
            intervene=False,
            code=SmartReasonCode.OK,
            message="risk within threshold",
            score=result.score,
            details={"level": result.level.name, "matched_rules": result.matched_rules},
        )
