"""诊断 Telegram Token / chat_id / 网络是否能发出消息。"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("AG_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("AG_TELEGRAM_CHAT_ID")
    api_base = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    proxy = (
        os.getenv("TELEGRAM_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("http_proxy")
    )

    print("TOKEN set:", bool(token), f"(len={len(token) if token else 0})")
    print("CHAT_ID:", chat_id)
    print("API_BASE:", api_base)
    print("PROXY:", proxy or "(none)")

    if not token or not chat_id:
        print("\n缺少环境变量。请先在当前终端设置：")
        print('  $env:TELEGRAM_BOT_TOKEN="..."')
        print('  $env:TELEGRAM_CHAT_ID="8855407236"  # 用你 IDBot 显示的 Id')
        print('  $env:TELEGRAM_PROXY="http://127.0.0.1:7890"  # 按你的代理端口改')
        sys.exit(1)

    url = f"{api_base}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "✅ Agent Guardian 诊断消息：如果你看到这条，说明 Token/chat_id/网络正常。",
    }

    client_kwargs: dict = {"timeout": 30.0, "trust_env": False}
    if proxy:
        client_kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**client_kwargs) as client:
        try:
            me = await client.get(f"{api_base}/bot{token}/getMe")
            print("\ngetMe status:", me.status_code)
            print("getMe body:", me.text[:500])
            if me.status_code != 200 or not me.json().get("ok"):
                print("\n失败：Token 无效，或无法访问 api.telegram.org")
                sys.exit(2)

            resp = await client.post(url, json=payload)
            print("\nsendMessage status:", resp.status_code)
            print("sendMessage body:", resp.text[:800])
            data = resp.json()
            if data.get("ok"):
                print("\n成功：请打开 Telegram → My Agent Guardian，应看到一条诊断消息。")
            else:
                print("\n失败：请根据 description 检查 chat_id 是否正确、是否已向 Bot 发过 /start。")
                sys.exit(3)
        except httpx.TimeoutException:
            print("\n失败：连接超时。当前网络可能无法直连 api.telegram.org（常见于国内网络）。")
            sys.exit(4)
        except Exception as exc:
            print("\n失败：", type(exc).__name__, exc)
            sys.exit(5)


if __name__ == "__main__":
    asyncio.run(main())
