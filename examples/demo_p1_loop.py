"""
Phase 1 冒烟演示：断点触发 -> 人类干预 -> 继续/回滚（全彩日志）

用法（需先启动 Daemon）:
  终端 A:  python -m agent_guardian serve --host 127.0.0.1 --port 8787
  终端 B:  python examples/demo_p1_loop.py
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from uuid import uuid4

from agent_guardian import (
    AgentGuardian,
    InterventionDeniedError,
    InterventionTimeoutError,
)
from agent_guardian.schemas import (
    ChannelName,
    DecisionSource,
    InterventionDecision,
    InterventionRequest,
    Option,
    utc_now,
)

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"


def log(step: str, message: str, *, color: str = CYAN) -> None:
    print(f"{color}{BOLD}[{step}]{RESET} {message}", flush=True)


def banner(title: str) -> None:
    line = "─" * 56
    print(f"\n{MAGENTA}{BOLD}{line}{RESET}")
    print(f"{MAGENTA}{BOLD}  {title}{RESET}")
    print(f"{MAGENTA}{BOLD}{line}{RESET}\n", flush=True)


async def _prompt_123(options: Sequence[Option]) -> str:
    print(f"{YELLOW}{BOLD}请选择（输入 1/2/3 后回车）:{RESET}")
    for i, opt in enumerate(options, start=1):
        print(f"  {BOLD}{i}{RESET}) {opt.id} — {opt.label}")
    while True:
        raw = await asyncio.to_thread(sys.stdin.readline)
        raw = (raw or "").strip()
        if raw in {"1", "2", "3"}:
            return options[int(raw) - 1].id
        print(f"{RED}无效输入 {raw!r}，请输入 1、2 或 3{RESET}", flush=True)


async def _run_loop(base_url: str) -> None:
    options = [
        Option(id="approve", label="确认支付并继续"),
        Option(id="deny", label="拒绝并安全回滚"),
        Option(id="retry_later", label="稍后重试"),
    ]
    client_request_id = str(uuid4())

    banner("Agent Guardian · Phase 1 人机协同闭环演示")
    log("AGENT", "开始执行自动化任务：提交订单 / 准备支付…", color=BLUE)
    await asyncio.sleep(0.25)
    log("AGENT", "检测到敏感操作：支付确认页（模拟，非真实扣款）", color=YELLOW)
    log("BREAKPOINT", "置信度不足 → 触发 ask_human 断点", color=RED)

    async with AgentGuardian(
        base_url,
        local_terminal_prompt=False,
        poll_interval=0.25,
    ) as guardian:
        # 1) 先创建干预，拿到稳定的 intervention_id（避免与 ask_human 竞态）
        created = await guardian._http.create(
            InterventionRequest(
                client_request_id=client_request_id,
                reason="检测到支付确认页，模型置信度不足，请人工确认是否继续。",
                title="支付确认（Demo）",
                options=options,
                timeout_seconds=120,
                channels=[ChannelName.TERMINAL],
            )
        )
        iid = created.intervention_id
        log(
            "DAEMON",
            f"未决断点已持久化 intervention_id={iid} reused={created.reused}",
            color=CYAN,
        )

        # 2) 再挂起 Agent 等待（同一 client_request_id → reused）
        wait_task = asyncio.create_task(
            guardian.ask_human(
                reason="检测到支付确认页，模型置信度不足，请人工确认是否继续。",
                title="支付确认（Demo）",
                options=options,
                context={"url": "https://shop.example/checkout", "amount": "¥128.00"},
                timeout=120,
                channels=["terminal"],
                client_request_id=client_request_id,
                deny_option_ids={"deny"},
            )
        )

        log("HUMAN", "等待输入 1/2/3 …", color=YELLOW)
        choice = await _prompt_123(options)
        log("HUMAN", f"决策回传 option_id={choice} (source=terminal)", color=GREEN)

        await guardian._http.decide(
            InterventionDecision(
                intervention_id=iid,
                option_id=choice,
                source=DecisionSource.TERMINAL,
                decided_at=utc_now(),
                operator_id="demo:cli",
            )
        )

        try:
            decision = await wait_task
        except InterventionDeniedError as exc:
            log("AGENT", f"捕获异常 InterventionDeniedError: {exc}", color=RED)
            log("ROLLBACK", "执行 Safe Rollback：取消支付、恢复会话（模拟）", color=RED)
            log("DONE", "闭环完成：人类拒绝 → Agent 回滚", color=MAGENTA)
            return
        except InterventionTimeoutError as exc:
            log("AGENT", f"捕获异常 InterventionTimeoutError: {exc}", color=RED)
            log("ROLLBACK", "超时未决策 → 默认安全回滚（模拟）", color=RED)
            log("DONE", "闭环完成：超时 → 回滚", color=MAGENTA)
            return

        log(
            "AGENT",
            f"收到决策 status={decision.status} selected={decision.selected_option_id}",
            color=GREEN,
        )
        if decision.selected_option_id == "retry_later":
            log("AGENT", "选择稍后重试 → 暂停流水线（模拟）", color=YELLOW)
        else:
            log("AGENT", "选择确认 → 继续执行支付后续步骤（模拟）", color=GREEN)
        log("DONE", "闭环完成：断点 → 干预 → 继续", color=MAGENTA)


async def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
    log("BOOT", f"连接 Daemon {base_url}", color=DIM)
    try:
        async with AgentGuardian(base_url, local_terminal_prompt=False) as g:
            await g._http.health()
    except Exception as exc:
        log("ERROR", f"无法连接 Daemon：{exc}", color=RED)
        log(
            "HINT",
            "请先运行: python -m agent_guardian serve --host 127.0.0.1 --port 8787",
            color=YELLOW,
        )
        sys.exit(1)

    await _run_loop(base_url)


if __name__ == "__main__":
    asyncio.run(main())
