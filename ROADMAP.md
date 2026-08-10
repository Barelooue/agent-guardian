# Roadmap · Agent Guardian / Shadow Intervener

> 分阶段任务目标与子任务清单。时间盒为建议节奏，可按人力微调，但阶段边界与验收标准保持稳定。

相关文档：[TASK_OVERVIEW.md](./TASK_OVERVIEW.md)

---

## 总览

| 阶段 | 时间盒 | 主题 | 核心交付物 |
| --- | --- | --- | --- |
| Phase 1 | Week 1–2 | MVP 核心引擎与极简 SDK | `ask_human` + Daemon + 单通道推送闭环 |
| Phase 2 | Week 3–4 | 端侧交互与 UI 增强 | 截图快照 + 悬浮窗 / Web 控制台 |
| Phase 3 | Week 5 | 热门开源 Agent 适配与 Demo | Computer Use / Browser-Use 适配 + 爆款 Demo |
| Phase 4 | Week 6+ | 社区化与高级特性 | 干预日志、多通道、开源发布 |

---

## Phase 1: MVP 核心引擎与极简 SDK（Week 1–2） — ✅ 已完成

| 字段 | 内容 |
| --- | --- |
| **目标** | 实现核心同步 / 异步 `ask_human` 阻塞唤醒机制，并打通至少一条远程推送通道（Telegram 或 Bark），形成「断点 → 通知 → 人类决策 → 唤醒」最小闭环。 |
| **验收标准** | ① 同步 `ask_human` 可阻塞并在决策后返回；② 异步 API 可用；③ Telegram/Bark 任一通道可完成一键确认；④ 超时 / 取消路径可测；⑤ 提供可运行的 CLI 示例。 |
| **风险** | Bot Token 与回调网络环境；进程崩溃导致未决请求丢失（用轻量落盘缓解）。 |
| **状态** | ✅ 2026-08-09 完成；验收见 `tests/` + `examples/hello_telegram.py` / `demo_p1_loop.py` |

### 子任务拆解

- **1.1 定义 JSON 交互协议** ✅
  - 起草 `InterventionRequest` / `InterventionDecision` / `Error` 字段与 `protocol_version`
  - 明确状态机：`PENDING → NOTIFIED → AWAITING_HUMAN → RESOLVED | TIMEOUT | CANCELLED`
  - 约定超时、幂等键（`intervention_id`）、错误码枚举
  - 输出 `docs/protocol.md` 初稿与示例 JSON

- **1.2 编写 Python SDK** ✅
  - 实现同步阻塞 `ask_human(...)`
  - 实现异步接口 `ask_human.async_(...)` / `await ask_human(...)`
  - 封装与 Daemon 的 WebSocket（主）或 HTTP JSON-RPC（辅）客户端
  - 加入超时、取消、基础重试与类型校验（如 pydantic）
  - 提供最小集成示例：`examples/hello_ask_human.py`

- **1.3 构建轻量 CLI 服务端（Daemon）** ✅
  - 使用 FastAPI / asyncio（或等价）实现本地守护进程
  - 暴露健康检查、提交干预、接收决策、查询状态等接口
  - 实现内存 + 可选落盘的未决请求队列
  - 提供 `agent-guardian serve`（或等价）一键启动 CLI

- **1.4 单通道推送（Telegram / Bark）** ✅
  - 实现可插拔 Channel 接口（`send_card` / `on_decision`）
  - 优先落地 Telegram Bot：发送交互卡片（按钮）并解析回传
  - 或落地 Bark / 通用 Webhook 作为备选 P0 通道
  - 配置项走环境变量（Token、Chat ID、Webhook URL）

- **1.5 Phase 1 工程化基线** ✅
  - 仓库结构、`pyproject.toml` / 依赖锁定
  - lint、类型检查、核心单元测试（协议解析、状态机、超时）
  - 临时 README：安装、配置、跑通闭环的三步说明

---

## Phase 2: 端侧交互与 UI 增强（Week 3–4） — ✅ 已完成

| 字段 | 内容 |
| --- | --- |
| **目标** | 提供桌面 / 移动侧更直观的干预体验：自动附带屏幕区域截图，并交付悬浮窗或 Web 控制台极简 UI，降低「看文字猜现场」的认知成本。 |
| **验收标准** | ① 干预请求可附带截图或区域快照；② 人类可在悬浮窗或 Web UI 中完成确认；③ 截图传输有大小 / 超时限制且失败可降级为纯文本卡片；④ UI 仅服务干预决策，不做复杂仪表盘。 |
| **风险** | 跨平台截图权限差异；大图推送导致通道失败（需压缩与降级策略）。 |
| **状态** | ✅ Web 控制台优先交付（`/ui/`）；截图 JPEG≤512KiB；Telegram `sendPhoto` 失败降级文本 |

### 子任务拆解

- **2.1 屏幕区域截图自动附带** ✅
  - SDK / Daemon 侧提供可选 `snapshot` 采集接口（全屏或指定区域）
  - 统一压缩格式（如 JPEG/WebP）与大小上限
  - 将快照元数据写入协议（`content_type`、`width`、`height`、可选 URL / base64）
  - 通道不支持图片时自动降级为文字摘要 + 本地预览链接

- **2.2 悬浮窗 / Web 控制台极简 UI** ✅
  - 桌面悬浮窗：展示 reason、options、截图缩略图、一键决策
  - 或优先交付轻量 Web 控制台（本地端口），移动端浏览器亦可打开
  - UI 只做「当前待处理干预」列表与详情，避免卡片堆砌与统计噪音
  - 决策动作与 Daemon 状态机打通，保证与 Bot 通道行为一致

- **2.3 体验与稳定性增强** ✅
  - 多待处理干预的排队与去重展示
  - 本地通知音 / 系统托盘提示（可选）
  - 截图失败、权限拒绝时的可读错误与降级路径
  - 补充 E2E 示例：触发断点 → UI 确认 → Agent 恢复

---

## Phase 3: 热门开源 Agent 适配与 Demo（Week 5）

| 字段 | 内容 |
| --- | --- |
| **目标** | 打通主流开源 Agent 框架，产出可复现、可传播的 Demo，证明中间件「五分钟接入」价值。 |
| **验收标准** | ① 至少完成 Claude Computer Use 与 Browser-Use 其中两者的适配示例；② GitHub README 达到可对外传播质量；③ Demo 视频或动图可展示完整人机协同闭环。 |
| **风险** | 上游框架 API 变动；演示环境依赖（浏览器、桌面权限）导致复现成本高。 |

### 子任务拆解

- **3.1 适配 Claude Computer Use 插件 / Hook**
  - 识别高风险 / 低置信度动作插入点（点击支付、提交表单、未知弹窗等）
  - 封装薄适配层：在工具调用前后调用 `ask_human`
  - 提供可运行示例与最短接入文档

- **3.2 适配 Browser-Use 插件 / Hook** ✅
  - 在导航、填表、点击等关键步骤增加可选人工确认策略
  - 针对验证码 / 登录墙场景给出推荐接入模式
  - 示例脚本覆盖「遇到验证码 → Telegram 求助 → 继续爬取」叙事
  - 交付：`src/agent_guardian/adapters/browser_use.py` + `examples/browser_use_integration/`

- **3.3 撰写 GitHub README 与示例代码** ✅
  - 项目定位一句话、架构简图、快速开始
  - 多语言示例目录（至少 Python）
  - Demo 录制脚本：场景说明、预期画面、失败兜底 → `docs/demo_guide.md`
  - Badges、License、贡献入口占位

- **3.4 Demo 打磨与传播物料** ✅（指南已交付；成片由维护者按指南录制）
  - 录制 1–2 分钟闭环 Demo（Gif / 短视频）— 步骤见 `docs/demo_guide.md`
  - 整理「痛点 → 中间件 → 一键确认」叙事文案
  - Issue 模板：适配请求、Bug、通道贡献（占位，Phase 4 补全）

---

## Phase 4: 社区化与高级特性（Week 6+）

| 字段 | 内容 |
| --- | --- |
| **目标** | 沉淀干预日志以支撑后续 RLHF / DPO 等对齐数据收集；扩展多通知通道；完成开源发布与社区协作机制。 |
| **验收标准** | ① 干预全生命周期可结构化导出；② 至少新增 1–2 个通道适配器；③ 完成正式开源发布（Tag / Release / Changelog）；④ 贡献指南与治理文档就位。 |
| **风险** | 日志中的隐私与敏感信息；通道生态碎片化导致维护成本上升。 |

### 子任务拆解

- **4.1 干预日志记录（对齐数据友好）** ⏳（导出 CLI 已提前交付）
  - 记录：触发原因、上下文摘要、选项、人类决策、时延、超时与取消
  - 导出格式：JSONL / Parquet（可选），字段稳定可版本化 → ✅ `agent-guardian export --output dataset.jsonl`
  - 提供脱敏策略（URL、账号、金额等可配置掩码）
  - 文档说明如何用于 RLHF / DPO 数据管线（不做训练本身）

- **4.2 多通道扩展**
  - 标准化 Channel Plugin 接口与注册机制
  - 扩展候选：企业微信 / Discord / Slack / 邮件 / 通用 Webhook 增强
  - 通道能力矩阵（是否支持按钮、图片、回传）写入文档
  - 社区贡献模板：新增通道的最小 PR 清单

- **4.3 开源发布与社区化** ⏳（物料已就绪，待打 Tag / GitHub Release）
  - v0.1.0 Release：安装包、Changelog、迁移说明（如有）→ ✅ `CHANGELOG.md` / `CONTRIBUTING.md` / `LICENSE`
  - `CONTRIBUTING.md`、Code of Conduct、安全披露渠道 → ✅ CONTRIBUTING（CoC/安全渠道可后续补）
  - CI 完善：多版本测试、示例冒烟、文档链接检查
  - 路线图看板（GitHub Projects / Discussions）同步本文件阶段状态

- **4.4 可选高级特性（按优先级择机）**
  - 多操作者协同（认领 / 转交干预）
  - 策略引擎：按规则自动决定是否升级人工
  - TypeScript SDK 正式版与 Daemon 多语言客户端
  - 远程只读「影子观察」模式（不阻断，仅告警）

---

## Phase 5–8：Perceive → Intervene → Steer → Evolve（进阶控制塔）

> 面向 Autonomous Agent Swarm 的感知-干预-纠偏-自我进化能力。在 Phase 1–4 中间件之上增量扩展。

### Phase 5: 智能预测性干预 — ✅ 引擎 + SDK `guard_step` 已交付

| 交付 | 路径 | 状态 |
| --- | --- | --- |
| 死循环 / 死锁检测 | `smart/loop_detector.py` | ✅ |
| 高风险策略打分 | `smart/risk_evaluator.py` | ✅ |
| 置信度 / logits 门控 | `smart/uncertainty_evaluator.py` | ✅ |
| 组合引擎 | `smart/engine.py` | ✅ |
| SDK `guard_step` / checkpoint / rollback | `client/guardian.py` | ✅ |
| Demo | `examples/browser_use_integration/demo_guard_step.py` | ✅ |

### Phase 6: 多模态视觉画布指引 — ✅ 画布 + 空间 Prompt + Rollback 已落地

| 交付 | 路径 | 状态 |
| --- | --- | --- |
| Web 画布点选 / BBox | `daemon/static/ui/index.html` | ✅ |
| `SpatialAnnotation` 协议字段 | `schemas/spatial.py` | ✅ |
| SoM 空间 Prompt 注入 | `ui/spatial.py` | ✅ |
| `guardian.rollback(steps)` | `client/checkpoints.py` | ✅ |

### Phase 7: 多 Agent 集群控制塔 — ✅ 服务端 + API/WS + Swarm 大厅

| 交付 | 路径 | 状态 |
| --- | --- | --- |
| AgentHubManager | `swarm/swarm_hub.py` | ✅ |
| REST `/api/swarm/agents` | `daemon/swarm_api.py` | ✅ |
| WS `/ws/swarm` | `daemon/swarm_api.py` | ✅ |
| Web Swarm 大厅 + Force Takeover | `daemon/static/ui/index.html` | ✅ |

### Phase 8: 自动化 DPO 自我进化闭环 — ✅ 导出 / Benchmark / train 配方

| 交付 | 路径 | 状态 |
| --- | --- | --- |
| DatasetCurator（Qwen2-VL/LLaVA/ORPO） | `align/dataset_curator.py` | ✅ |
| CLI `export-dpo` / `train` / `benchmark` | `daemon/cli.py` | ✅ |
| 10-task AgentBenchmark | `align/benchmark.py` | ✅ |

---

## 里程碑检查清单

| 里程碑 | 完成定义 | 建议状态标记 |
| --- | --- | --- |
| M1 · 协议冻结（Phase 1 中期） | `protocol_version` 初版合并，SDK/Daemon 可联调 | `[x]` |
| M2 · MVP 闭环（Phase 1 结束） | Telegram/Bark + `ask_human` 端到端可用 | `[x]` |
| M3 · 可视化干预（Phase 2 结束） | 截图 + 悬浮窗/Web UI 可用 | `[x]` |
| M4 · 框架适配 Demo（Phase 3 结束） | Browser-Use 示例 + README + Demo 录制指南（Computer Use 待补） | `[x]` 部分 |
| M5 · 开源发布（Phase 4） | Release + 日志导出 + 多通道扩展启动 | `[ ]` |

---

## 协作约定（简表）

| 事项 | 约定 |
| --- | --- |
| 阶段推进 | 上一阶段验收标准未达成前，不扩散下一阶段范围 |
| 协议变更 | 破坏性变更必须升 `protocol_version` 并写迁移说明 |
| 范围控制 | 优先闭环与 DX，避免过早构建重型云控与复杂仪表盘 |
| 文档同步 | 阶段完成时回写本文件状态，并更新 `TASK_OVERVIEW.md` 中的成功标准对照 |

---

*本路线图与 [TASK_OVERVIEW.md](./TASK_OVERVIEW.md) 配套使用，作为开源迭代的执行基线。*
