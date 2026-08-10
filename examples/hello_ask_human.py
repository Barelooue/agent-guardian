"""Minimal Phase 1联调示例：ask_human + 本终端输入决策。"""

from __future__ import annotations

import asyncio
import sys

from agent_guardian import AgentGuardian, InterventionDeniedError, InterventionTimeoutError
from agent_guardian.schemas import Option


async def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
    async with AgentGuardian(base_url) as guardian:
        print(f"Connecting daemon at {base_url} ...")
        print("提示：选项会打印在【本终端】，请在这里输入 0/1/2 或 approve/deny 后回车。")
        try:
            decision = await guardian.ask_human(
                reason="检测到支付确认页，置信度不足，请人工确认是否继续。",
                title="支付确认",
                options=[
                    Option(id="approve", label="确认支付", style="danger", destructive=True),
                    Option(id="deny", label="拒绝并回滚", style="primary"),
                    Option(id="retry_later", label="稍后重试"),
                ],
                context={"url": "https://shop.example/checkout", "amount": "¥128.00"},
                timeout=120,
                channels=["terminal"],
            )
        except InterventionDeniedError as exc:
            print(f"[rollback] denied: {exc}")
            return
        except InterventionTimeoutError as exc:
            print(f"[rollback] timeout: {exc}")
            return

        print(f"[continue] selected={decision.selected_option_id} status={decision.status}")


if __name__ == "__main__":
    asyncio.run(main())
