"""
Browser-Use / Playwright 闭环 Demo（无需 LLM API Key）。

叙事：打开本地收银台 → 遇到验证码 → ask_human（截图）→ 人类 Approve
→ 脚本解锁验证码 → 点击支付前再次 ask_human → Approve 后提交。

用法（先启动 Daemon）:
  python examples/browser_use_integration/demo_captcha_loop.py
  python examples/browser_use_integration/demo_captcha_loop.py --headed
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

from agent_guardian import AgentGuardian, InterventionDeniedError  # noqa: E402
from agent_guardian.adapters.browser_use import GuardianBrowserHook  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "checkout_gate.html"


async def _run(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "缺少依赖 playwright。请先执行:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        return 2

    if not FIXTURE.is_file():
        print(f"找不到 fixture: {FIXTURE}")
        return 2

    file_url = FIXTURE.resolve().as_uri()
    print(f"打开演示页: {file_url}")
    print(f"Web UI: {args.base_url.rstrip('/')}/ui/")
    webbrowser.open(f"{args.base_url.rstrip('/')}/ui/")

    async with AgentGuardian(
        args.base_url,
        local_terminal_prompt=True,
        prefer_websocket=False,
    ) as guardian:
        hook = GuardianBrowserHook(guardian, timeout=args.timeout)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not args.headed)
            page = await browser.new_page(viewport={"width": 900, "height": 720})
            await page.goto(file_url)

            # ---- Gate 1: captcha ----
            print("\n[1/2] 检测到验证码墙 → 请求人类确认…")
            shot1 = await page.screenshot(type="jpeg", quality=70)
            try:
                await hook.request_captcha_help(
                    url=page.url,
                    screenshot=shot1,
                    detail="收银台出现人机验证码。请 Approve 后由脚本自动解锁（或你在 headed 窗口手动点「我是人类」）。",
                )
            except InterventionDeniedError:
                print("人类拒绝：放弃任务。")
                await browser.close()
                return 1

            # Simulate human/agent solving captcha after approval
            await page.click("#solveBtn")
            await page.wait_for_function("() => window.__captchaSolved === true")
            print("验证码已解锁。")

            # ---- Gate 2: payment click ----
            print("\n[2/2] 即将点击「确认支付」→ 高风险拦截…")
            shot2 = await page.screenshot(type="jpeg", quality=70)
            try:
                await hook.confirm_sensitive_action(
                    reason="Agent 即将点击支付按钮提交 ¥128.00 订单，请确认是否继续。",
                    action="click_pay",
                    url=page.url,
                    selector="#payBtn",
                    page_title=await page.title(),
                    screenshot=shot2,
                )
            except InterventionDeniedError:
                print("人类拒绝支付：未点击支付按钮。")
                await browser.close()
                return 1

            await page.click("#payBtn")
            await page.wait_for_function("() => window.__paymentSubmitted === true")
            print("\n✅ 闭环完成：验证码 → 人类确认 → 支付提交。")
            if args.headed:
                await asyncio.sleep(2)
            await browser.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Guardian × Browser checkout demo")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show Chromium window (default: headless)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
