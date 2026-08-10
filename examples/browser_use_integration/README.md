# Browser-Use × Agent Guardian

当 Browser-Use（或任何 Playwright 自动化）遇到**验证码 / 支付 / 提交**时，用一层薄 Hook 调用 `AgentGuardian.ask_human()`：截取当前页面 → Telegram / Web UI 决策 → Approve 后继续，Deny 则中止。

## 三步跑通 Demo（推荐，无需 LLM）

### 1. 安装依赖

在仓库根目录：

```powershell
$env:PYTHONPATH="src"
pip install -e ".[dev]"
pip install playwright
playwright install chromium
```

### 2. 启动 Daemon

```powershell
# 可选：Telegram 远程确认
# $env:TELEGRAM_BOT_TOKEN="..."
# $env:TELEGRAM_CHAT_ID="..."
# $env:TELEGRAM_PROXY="http://127.0.0.1:7890"

python -m agent_guardian serve --host 127.0.0.1 --port 8787
```

### 3. 运行闭环脚本

另开终端：

```powershell
$env:PYTHONPATH="src"
python examples/browser_use_integration/demo_captcha_loop.py --headed
# 或 Phase 5 智能拦截 + 画布：
python examples/browser_use_integration/demo_guard_step.py --headed
```

然后在 [http://127.0.0.1:8787/ui/](http://127.0.0.1:8787/ui/)（或 Telegram）对干预点 **Approve**：

- `demo_captcha_loop`：验证码墙 → 支付确认  
- `demo_guard_step`：死循环预警 → 支付高危预警（可在截图上**点选/框选**后再 Approve）

### `guard_step` 片段

```python
result = await guardian.guard_step(
    action_name="click",
    target="#payBtn",
    dom_action="click pay",
    snapshot=bytes_to_snapshot(page_screenshot),
)
if result.proceeded and result.spatial_prompt:
    # 把人类画布标注注入给 Agent / VLM
    print(result.spatial_prompt)
```

脚本会截取 Chromium 页面图并附在干预卡片上。

---

## 目录结构

```
examples/browser_use_integration/
  README.md                 # 本说明
  requirements.txt          # browser-use / playwright（可选）
  fixtures/checkout_gate.html
  demo_captcha_loop.py      # ★ 推荐：Playwright 闭环，无需 LLM
  demo_browser_use_agent.py # 完整 Browser-Use Agent（需 API Key）
```

核心适配代码在包内（可被任意项目复用）：

```
src/agent_guardian/adapters/browser_use.py
  GuardianBrowserHook          # confirm_sensitive_action / request_captcha_help
  register_browser_use_tools() # 注册 @tools.action 给 Browser-Use
```

## 接入你自己的 Browser-Use Agent

```python
from agent_guardian import AgentGuardian
from agent_guardian.adapters.browser_use import (
    GuardianBrowserHook,
    register_browser_use_tools,
)
from browser_use import Agent

async with AgentGuardian() as guardian:
    hook = GuardianBrowserHook(guardian)
    tools = register_browser_use_tools(hook)
    agent = Agent(task="...", llm=llm, tools=tools)
    await agent.run()
```

Agent 会在高风险步骤调用自定义 Tool：

- `ask_guardian_before_sensitive_action` — 支付 / 提交前确认  
- `ask_guardian_captcha_help` — 验证码 / 登录墙  

## 上游依赖提醒

| 依赖 | 何时需要 | 说明 |
| --- | --- | --- |
| **playwright** + Chromium | `demo_captcha_loop.py` | 必装才能跑推荐 Demo |
| **browser-use** | `demo_browser_use_agent.py` | 完整 Agent 模式 |
| **LLM API Key**（如 `OPENAI_API_KEY`） | 仅 Agent 模式 | 闭环 Demo **不需要** |
| Agent Guardian Daemon | 所有模式 | `agent-guardian serve` |

> Windows 若系统代理导致本机 HTTP 502：SDK 已对 Daemon 使用 `trust_env=False`。Telegram 出站请单独设 `TELEGRAM_PROXY`。
