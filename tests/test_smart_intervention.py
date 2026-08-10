"""Phase 5: loop / risk / uncertainty smart intervention unit tests."""

from __future__ import annotations

import pytest

from agent_guardian.smart import (
    ActionHistory,
    LoopDetector,
    RiskEvaluator,
    RiskLevel,
    SmartInterventionEngine,
    SmartReasonCode,
    UncertaintyEvaluator,
    confidence_from_logits,
    softmax,
)

# ---------------------------------------------------------------------------
# Loop detector
# ---------------------------------------------------------------------------


def test_action_history_window_truncates() -> None:
    hist = ActionHistory(window_size=3)
    for i in range(5):
        hist.record(f"click-{i}")
    assert len(hist) == 3
    names = [e.name for e in hist.recent()]
    assert names == ["click-2", "click-3", "click-4"]


def test_loop_detector_repeated_actions() -> None:
    det = LoopDetector(repeat_threshold=3)
    assert det.observe("click", target="#pay").intervene is False
    assert det.observe("click", target="#pay").intervene is False
    sig = det.observe("click", target="#pay")
    assert sig.intervene is True
    assert sig.code == SmartReasonCode.LOOP_REPEATED_ACTION
    assert "死循环" in sig.message
    assert sig.human_title == "检测到 Agent 进入死循环"


def test_loop_detector_different_targets_not_loop() -> None:
    det = LoopDetector(repeat_threshold=3)
    det.observe("click", target="#a")
    det.observe("click", target="#b")
    sig = det.observe("click", target="#c")
    assert sig.intervene is False


def test_loop_detector_repeated_errors() -> None:
    det = LoopDetector(repeat_threshold=3)
    err = "TimeoutError: selector #login"
    det.observe("fill", error=err)
    det.observe("fill", error=err)
    sig = det.observe("fill", error=err)
    assert sig.intervene is True
    assert sig.code == SmartReasonCode.LOOP_REPEATED_ERROR
    assert "相同异常" in sig.message


def test_loop_detector_rejects_bad_threshold() -> None:
    with pytest.raises(ValueError):
        LoopDetector(repeat_threshold=1)


# ---------------------------------------------------------------------------
# Risk evaluator
# ---------------------------------------------------------------------------


def test_risk_rm_rf_critical() -> None:
    ev = RiskEvaluator()
    assessment = ev.evaluate(command="rm -rf /var/data")
    assert assessment.level == RiskLevel.CRITICAL
    assert assessment.score >= 0.9
    sig = ev.should_intervene(assessment)
    assert sig.intervene is True
    assert sig.code == SmartReasonCode.RISK_HIGH
    assert "高危" in sig.message


def test_risk_payment_dom() -> None:
    ev = RiskEvaluator()
    sig = ev.should_intervene(dom_action="click pay button", selector="#checkout-pay")
    assert sig.intervene is True
    assert "dom_pay" in sig.details["matched_rules"]


def test_risk_benign_nav_low() -> None:
    ev = RiskEvaluator()
    assessment = ev.evaluate(dom_action="click", selector="#next-page", url="https://docs.example")
    assert assessment.score < 0.7
    assert ev.should_intervene(assessment).intervene is False


def test_risk_sql_drop() -> None:
    ev = RiskEvaluator()
    a = ev.evaluate(command="DROP TABLE users;")
    assert a.level >= RiskLevel.HIGH
    assert a.is_high_risk


# ---------------------------------------------------------------------------
# Uncertainty evaluator
# ---------------------------------------------------------------------------


def test_softmax_and_confidence_from_logits() -> None:
    probs = softmax([1.0, 2.0, 3.0])
    assert pytest.approx(sum(probs), abs=1e-6) == 1.0
    assert confidence_from_logits([1.0, 2.0, 3.0]) == pytest.approx(max(probs))


def test_uncertainty_should_intervene_low_confidence() -> None:
    u = UncertaintyEvaluator(base_threshold=0.55, adaptive=False)
    assert u.should_intervene(confidence=0.40) is True
    assert u.should_intervene(confidence=0.80) is False
    sig = u.evaluate(confidence=0.2)
    assert sig.intervene is True
    assert sig.code == SmartReasonCode.UNCERTAINTY_LOW_CONFIDENCE


def test_uncertainty_from_logits() -> None:
    u = UncertaintyEvaluator(base_threshold=0.5, adaptive=False)
    # Peak probability ~0.57 for [0, 0.3, 0.5] roughly — use flat logits → ~0.33
    assert u.should_intervene(logits=[0.0, 0.0, 0.0]) is True
    assert u.should_intervene(logits=[10.0, 0.0, 0.0]) is False


def test_uncertainty_adaptive_threshold_moves() -> None:
    u = UncertaintyEvaluator(base_threshold=0.55, adaptive=True)
    before = u.threshold
    u.update_from_outcome(intervened=True, human_confirmed_needed=False, step=0.05)
    assert u.threshold == pytest.approx(before - 0.05)
    after_false_alarm = u.threshold
    u.update_from_outcome(intervened=False, human_confirmed_needed=True, step=0.05)
    assert u.threshold == pytest.approx(after_false_alarm + 0.05)


# ---------------------------------------------------------------------------
# Engine composition
# ---------------------------------------------------------------------------


def test_engine_triggers_on_loop() -> None:
    engine = SmartInterventionEngine(loop=LoopDetector(repeat_threshold=3))
    engine.evaluate_step(action_name="retry_login", target="form")
    engine.evaluate_step(action_name="retry_login", target="form")
    sig = engine.evaluate_step(action_name="retry_login", target="form")
    assert sig.intervene is True
    assert sig.code == SmartReasonCode.LOOP_REPEATED_ACTION


def test_engine_triggers_on_risk_before_uncertainty() -> None:
    engine = SmartInterventionEngine()
    sig = engine.evaluate_step(
        action_name="run",
        command="git push --force origin main",
        confidence=0.99,  # high confidence should not block risk
    )
    assert sig.intervene is True
    assert sig.code == SmartReasonCode.RISK_HIGH


def test_engine_triggers_on_uncertainty() -> None:
    engine = SmartInterventionEngine(
        uncertainty=UncertaintyEvaluator(base_threshold=0.6, adaptive=False)
    )
    sig = engine.evaluate_step(action_name="choose_option", confidence=0.2)
    assert sig.intervene is True
    assert sig.code == SmartReasonCode.UNCERTAINTY_LOW_CONFIDENCE
