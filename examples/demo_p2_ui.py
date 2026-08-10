"""
Phase 2 验收 Demo：截图 + Web 控制台决策

重要说明：
- 真实 Agent 应在「看到支付页的那一刻」截图（或用 Playwright page.screenshot），
  再调用 ask_human；截的是 Agent 当前操作的界面，不是控制台。
- 本 Demo 为避免「先开浏览器再截屏 → 拍到控制台自己」的误导，
  默认生成一张「模拟支付页」示意图作为 snapshot，然后再打开 /ui/。

用法:
  终端 A:  python -m agent_guardian serve --host 127.0.0.1 --port 8787
  终端 B:  python examples/demo_p2_ui.py
  浏览器:  http://127.0.0.1:8787/ui/
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import sys
import webbrowser

from PIL import Image, ImageDraw, ImageFont

from agent_guardian import (
    AgentGuardian,
    InterventionDeniedError,
    InterventionTimeoutError,
)
from agent_guardian.schemas import Option, SnapshotRef


def _mock_payment_snapshot() -> SnapshotRef:
    """合成一张「模拟收银台」图，演示 snapshot 能力（非真实屏幕）。"""
    img = Image.new("RGB", (720, 420), color=(245, 247, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 40, 680, 380), fill=(255, 255, 255), outline=(200, 205, 215), width=2)
    draw.rectangle((40, 40, 680, 110), fill=(30, 64, 175))
    try:
        font = ImageFont.truetype("arial.ttf", 28)
        font_sm = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
        font_sm = font
    draw.text((60, 60), "模拟收银台 / Mock Checkout", fill=(255, 255, 255), font=font)
    draw.text((70, 150), "商户：Example Shop", fill=(40, 40, 40), font=font_sm)
    draw.text((70, 190), "订单金额：¥128.00", fill=(40, 40, 40), font=font_sm)
    draw.text((70, 230), "支付方式：模拟银行卡", fill=(40, 40, 40), font=font_sm)
    draw.text(
        (70, 290),
        "（这是 Demo 合成图，不是浏览器控制台截图）",
        fill=(120, 120, 120),
        font=font_sm,
    )
    draw.rectangle((70, 330, 250, 365), fill=(220, 38, 38))
    draw.text((95, 336), "确认支付", fill=(255, 255, 255), font=font_sm)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    return SnapshotRef(
        content_type="image/jpeg",
        width=img.size[0],
        height=img.size[1],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        base64=base64.b64encode(data).decode("ascii"),
    )


async def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
    use_real_screen = "--real-screen" in sys.argv

    if use_real_screen:
        print("模式：真实全屏截图（请先把「支付页/目标窗口」放到前台，再运行本脚本）")
        print("注意：不要先打开 /ui/，否则会拍到控制台自己。")
        snapshot = None
        include_screenshot = True
    else:
        print("模式：使用「模拟支付页」示意图（推荐验收）")
        snapshot = _mock_payment_snapshot()
        include_screenshot = False

    # 先创建干预（带 snapshot），再打开 UI，避免拍到控制台
    async with AgentGuardian(base_url, local_terminal_prompt=False) as guardian:
        print("触发 ask_human…")
        wait = asyncio.create_task(
            guardian.ask_human(
                reason="Phase 2：请根据截图确认是否继续模拟支付。",
                title="支付确认（含截图）",
                options=[
                    Option(id="approve", label="确认支付", style="danger", destructive=True),
                    Option(id="deny", label="拒绝并回滚", style="primary"),
                    Option(id="retry_later", label="稍后重试"),
                ],
                timeout=180,
                channels=["terminal"],
                snapshot=snapshot,
                include_screenshot=include_screenshot,
                deny_option_ids={"deny"},
            )
        )
        await asyncio.sleep(0.4)
        ui = f"{base_url.rstrip('/')}/ui/"
        print(f"现在打开 Web 控制台: {ui}")
        try:
            webbrowser.open(ui)
        except Exception:
            pass

        try:
            decision = await wait
        except InterventionDeniedError as exc:
            print("[rollback] denied:", exc)
            return
        except InterventionTimeoutError as exc:
            print("[rollback] timeout:", exc)
            return

        print(
            f"[continue] selected={decision.selected_option_id} status={decision.status}"
        )


if __name__ == "__main__":
    asyncio.run(main())
