# Agent Guardian / Shadow Intervener

> 轻量级「状态守护与远程干预」中间件 —— 为无人值守的桌面 / 移动端 Agent 提供人机协同安全网。

---

## 1. 项目背景与核心痛点

随着大模型 Agent 从「对话助手」走向「可操作环境的执行体」，越来越多的任务会在无人值守场景下长时间运行，例如：

- 跨站点自动化爬虫与数据采集
- 表单填写、账号登录与业务流转
- 桌面端跨应用操作（文件、浏览器、办公软件）
- 支付、下单、删除等高风险动作的自动执行

这些场景中，Agent 并不总是能「顺利跑完」。常见失败模式包括：

| 断点类型 | 典型表现 | 无人值守后果 |
| --- | --- | --- |
| 验证码 / 人机验证 | 页面出现 CAPTCHA、滑块、短信验证 | Agent 卡住或反复重试 |
| 登录失效 / Session 过期 | Cookie 失效、二次认证 | 任务中断，状态丢失 |
| 低置信度决策 | 页面结构变化、语义歧义 | 误点、误填、死循环 |
| 高风险操作 | 支付、转账、批量删除 | 静默执行造成不可逆损失 |

**核心痛点**：现有 Agent 框架擅长「规划与执行」，却普遍缺少「遇到不确定 / 高风险时主动请求人类介入」的标准化中间件。结果是：

1. Agent 静默崩溃或空转重试，运维成本高；
2. 高风险动作缺少二次确认，安全边界薄弱；
3. 人类无法以低成本方式远程「点一下」完成干预；
4. 干预过程与上下文碎片化，难以沉淀为后续对齐数据。

因此，我们需要一层独立于具体 Agent 框架的 **Human-in-the-Loop（人机协同）中间件**：在断点处捕获状态、推送交互卡片、等待人类决策，再唤醒 Agent 继续执行。

---

## 2. 系统核心定位

**Agent Guardian（状态守护） / Shadow Intervener（影子干预者）** 不是又一个 Agent 框架，而是挂在 Agent 外围的「安全网与远程指挥官」。

```text
┌─────────────────────────────────────────────────────────┐
│                    业务 Agent 层                        │
│   (Claude Computer Use / Browser-Use / 自研 Agent …)    │
└───────────────────────────┬─────────────────────────────┘
                            │ ask_human() / Hook
┌───────────────────────────▼─────────────────────────────┐
│         Agent Guardian 中间件（本项目）                   │
│  SDK · 本地守护进程 · 通知通道 · 干预状态机              │
└───────────────────────────┬─────────────────────────────┘
                            │ 交互卡片 / 快照 / 决策回传
┌───────────────────────────▼─────────────────────────────┐
│              人类操作者（Human Operator）                 │
│     Telegram / Bark / Webhook / 桌面悬浮窗 / Web 控制台 │
└─────────────────────────────────────────────────────────┘
```

### 2.1 定位关键词

- **Middleware，而非 Framework**：不负责规划、工具调用或记忆；只负责「断点捕获 → 人类决策 → 恢复执行」。
- **Human-in-the-Loop First**：默认假设无人值守，但任何不确定节点都可升级为人工确认。
- **Shadow Intervener**：以「影子」方式介入——尽量不侵入 Agent 主逻辑，通过 SDK / Hook 实现最小耦合集成。
- **轻量可嵌入**：Python / TypeScript 双端 SDK，分钟级接入；本地 Daemon 可单机运行，无需重型基础设施。

### 2.2 非目标（Non-Goals）

- 不替代 LangChain / AutoGen / CrewAI 等编排框架；
- 不实现完整 RPA 引擎或浏览器自动化内核；
- 不强制自建复杂云端控制平面（MVP 以本地 + 推送通道为主）。

---

## 3. 核心功能模块

系统由三大模块构成，彼此通过明确的协议边界解耦。

### 3.1 SDK / Hook 模块

面向 Agent 开发者的极简接入层，提供阻塞与异步唤醒能力。

**核心 API 形态（示意）：**

```python
from agent_guardian import ask_human

# 同步阻塞：挂起当前执行，直到人类回复或超时
decision = ask_human(
    reason="检测到支付确认页，置信度不足",
    options=["确认支付", "取消", "稍后重试"],
    context={"url": current_url, "amount": "¥128.00"},
    timeout=300,
)

# 异步非阻塞：提交干预请求，通过回调 / Future 获取结果
future = ask_human.async_(...)
```

**职责：**

- 封装与本地 Daemon 的通信（WebSocket / HTTP / JSON-RPC）；
- 定义断点上下文（reason、options、截图、元数据）；
- 支持同步阻塞与异步非阻塞两种调用模式；
- 提供装饰器 / Hook，便于嵌入现有 Agent 工具调用链路。

### 3.2 本地守护进程（Daemon）

轻量常驻进程，负责状态机监听与断点生命周期管理。

**职责：**

- 接收 SDK 发起的干预请求，分配 `intervention_id`；
- 维护状态机：`PENDING → NOTIFIED → AWAITING_HUMAN → RESOLVED / TIMEOUT / CANCELLED`；
- 调用 PNS Channel 推送交互卡片；
- 接收人类决策并回写，唤醒挂起的 Agent 调用；
- 可选：本地队列、超时重试、幂等与去重。

**设计原则：**

- 单机可运行，依赖少；
- 崩溃可恢复（请求落盘 / 轻量持久化）；
- 与 Agent 进程解耦，支持多 Agent 客户端并发接入。

### 3.3 通知与远程交互通道（PNS Channel）

Push / Notify / Serve 通道层，把「交互卡片」送到人类触手可及之处。

| 通道 | 适用场景 | MVP 优先级 |
| --- | --- | --- |
| Telegram Bot | 跨设备远程确认 | P0 |
| Bark / Webhook | iOS 推送 / 自建集成 | P0 / P1 |
| 桌面悬浮窗 | 本机即时干预 | P1 |
| Web 控制台 | 可视化回看与批量处理 | P1 |
| 企业 IM / 邮件等 | 后续扩展 | P2 |

**交互卡片最小字段：**

- `title` / `reason`：为何需要人类介入
- `options`：一键可选动作
- `snapshot`：可选截图或 DOM / 状态摘要
- `expires_at`：超时策略
- `reply_channel`：决策回传地址

---

## 4. 系统技术选型推荐

以下为开源 MVP 的推荐基线，允许在实现阶段按团队栈微调，但应保持协议层稳定。

### 4.1 语言与运行时

| 层级 | 推荐 | 说明 |
| --- | --- | --- |
| SDK（主） | **Python 3.11+** | Agent 生态主流语言，异步友好 |
| SDK（次） | **TypeScript / Node.js** | 覆盖 JS Agent 与前端控制台 |
| Daemon | **Python（FastAPI / asyncio）** 或 **TypeScript（Node）** | MVP 优先 Python，便于与 SDK 同仓 |
| 桌面悬浮窗 | **Electron / Tauri** 或轻量 WebView | Phase 2 引入 |
| 移动推送 | Telegram Bot API、Bark、通用 Webhook | 优先复用成熟通道 |

### 4.2 通信协议

| 链路 | 推荐协议 | 说明 |
| --- | --- | --- |
| SDK ↔ Daemon | **WebSocket + JSON**（主） / **HTTP JSON-RPC**（辅） | 长连接便于阻塞唤醒；HTTP 便于探活与简易接入 |
| Daemon ↔ PNS | HTTPS Webhook / Bot API | 出站推送与回调 |
| 人类决策回传 | HTTPS Callback / Bot Update | 统一映射为内部 `Decision` 事件 |

**协议原则：**

- 载荷使用 JSON，字段版本化（`protocol_version`）；
- 请求 / 响应带 `intervention_id` 保证幂等；
- 超时、取消、错误码显式定义，避免 Agent 侧无限等待。

### 4.3 推荐接入的开源 Agent 框架

| 框架 / 能力 | 适配方式 | 价值 |
| --- | --- | --- |
| **Claude Computer Use** | 工具调用前后 Hook / 包装高风险动作 | 桌面操作断点演示强 |
| **Browser-Use** | 页面动作拦截与验证码检测钩子 | Web Agent 场景覆盖广 |
| OpenAI / Anthropic 工具调用 Agent | 在 tool 层插入 `ask_human` | 通用示例 |
| 自研脚本型 Agent | 直接调用 SDK API | 最低接入成本 |

Phase 3 将优先打通 **Claude Computer Use** 与 **Browser-Use**，并沉淀可复现 Demo。

---

## 5. 代码规范与开源质量要求

本项目以「可被他人五分钟接入」为第一质量标准，工程约束如下。

### 5.1 架构与耦合

- **低耦合**：SDK、Daemon、Channel 分层清晰，通道可插拔（Strategy / Plugin）；
- **协议优先**：先稳定 JSON Schema / 类型定义，再扩展 UI 与适配器；
- **最小侵入**：Agent 侧理想接入成本 ≤ 数行代码或一个装饰器。

### 5.2 API 与类型安全

- Python 使用类型注解 + `pydantic`（或等价）做请求 / 响应校验；
- TypeScript SDK 提供完整 `.d.ts` / 严格模式类型；
- 公开 API 保持极简：`ask_human`、`ask_human.async_`、通道配置、超时与取消。

### 5.3 异步与可靠性

- 同时支持 **同步阻塞** 与 **异步非阻塞**；
- I/O 路径默认非阻塞，避免拖垮 Agent 事件循环；
- 明确超时、重试、取消语义；Daemon 重启后可恢复未决干预（至少尽力而为）。

### 5.4 开源交付标准

- 清晰的 `README`、快速开始、最小可运行示例；
- CI：lint、类型检查、单元测试；
- 约定式提交与 Changelog；
- 安全默认值：密钥走环境变量，不在日志中打印敏感上下文；
- License、贡献指南与 Issue / PR 模板齐全。

### 5.5 体验原则（DX）

1. **默认可用**：一条命令启动 Daemon，一段示例跑通 Telegram 确认闭环；
2. **失败可读**：超时、通道失败、协议错误给出可操作提示；
3. **可观测**：结构化日志 + 干预生命周期事件，便于排障与后续数据收集。

---

## 6. 成功标准（Definition of Success）

MVP 成功，当且仅当满足：

1. Agent 脚本中调用 `ask_human()` 可被正确阻塞；
2. 人类通过至少一种远程通道（如 Telegram）完成一键决策；
3. 决策回传后 Agent 被唤醒并继续执行；
4. 超时与取消路径行为明确、可测试；
5. 新用户可依据文档在短时间内完成本地跑通。

进阶成功（Phase 2–4）：

- 截图快照与悬浮窗干预可用；
- 至少 2 个主流开源 Agent 框架有官方适配示例；
- 干预日志可导出，支撑后续 RLHF / DPO 数据管线；
- 项目完成开源发布并具备社区贡献入口。

---

## 7. 文档索引

| 文档 | 说明 |
| --- | --- |
| [ROADMAP.md](./ROADMAP.md) | 分阶段目标、时间盒与子任务清单 |
| `README.md`（待建） | 用户向快速开始与示例 |
| `docs/protocol.md`（待建） | JSON 交互协议详细规范 |

---

*本文档描述项目任务要求与系统总览，作为开源开发与协作的基线共识。具体排期与子任务见 `ROADMAP.md`。*
