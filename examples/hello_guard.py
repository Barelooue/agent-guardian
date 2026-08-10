"""Context manager 示例：deny/timeout 自动抛错供 Safe Rollback。"""

from __future__ import annotations

import asyncio

from agent_guardian import (
    AgentGuardian,
    InterventionDeniedError,
    InterventionTimeoutError,
)


async def main() -> None:
    async with AgentGuardian() as guardian:
        try:
            async with guardian.guard(
                reason="即将执行高风险删除操作",
                options=["approve", "deny"],
                timeout=60,
                channels=["terminal"],
                deny_option_ids={"deny"},
            ) as decision:
                print(f"human approved: {decision.selected_option_id}")
                # ... continue agent critical section ...
        except InterventionDeniedError:
            print("Safe Rollback: human denied")
        except InterventionTimeoutError:
            print("Safe Rollback: timeout (cancel synced to daemon)")


if __name__ == "__main__":
    asyncio.run(main())
