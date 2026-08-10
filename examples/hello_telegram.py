"""
Telegram 通道联调示例。

若 Telegram 网络不通，本脚本会在【本终端】打印选项，输入 0/1/2 即可继续验收。

启动 Daemon 示例（PowerShell）:
  $env:TELEGRAM_BOT_TOKEN="..."
  $env:TELEGRAM_CHAT_ID="8855407236"
  # 国内常需要本地代理（端口按你的客户端改，常见 7890/10809）:
  $env:TELEGRAM_PROXY="http://127.0.0.1:7890"
  $env:PYTHONPATH="src"
  python -m agent_guardian serve --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import asyncio
import sys

from agent_guardian import (
    AgentGuardian,
    InterventionDeniedError,
    InterventionTimeoutError,
)
from agent_guardian.schemas import Option


async def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
    # local_terminal_prompt=True：Telegram 发不出时，在本终端输入 0/1/2
    async with AgentGuardian(base_url, local_terminal_prompt=True) as guardian:
        health = await guardian._http.health()
        print("Daemon health:", health)
        if not health.get("channels", {}).get("telegram"):
            print(
                "WARNING: Daemon 未启用 Telegram。"
                "请设置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 后重启 serve。"
            )
        else:
            print(
                "已启用 Telegram。若手机无消息，请检查网络/TELEGRAM_PROXY；"
                "同时可在本终端输入 0/1/2 作为降级决策。"
            )

        print("触发 ask_human —— 优先看 Telegram 按钮；否则在本终端输入选项…")
        try:
            decision = await guardian.ask_human(
                reason="Telegram 通道验收：请确认是否继续模拟支付。",
                title="Agent Guardian · Telegram",
                options=[
                    Option(id="approve", label="确认", style="danger", destructive=True),
                    Option(id="deny", label="拒绝", style="primary"),
                    Option(id="retry_later", label="稍后"),
                ],
                timeout=180,
                channels=None,
                deny_option_ids={"deny"},
            )
        except InterventionDeniedError as exc:
            print("[rollback] denied:", exc)
            return
        except InterventionTimeoutError as exc:
            print("[rollback] timeout:", exc)
            return

        print(
            f"[continue] selected={decision.selected_option_id} "
            f"status={decision.status} source="
            f"{decision.decision.source if decision.decision else None}"
        )


if __name__ == "__main__":
    asyncio.run(main())
