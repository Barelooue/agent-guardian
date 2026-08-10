"""
完整 Browser-Use Agent 示例（需要 LLM API Key + browser-use）。

Agent 被指示在遇到验证码 / 支付前调用自定义 Tool：
`ask_guardian_before_sensitive_action` / `ask_guardian_captcha_help`。

用法:
  set OPENAI_API_KEY=...   # 或 browser-use 支持的其他 provider
  python examples/browser_use_integration/demo_browser_use_agent.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_guardian import AgentGuardian  # noqa: E402
from agent_guardian.adapters.browser_use import (  # noqa: E402
    GuardianBrowserHook,
    register_browser_use_tools,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "checkout_gate.html"


async def _run(args: argparse.Namespace) -> int:
    try:
        from browser_use import Agent
    except ImportError:
        print(
            "缺少 browser-use。请先:\n"
            "  pip install -r examples/browser_use_integration/requirements.txt\n"
            "  playwright install chromium"
        )
        return 2

    # LLM import differs across browser-use versions; try common entry points
    llm = None
    for import_path in (
        ("browser_use", "ChatOpenAI"),
        ("browser_use.llm", "ChatOpenAI"),
        ("langchain_openai", "ChatOpenAI"),
    ):
        try:
            mod = __import__(import_path[0], fromlist=[import_path[1]])
            cls = getattr(mod, import_path[1])
            llm = cls(model=args.model)
            break
        except Exception:
            continue
    if llm is None:
        print(
            "无法创建 LLM。请安装 browser-use 并设置 OPENAI_API_KEY，"
            "或改用无需 LLM 的 demo_captcha_loop.py。"
        )
        return 2

    file_url = FIXTURE.resolve().as_uri()
    task = (
        f"Open {file_url}. There is a captcha gate and a payment button. "
        "BEFORE solving captcha or clicking pay, you MUST call the custom tools "
        "`ask_guardian_captcha_help` or `ask_guardian_before_sensitive_action`. "
        "Only continue after HUMAN_APPROVED / HUMAN_RESOLVED. "
        "Goal: unlock captcha then submit payment if the human approves."
    )

    async with AgentGuardian(
        args.base_url,
        local_terminal_prompt=True,
        prefer_websocket=False,
    ) as guardian:
        hook = GuardianBrowserHook(guardian, timeout=args.timeout)
        tools = register_browser_use_tools(hook)
        agent = Agent(task=task, llm=llm, tools=tools)
        print("启动 Browser-Use Agent… Web UI:", f"{args.base_url.rstrip('/')}/ui/")
        await agent.run()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--model", default="gpt-4o-mini")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
