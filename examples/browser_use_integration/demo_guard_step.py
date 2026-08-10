"""
Phase 5 + Browser-Use：guard_step 自动捕获死循环 / 支付风险。

无需 LLM。演示：
1) 连续 3 次相似 click → 死循环预警 → ask_human
2) 点击支付 → 高风险预警 → ask_human（可在 /ui/ 画布点选）

用法（先启动 Daemon）:
  python examples/browser_use_integration/demo_guard_step.py --headed
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_guardian import AgentGuardian  # noqa: E402
from agent_guardian.adapters.browser_use import bytes_to_snapshot  # noqa: E402
from agent_guardian.smart import LoopDetector, SmartInterventionEngine  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "checkout_gate.html"


async def _run(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先: pip install playwright && python -m playwright install chromium")
        return 2

    file_url = FIXTURE.resolve().as_uri()
    print(f"Demo page: {file_url}")
    print(f"Web UI canvas: {args.base_url.rstrip('/')}/ui/")
    webbrowser.open(f"{args.base_url.rstrip('/')}/ui/")

    engine = SmartInterventionEngine(loop=LoopDetector(repeat_threshold=3))

    async with AgentGuardian(
        args.base_url,
        local_terminal_prompt=True,
        prefer_websocket=False,
        smart_engine=engine,
    ) as guardian:
        # Seed checkpoints for optional rollback-on-deny
        agent_state = {"step": "start", "captcha": False, "paid": False}
        guardian.checkpoint(dict(agent_state), label="start")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not args.headed)
            page = await browser.new_page(viewport={"width": 900, "height": 720})
            await page.goto(file_url)

            # --- Loop demo: hammer the same non-pay click ---
            print("\n[loop] 模拟连续 3 次相同 click（死循环）…")
            for i in range(3):
                shot = await page.screenshot(type="jpeg", quality=70)
                result = await guardian.guard_step(
                    action_name="click",
                    target="#solveBtn",
                    dom_action="click",
                    selector="#solveBtn",
                    url=page.url,
                    snapshot=bytes_to_snapshot(shot),
                    timeout=args.timeout,
                    agent_id="browser-use-demo",
                )
                print(
                    f"  attempt {i+1}: intervene={result.signal.intervene} "
                    f"code={result.signal.code} proceeded={result.proceeded}"
                )
                if result.signal.intervene:
                    if not result.proceeded:
                        print("人类拒绝死循环继续，结束。")
                        await browser.close()
                        return 1
                    if result.spatial_prompt:
                        print("空间指引:", result.spatial_prompt)
                    break

            # Human/agent unlocks captcha
            await page.click("#solveBtn")
            await page.wait_for_function("() => window.__captchaSolved === true")
            agent_state = {"step": "captcha_ok", "captcha": True, "paid": False}
            guardian.checkpoint(dict(agent_state), label="captcha_ok")
            print("验证码已解锁。")

            # --- Risk demo: pay click ---
            print("\n[risk] 即将确认支付 → guard_step 高危拦截…")
            shot2 = await page.screenshot(type="jpeg", quality=70)
            pay = await guardian.guard_step(
                action_name="click",
                target="#payBtn",
                dom_action="click confirm pay ¥128",
                selector="#payBtn",
                url=page.url,
                snapshot=bytes_to_snapshot(shot2),
                timeout=args.timeout,
                force_ask=False,
            )
            print(f"  risk signal: {pay.signal.code} proceeded={pay.proceeded}")
            if not pay.proceeded:
                if pay.restored_state is not None:
                    print("已回滚到:", pay.restored_state)
                await browser.close()
                return 1

            if pay.spatial_prompt:
                print("人类画布标注 →", pay.spatial_prompt)

            await page.click("#payBtn")
            await page.wait_for_function("() => window.__paymentSubmitted === true")
            guardian.checkpoint({"step": "paid", "captcha": True, "paid": True}, label="paid")
            print("\n✅ guard_step 闭环完成（死循环检测 + 支付风险 + 可选画布标注）。")
            if args.headed:
                await asyncio.sleep(1.5)
            await browser.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
