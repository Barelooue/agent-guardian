# Demo 录制指南（约 60–90 秒）

面向 GitHub README / 社交传播的短视频或 GIF。目标叙事：**痛点 → 中间件拦截 → 一键确认 → Agent 继续**。

## 推荐画面布局

| 区域 | 内容 |
| --- | --- |
| 左半屏 | 终端：Daemon 日志（可选缩小）+ `demo_captcha_loop.py --headed` 的 Chromium 收银台 |
| 右半屏 | 浏览器打开 [http://127.0.0.1:8787/ui/](http://127.0.0.1:8787/ui/) **或** Telegram 对话 |

不要先单独打开 `/ui/` 再全屏截系统桌面——Demo 脚本会截 **Chromium 页面**，卡片里应看到收银台，而不是控制台自己。

## 录制前准备（30 秒，可剪掉）

1. 仓库根目录安装依赖：`pip install -e ".[dev]"` + `pip install playwright` + `python -m playwright install chromium`
2. 终端 A：`$env:PYTHONPATH="src"; python -m agent_guardian serve`
3. （可选）配置 `TELEGRAM_*`，使卡片同时出现在手机上，传播画面更强
4. 右屏先打开 `/ui/`，确认空白列表
5. 开始录屏（OBS / Windows Win+G / 手机拍显示器均可）

## 镜头脚本（约 1 分钟）

| 时间 | 画面与旁白要点 |
| --- | --- |
| 0:00–0:08 | 左屏启动 `python examples/browser_use_integration/demo_captcha_loop.py --headed`。旁白：「无人值守 Agent 打开收银台，遇到验证码。」 |
| 0:08–0:20 | 右屏 `/ui/`（或 Telegram）弹出**第一张卡片**，带页面截图。旁白：「Agent Guardian 自动截图并请求人类确认。」 |
| 0:20–0:28 | 点击 **Approve / 已处理，继续**。左屏验证码解锁。 |
| 0:28–0:40 | 即将点「确认支付」→ **第二张卡片**（高风险支付）。旁白：「支付前再次拦截，防止误操作。」 |
| 0:40–0:50 | 再次 Approve → 左屏显示支付成功。旁白：「人类确认后 Agent 继续；若 Deny 则安全中止。」 |
| 0:50–0:60 | （可选）切到终端跑 `python -m agent_guardian export --output dataset.jsonl`，展示一行 `chosen`/`rejected`。旁白：「干预记录可导出为 DPO 数据。」 |

## 失败兜底（录制时若卡住）

| 现象 | 处理 |
| --- | --- |
| `/ui/` 无卡片 | 确认 Daemon 在 8787；Demo 终端无连接错误 |
| 截图空白 / 全黑 | 用 `--headed`；勿最小化 Chromium |
| Telegram 收不到 | 跑 `examples/diagnose_telegram.py`；检查 `TELEGRAM_PROXY` |
| `playwright` 命令找不到 | 用 `python -m playwright install chromium` |
| 想演示 Deny | 第二次干预点 Deny，旁白改为「拒绝后 Agent 不会提交支付」 |

## 成片检查清单

- [ ] 卡片上能看清**收银台截图**（不是 IDE / 控制台）
- [ ] 至少完整走通 **验证码 → 支付** 两次决策
- [ ] 片头或字幕出现项目名 **Agent Guardian** 与一句定位
- [ ] 片尾可附仓库链接或 `pip install` / clone 提示（文字即可）

## 文案一句话（可贴视频简介）

> Agent 卡住验证码或要付钱时，Agent Guardian 截图喊你一键确认——Telegram / Web UI 都能管，还能导出 DPO 数据。
