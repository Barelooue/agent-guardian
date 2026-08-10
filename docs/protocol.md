# Agent Guardian 交互协议规范

> **Protocol Version:** `1.0.0`  
> **状态:** Approved（Phase 1.1 已审阅批准；含审阅补充条款）  
> **相关文档:** [TASK_OVERVIEW.md](../TASK_OVERVIEW.md) · [ROADMAP.md](../ROADMAP.md)

本文档定义 SDK、Daemon、PNS Channel 之间的 JSON 载荷、状态机、错误码与并发语义。实现必须以本协议为准；破坏性变更必须提升 `protocol_version` 并附迁移说明。

---

## 1. 设计原则

| 原则 | 说明 |
| --- | --- |
| **协议优先** | 先稳定 Schema 与状态机，再写业务代码 |
| **幂等终态** | 任一干预只能被「成功结算」一次；超时与人类决策竞争时，先到达且合法的一方胜出 |
| **可恢复** | 未决请求必须持久化（SQLite / WAL），Daemon 重启后可恢复 |
| **可降级** | 远程通道失败时降级为终端交互，禁止静默崩溃 |
| **版本显式** | 所有信封携带 `protocol_version`；不兼容则拒绝并返回明确错误 |

---

## 2. 术语

| 术语 | 含义 |
| --- | --- |
| **Intervention** | 一次人机干预生命周期（从 SDK 发起到终态） |
| **intervention_id** | 全局唯一幂等键（UUID v4 字符串） |
| **Client** | 调用 SDK 的 Agent 进程 |
| **Daemon** | 本地守护进程，持有权威状态机 |
| **Channel** | 通知与决策回传通道（Telegram / Bark / Terminal 等） |
| **Terminal State** | `RESOLVED` / `TIMEOUT` / `CANCELLED` / `FAILED` 之一 |

---

## 3. 传输与信封

### 3.1 传输绑定（Phase 1）

| 链路 | 绑定 | 说明 |
| --- | --- | --- |
| SDK ↔ Daemon | WebSocket（主）+ HTTP（辅） | WS 用于订阅决策事件；HTTP 用于提交 / 查询 / 健康检查 |
| Daemon ↔ Channel | HTTPS（Telegram Bot API 等） | 出站推送；入站为 Bot Update 或本地 stdin |
| 决策权威 | **仅 Daemon** | Channel / SDK 不得绕过 Daemon 直接改写终态 |

### 3.2 通用信封（Envelope）

所有 WebSocket / HTTP JSON 消息外层统一使用以下结构：

```json
{
  "protocol_version": "1.0.0",
  "message_type": "intervention.create",
  "message_id": "9f3c2a1e-4b7d-4e2a-9c1f-8a6b0d5e4f21",
  "timestamp": "2026-08-09T12:00:00.000Z",
  "payload": {}
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `protocol_version` | string | 是 | 语义化版本；当前实现仅接受 `1.0.x` |
| `message_type` | string | 是 | 见 [§6 消息类型](#6-消息类型一览) |
| `message_id` | string (uuid) | 是 | 单次消息 ID，用于去重与日志关联 |
| `timestamp` | string (ISO-8601 UTC) | 是 | 消息产生时间 |
| `payload` | object | 是 | 与 `message_type` 对应的载荷 |

### 3.3 HTTP 约定（辅通道）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 探活 |
| `POST` | `/v1/interventions` | 创建干预（body = Envelope，`message_type=intervention.create`） |
| `GET` | `/v1/interventions/{intervention_id}` | 查询状态 |
| `POST` | `/v1/interventions/{intervention_id}/decision` | 提交决策（人类 / 终端 / Channel 回调统一入口） |
| `POST` | `/v1/interventions/{intervention_id}/cancel` | 客户端取消 |
| `WS` | `/v1/ws` | 订阅 `intervention.updated` 等事件 |

成功响应仍使用 Envelope；失败响应使用 `message_type=error`（见 [§8](#8-错误模型与错误码)）。

---

## 4. 状态机

### 4.1 状态枚举

```text
PENDING
NOTIFIED
AWAITING_HUMAN
RESOLVED
TIMEOUT
CANCELLED
FAILED
```

| 状态 | 是否终态 | 含义 |
| --- | --- | --- |
| `PENDING` | 否 | 已持久化，尚未完成通道推送 |
| `NOTIFIED` | 否 | 至少一个通道已成功投递交互卡片 |
| `AWAITING_HUMAN` | 否 | 已进入等待人类决策窗口（通常与 NOTIFIED 紧邻或合并推进） |
| `RESOLVED` | **是** | 人类（或终端降级）已给出有效决策 |
| `TIMEOUT` | **是** | 超过 `timeout_seconds` 未决策 |
| `CANCELLED` | **是** | 客户端主动取消，或上下文管理器提前退出 |
| `FAILED` | **是** | 不可恢复的系统错误（持久化失败、协议非法且无法纠正等） |

> **实现说明（Phase 1）：** `NOTIFIED` 与 `AWAITING_HUMAN` 在单通道场景可连续推进；协议仍保留两态，便于多通道「部分投递成功」扩展。

### 4.2 状态转换图

```text
                    create()
                       │
                       ▼
                   ┌────────┐
          ┌────────│ PENDING │────────┐
          │        └────┬───┘         │
          │             │ notify_ok   │ notify_all_failed
          │             ▼             │   + terminal fallback ok
          │        ┌─────────┐        │             │
          │        │ NOTIFIED │        │             │
          │        └────┬────┘        │             │
          │             │             │             │
          │             ▼             │             ▼
          │     ┌────────────────┐    │      ┌──────────────┐
          │     │ AWAITING_HUMAN │◄───┘      │ AWAITING_HUMAN│
          │     └───────┬────────┘           │ (via terminal)│
          │             │                    └───────┬──────┘
          │     ┌───────┼────────┬──────────┐        │
          │     │       │        │          │        │
          │  decision timeout  cancel    fatal    decision
          │     │       │        │          │        │
          ▼     ▼       ▼        ▼          ▼        ▼
     CANCELLED RESOLVED TIMEOUT CANCELLED FAILED  RESOLVED
```

### 4.3 合法转换表

| 从 \ 到 | PENDING | NOTIFIED | AWAITING_HUMAN | RESOLVED | TIMEOUT | CANCELLED | FAILED |
| --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| _(new)_ | ✓ |  |  |  |  |  |  |
| PENDING |  | ✓ | ✓* |  | ✓ | ✓ | ✓ |
| NOTIFIED |  |  | ✓ | ✓** | ✓ | ✓ | ✓ |
| AWAITING_HUMAN |  |  |  | ✓ | ✓ | ✓ | ✓ |
| RESOLVED |  |  |  |  |  |  |  |
| TIMEOUT |  |  |  |  |  |  |  |
| CANCELLED |  |  |  |  |  |  |  |
| FAILED |  |  |  |  |  |  |  |

\* 通道失败后直接降级终端时，允许 `PENDING → AWAITING_HUMAN`。  
\*\* 极短路径：决策与通知确认乱序到达时，允许在已终态保护下忽略迟到的 `NOTIFIED`（见并发规则）。

**非法转换必须被拒绝**，并返回 `AG_STATE_CONFLICT`（状态不变）。

### 4.4 并发、竞态与幂等（强制）

Daemon 是状态机的**唯一写权威**。对同一 `intervention_id` 的状态迁移必须在**互斥临界区**内完成（SQLite 事务 / `BEGIN IMMEDIATE` 或等价行锁）。

#### 典型竞态：人类点击 vs SDK 超时

```text
T0  状态 = AWAITING_HUMAN
T1  Telegram callback: decision=approve   ─┐
T2  SDK/Daemon timer: timeout             ─┼─ 几乎同时
T3  两者都尝试写入终态                     ─┘
```

**规则：**

1. 使用「Compare-And-Set」语义：`UPDATE ... WHERE intervention_id=? AND status IN (open_states)`。
2. 影响行数 = 1 → 本次迁移成功，广播 `intervention.updated`。
3. 影响行数 = 0 → 请求已被其他事件结算；返回当前权威状态，**不得二次写入**。
4. 迟到的决策 / 超时事件视为幂等确认：
   - 若已是期望终态 → 返回成功回显（HTTP 200 / WS ack），`idempotent=true`；
   - 若已是其他终态 → 返回 `AG_ALREADY_TERMINAL`，附带当前状态与胜出决策。

#### 其他竞态

| 场景 | 期望行为 |
| --- | --- |
| 重复提交相同 `client_request_id` | **必须**直接返回已有 intervention，且 `reused=true`，绝不新建 |
| 重复 `message_id` 的 decision | 忽略写，回显权威结果 |
| 终态后再 cancel | `AG_ALREADY_TERMINAL`（幂等回显当前终态，可附 `idempotent=true`） |
| 多 Channel 同时回传不同选项 | 仅第一份合法 decision 生效，其余拒绝 |
| SDK 超时 / Context Manager 退出 | **必须**主动发送 `intervention.cancel`，以便 Daemon 销毁远程悬挂按钮 |

---

## 5. 持久化要求（Daemon）

Phase 1 **禁止**仅用内存保存未决请求。

### 5.1 存储选型

- **推荐:** SQLite + `aiosqlite`，开启 WAL 模式（`PRAGMA journal_mode=WAL`）
- **最低表语义（逻辑模型，非强制物理 DDL）：**

| 列 | 说明 |
| --- | --- |
| `intervention_id` | PK |
| `client_request_id` | 客户端幂等键，UNIQUE |
| `status` | 当前状态 |
| `request_json` | 原始请求快照 |
| `decision_json` | 决策快照（可空） |
| `expires_at` | 绝对超时时间 UTC |
| `created_at` / `updated_at` | 审计时间 |
| `version` | 乐观锁版本号（每次合法迁移 +1） |

### 5.2 恢复语义

Daemon 启动时：

1. 加载所有非终态记录；
2. 若 `now >= expires_at` → 尝试迁移至 `TIMEOUT`（CAS）；
3. 否则重新订阅 / 重新投递策略按实现选择（至少保证 SDK 可通过 `GET` / WS 恢复等待）；
4. 不得丢失已 `RESOLVED` 的决策回显能力（短时内仍可查询）。

---

## 6. 消息类型一览

| message_type | 方向 | 说明 |
| --- | --- | --- |
| `intervention.create` | Client → Daemon | 创建干预 |
| `intervention.created` | Daemon → Client | 创建确认 |
| `intervention.decision` | Channel/Client → Daemon | 提交决策 |
| `intervention.cancel` | Client → Daemon | 取消 |
| `intervention.updated` | Daemon → Client | 状态变更推送 |
| `error` | Daemon → \* | 错误 |

---

## 7. JSON Schema 与载荷定义

> 下列 Schema 使用 JSON Schema Draft 2020-12 子集描述。实现可用 pydantic / jsonschema 校验。

### 7.1 公共定义

```json
{
  "$id": "https://agent-guardian.dev/schemas/common-1.0.0.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AgentGuardianCommon",
  "definitions": {
    "uuid": {
      "type": "string",
      "format": "uuid"
    },
    "iso8601": {
      "type": "string",
      "format": "date-time"
    },
    "protocol_version": {
      "type": "string",
      "pattern": "^1\\.0\\.\\d+$"
    },
    "InterventionStatus": {
      "type": "string",
      "enum": [
        "PENDING",
        "NOTIFIED",
        "AWAITING_HUMAN",
        "RESOLVED",
        "TIMEOUT",
        "CANCELLED",
        "FAILED"
      ]
    },
    "Option": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "label"],
      "properties": {
        "id": {
          "type": "string",
          "minLength": 1,
          "maxLength": 64,
          "description": "机器可读选项 ID，决策回传使用此值"
        },
        "label": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "description": "人类可读文案"
        },
        "style": {
          "type": "string",
          "enum": ["primary", "danger", "neutral"],
          "default": "neutral"
        },
        "destructive": {
          "type": "boolean",
          "default": false,
          "description": "高风险选项提示（UI/通道可用于二次确认）"
        }
      }
    },
    "SnapshotRef": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "content_type": {
          "type": "string",
          "enum": ["image/jpeg", "image/png", "image/webp", "text/plain"]
        },
        "width": { "type": "integer", "minimum": 1 },
        "height": { "type": "integer", "minimum": 1 },
        "size_bytes": { "type": "integer", "minimum": 0 },
        "sha256": { "type": "string", "minLength": 64, "maxLength": 64 },
        "url": { "type": "string", "format": "uri" },
        "base64": {
          "type": "string",
          "description": "Phase 1 可选；建议限制体积，Phase 2 起优先 URL"
        }
      }
    },
    "Envelope": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "protocol_version",
        "message_type",
        "message_id",
        "timestamp",
        "payload"
      ],
      "properties": {
        "protocol_version": { "$ref": "#/definitions/protocol_version" },
        "message_type": { "type": "string" },
        "message_id": { "$ref": "#/definitions/uuid" },
        "timestamp": { "$ref": "#/definitions/iso8601" },
        "payload": { "type": "object" }
      }
    }
  }
}
```

### 7.2 InterventionRequest（`intervention.create` payload）

```json
{
  "$id": "https://agent-guardian.dev/schemas/intervention-request-1.0.0.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "InterventionRequest",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "client_request_id",
    "reason",
    "options",
    "timeout_seconds"
  ],
  "properties": {
    "client_request_id": {
      "type": "string",
      "format": "uuid",
      "description": "客户端幂等键；重复提交返回同一 intervention"
    },
    "title": {
      "type": "string",
      "maxLength": 200,
      "default": "Agent 需要你的确认"
    },
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4000,
      "description": "为何需要人类介入"
    },
    "options": {
      "type": "array",
      "minItems": 1,
      "maxItems": 10,
      "items": { "$ref": "common-1.0.0.json#/definitions/Option" }
    },
    "context": {
      "type": "object",
      "description": "结构化上下文（URL、金额、步骤名等），勿放密钥",
      "additionalProperties": true
    },
    "snapshot": {
      "$ref": "common-1.0.0.json#/definitions/SnapshotRef",
      "description": "Phase 1 可选；Phase 2 强化"
    },
    "timeout_seconds": {
      "type": "integer",
      "minimum": 1,
      "maximum": 86400,
      "default": 300
    },
    "channels": {
      "type": "array",
      "description": "期望通道优先级列表；省略则使用 Daemon 默认策略",
      "items": {
        "type": "string",
        "enum": ["telegram", "bark", "webhook", "terminal"]
      },
      "uniqueItems": true
    },
    "metadata": {
      "type": "object",
      "additionalProperties": {
        "type": ["string", "number", "boolean", "null"]
      }
    },
    "agent_id": {
      "type": "string",
      "maxLength": 128,
      "description": "可选：多 Agent 并发时区分来源"
    }
  }
}
```

#### 示例：创建干预

```json
{
  "protocol_version": "1.0.0",
  "message_type": "intervention.create",
  "message_id": "11111111-1111-4111-8111-111111111111",
  "timestamp": "2026-08-09T12:00:00.000Z",
  "payload": {
    "client_request_id": "22222222-2222-4222-8222-222222222222",
    "title": "支付确认",
    "reason": "检测到支付确认页，模型置信度不足，请人工确认是否继续。",
    "options": [
      { "id": "approve", "label": "确认支付", "style": "danger", "destructive": true },
      { "id": "deny", "label": "拒绝并回滚", "style": "primary" },
      { "id": "retry_later", "label": "稍后重试", "style": "neutral" }
    ],
    "context": {
      "url": "https://shop.example/checkout",
      "amount": "¥128.00",
      "step": "checkout.confirm"
    },
    "timeout_seconds": 300,
    "channels": ["telegram", "terminal"],
    "agent_id": "browser-agent-01"
  }
}
```

### 7.3 InterventionCreated（`intervention.created` payload）

```json
{
  "$id": "https://agent-guardian.dev/schemas/intervention-created-1.0.0.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "InterventionCreated",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "intervention_id",
    "client_request_id",
    "status",
    "expires_at",
    "created_at"
  ],
  "properties": {
    "intervention_id": { "type": "string", "format": "uuid" },
    "client_request_id": { "type": "string", "format": "uuid" },
    "status": {
      "type": "string",
      "enum": ["PENDING", "NOTIFIED", "AWAITING_HUMAN"]
    },
    "expires_at": { "type": "string", "format": "date-time" },
    "created_at": { "type": "string", "format": "date-time" },
    "reused": {
      "type": "boolean",
      "default": false,
      "description": "true 表示命中 client_request_id 幂等复用；Daemon MUST 在重复提交时返回已有 intervention 并置 true"
    }
  }
}
```

#### `client_request_id` 幂等（强制）

1. `client_request_id` 在 Daemon 存储中 **UNIQUE**。
2. 若收到已存在的 `client_request_id`：
   - **不得**创建新的 `intervention_id`；
   - 返回已有记录对应的 `intervention.created`；
   - **必须**设置 `"reused": true`。
3. 即使原干预已进入终态，重复提交仍返回同一 `intervention_id`（`reused=true`），由客户端自行决定是否重新发起新的 `client_request_id`。

### 7.4 InterventionDecision（`intervention.decision` payload）

```json
{
  "$id": "https://agent-guardian.dev/schemas/intervention-decision-1.0.0.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "InterventionDecision",
  "type": "object",
  "additionalProperties": false,
  "required": ["intervention_id", "option_id", "source", "decided_at"],
  "properties": {
    "intervention_id": { "type": "string", "format": "uuid" },
    "option_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 64,
      "description": "必须匹配请求 options[].id"
    },
    "source": {
      "type": "string",
      "enum": ["telegram", "bark", "webhook", "terminal", "web_ui", "sdk"],
      "description": "决策来源通道"
    },
    "operator_id": {
      "type": "string",
      "maxLength": 128,
      "description": "可选：操作者标识（Telegram user id 等）"
    },
    "note": {
      "type": "string",
      "maxLength": 2000,
      "description": "可选自由文本补充"
    },
    "decided_at": { "type": "string", "format": "date-time" },
    "channel_message_id": {
      "type": "string",
      "description": "通道侧消息 ID，便于审计与去重"
    }
  }
}
```

#### 示例：Telegram 决策回传

```json
{
  "protocol_version": "1.0.0",
  "message_type": "intervention.decision",
  "message_id": "33333333-3333-4333-8333-333333333333",
  "timestamp": "2026-08-09T12:01:10.000Z",
  "payload": {
    "intervention_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "option_id": "deny",
    "source": "telegram",
    "operator_id": "tg:123456789",
    "note": "金额异常，先别付",
    "decided_at": "2026-08-09T12:01:09.500Z",
    "channel_message_id": "42"
  }
}
```

### 7.5 InterventionCancel（`intervention.cancel` payload）

```json
{
  "title": "InterventionCancel",
  "type": "object",
  "additionalProperties": false,
  "required": ["intervention_id", "reason"],
  "properties": {
    "intervention_id": { "type": "string", "format": "uuid" },
    "reason": {
      "type": "string",
      "enum": [
        "client_aborted",
        "client_timeout",
        "context_manager_exit",
        "agent_shutdown",
        "superseded"
      ]
    },
    "detail": { "type": "string", "maxLength": 1000 }
  }
}
```

### 7.6 InterventionUpdated（`intervention.updated` payload）

Daemon 在任何合法状态迁移后广播（WS）或作为 HTTP 写操作的响应体返回：

```json
{
  "$id": "https://agent-guardian.dev/schemas/intervention-updated-1.0.0.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "InterventionUpdated",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "intervention_id",
    "status",
    "version",
    "updated_at",
    "idempotent"
  ],
  "properties": {
    "intervention_id": { "type": "string", "format": "uuid" },
    "status": { "$ref": "common-1.0.0.json#/definitions/InterventionStatus" },
    "version": {
      "type": "integer",
      "minimum": 1,
      "description": "乐观锁版本；每次合法迁移递增"
    },
    "updated_at": { "type": "string", "format": "date-time" },
    "idempotent": {
      "type": "boolean",
      "description": "本次请求未改变状态（重复投递/迟到事件）"
    },
    "selected_option_id": {
      "type": ["string", "null"],
      "description": "RESOLVED 时必填"
    },
    "decision": {
      "description": "RESOLVED 时附带完整决策；其他状态可为 null",
      "oneOf": [
        { "$ref": "intervention-decision-1.0.0.json" },
        { "type": "null" }
      ]
    },
    "error": {
      "description": "FAILED / 冲突时可选附带",
      "oneOf": [
        { "$ref": "error-1.0.0.json" },
        { "type": "null" }
      ]
    },
    "active_channel": {
      "type": ["string", "null"],
      "enum": ["telegram", "bark", "webhook", "terminal", null],
      "description": "当前实际等待决策的通道（含降级后的 terminal）"
    }
  }
}
```

#### 示例：已解决

```json
{
  "protocol_version": "1.0.0",
  "message_type": "intervention.updated",
  "message_id": "44444444-4444-4444-8444-444444444444",
  "timestamp": "2026-08-09T12:01:10.100Z",
  "payload": {
    "intervention_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "status": "RESOLVED",
    "version": 4,
    "updated_at": "2026-08-09T12:01:10.050Z",
    "idempotent": false,
    "selected_option_id": "deny",
    "decision": {
      "intervention_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      "option_id": "deny",
      "source": "telegram",
      "operator_id": "tg:123456789",
      "note": "金额异常，先别付",
      "decided_at": "2026-08-09T12:01:09.500Z",
      "channel_message_id": "42"
    },
    "error": null,
    "active_channel": "telegram"
  }
}
```

---

## 8. 错误模型与错误码

### 8.1 Error 对象（`message_type=error` payload）

```json
{
  "$id": "https://agent-guardian.dev/schemas/error-1.0.0.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Error",
  "type": "object",
  "additionalProperties": false,
  "required": ["code", "message", "retryable"],
  "properties": {
    "code": {
      "type": "string",
      "pattern": "^AG_[A-Z0-9_]+$"
    },
    "message": {
      "type": "string",
      "minLength": 1,
      "maxLength": 2000
    },
    "retryable": {
      "type": "boolean"
    },
    "details": {
      "type": "object",
      "additionalProperties": true
    },
    "intervention_id": {
      "type": ["string", "null"],
      "format": "uuid"
    },
    "current_status": {
      "type": ["string", "null"]
    }
  }
}
```

#### 示例

```json
{
  "protocol_version": "1.0.0",
  "message_type": "error",
  "message_id": "55555555-5555-4555-8555-555555555555",
  "timestamp": "2026-08-09T12:01:10.200Z",
  "payload": {
    "code": "AG_ALREADY_TERMINAL",
    "message": "Intervention already in terminal state TIMEOUT; decision ignored.",
    "retryable": false,
    "intervention_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "current_status": "TIMEOUT",
    "details": {
      "attempted_transition": "AWAITING_HUMAN->RESOLVED",
      "winning_event": "timeout"
    }
  }
}
```

### 8.2 错误码表

| code | HTTP 建议 | retryable | 含义 | SDK 映射建议 |
| --- | ---: | :---: | --- | --- |
| `AG_OK` | 200 | — | 成功（通常不出现在 error payload） | — |
| `AG_INVALID_REQUEST` | 400 | false | Schema / 字段校验失败 | `AgentGuardianError` |
| `AG_UNSUPPORTED_VERSION` | 400 | false | `protocol_version` 不兼容 | `AgentGuardianError` |
| `AG_NOT_FOUND` | 404 | false | `intervention_id` 不存在 | `AgentGuardianError` |
| `AG_STATE_CONFLICT` | 409 | false | 非法状态转换 | `AgentGuardianError` |
| `AG_ALREADY_TERMINAL` | 409 | false | 已终态，拒绝二次写入 | 按当前终态映射* |
| `AG_TIMEOUT` | 408 / 200** | false | 干预超时 | `InterventionTimeoutError` |
| `AG_CANCELLED` | 200** | false | 已取消 | `AgentGuardianError`（或专用） |
| `AG_DENIED` | 200** | false | 人类选择拒绝类选项 / 策略拒绝 | `InterventionDeniedError` |
| `AG_CHANNEL_UNAVAILABLE` | 502 | true | 远程通道暂不可用 | 触发降级，不直接失败 |
| `AG_CHANNEL_RETRY_EXHAUSTED` | 502 | false | 指数退避耗尽 | 触发 Terminal 降级 |
| `AG_PERSISTENCE_ERROR` | 500 | true | SQLite 写入失败 | `AgentGuardianError` |
| `AG_INTERNAL` | 500 | true | 未分类内部错误 | `AgentGuardianError` |

\* 若终态为 `TIMEOUT` → `InterventionTimeoutError`；若决策 `option_id` 被策略标记为 deny → `InterventionDeniedError`。  
\*\* 对「查询已终态资源」可用 200 + `intervention.updated`；对「试图再次决策」用 409 + `AG_ALREADY_TERMINAL`。

### 8.3 SDK 异常树（实现约束，协议层对齐）

```text
AgentGuardianError
├── InterventionTimeoutError      # status=TIMEOUT 或等待期满
├── InterventionDeniedError       # 人类选择 deny / 明确拒绝
├── InterventionCancelledError    # status=CANCELLED
├── InterventionFailedError       # status=FAILED / AG_INTERNAL 等
└── ProtocolError                 # 版本不兼容、校验失败
```

**Context Manager 语义（Phase 1 SDK 必须实现）：**

```python
async with guardian.guard(
    reason="...",
    options=[...],
    deny_option_ids={"deny"},
) as decision:
    # 仅在 RESOLVED 且非 deny 时进入
    ...
# 超时 → InterventionTimeoutError
# 拒绝 → InterventionDeniedError
# 取消 → InterventionCancelledError
```

上层 Agent 应捕获上述异常并执行 Safe Rollback。

---

## 9. 通道投递、退避与降级

### 9.1 投递结果

Daemon 对每个 Channel 调用结果归一为：

| 结果 | 后续 |
| --- | --- |
| `delivered` | `PENDING → NOTIFIED → AWAITING_HUMAN` |
| `retryable_failure` | 指数退避重试 |
| `exhausted` | 按 `channels` 优先级尝试下一通道 |
| `all_remote_failed` | **必须**降级 `terminal`（若策略允许） |

### 9.2 Exponential Backoff（Telegram 等）

Phase 1 推荐默认参数（可配置）：

| 参数 | 默认值 |
| --- | --- |
| `initial_delay_ms` | 500 |
| `max_delay_ms` | 8000 |
| `multiplier` | 2.0 |
| `jitter_ratio` | 0.2 |
| `max_attempts` | 5 |

仅对网络抖动、429、5xx 等可重试错误退避；对 401/403 等配置错误立即失败并降级或 `FAILED`。

### 9.3 Terminal 降级协议

当远程通道不可用时，Daemon（或 SDK 本地模式）MUST：

1. 将 `active_channel` 设为 `terminal`；
2. 状态进入 `AWAITING_HUMAN`（或经 `NOTIFIED`）；
3. 在 stderr/stdout 打印人可读卡片（title、reason、编号选项）；
4. 从 stdin 读取选项序号或 `option_id`；
5. 构造 `source="terminal"` 的 `InterventionDecision` 走同一结算路径。

**禁止**在通道失败时让 Client 无限挂起且无任何本地提示。

### 9.4 Telegram `callback_data` 64 字节限制（强制）

Telegram Inline Keyboard 的 `callback_data` **上限为 64 字节**。不得把完整 UUID `intervention_id` 与 `option_id` 明文拼接进 `callback_data`（UTF-8 UUID 已 36 字节，易超限）。

**Phase 1 约定映射方案：**

| 组件 | 规则 |
| --- | --- |
| 短键 | Daemon 在投递 Telegram 卡片时为该干预生成 `callback_token`（建议 8–12 字符的 url-safe 随机串，或对 `intervention_id` 做截断哈希） |
| 选项索引 | `callback_data` 使用紧凑格式：`{token}:{opt_index}` 或 `{token}:{short_opt}`，总长 ≤ 64 字节 |
| 服务端映射表 | 持久化 `callback_token → intervention_id`，以及 `opt_index → option_id`（或随请求 options 顺序还原） |
| 回传还原 | Bot Update 到达后，Daemon/Telegram Channel **必须**还原为协议层完整 `InterventionDecision`（含完整 UUID `intervention_id` 与 `option_id`） |
| 生命周期 | 干预进入终态或收到 `intervention.cancel` 后，失效 token，并尽量 `editMessageReplyMarkup` 移除按钮 |

示例（合法短格式）：

```text
callback_data = "a1B2c3d4:0"   # token=a1B2c3d4, option index=0 → option_id=approve
```

### 9.5 取消同步与悬挂按钮销毁（强制）

当 SDK 侧出现以下情况时，**必须**主动向 Daemon 发送 `intervention.cancel`（除非已确认权威终态为 `RESOLVED`）：

1. 本地等待超时（即将或已经向调用方抛出 `InterventionTimeoutError` 之前/同时）；
2. `async with guardian.guard(...)` 在未成功进入业务块前因取消/异常退出，或调用方中止等待；
3. Agent 进程关闭前的尽力清理（best-effort）。

Daemon 收到 `cancel` 后 MUST：

1. CAS 迁移至 `CANCELLED`（若仍为开放态）；
2. 通知 Channel 层销毁远程交互控件（Telegram：移除 inline keyboard / 编辑消息提示已取消）；
3. 使 `callback_token` 失效，拒绝迟到点击。

> 说明：Daemon 自身的 `TIMEOUT` 结算与 SDK 的 `cancel` 可能竞态；以 CAS 为准，胜出终态唯一。SDK 在超时路径仍应发送 cancel，以便尽快拆除按钮（若已 `TIMEOUT`，cancel 以幂等终态回显处理）。

---

## 10. 超时与时钟

| 规则 | 说明 |
| --- | --- |
| 超时计算 | `expires_at = created_at + timeout_seconds`（Daemon 时钟为准） |
| 客户端时钟 | 仅用于展示；不得单独宣布超时终态 |
| 超时执行 | Daemon 定时器或懒检查（读时检查）均可，但必须 CAS 写入 `TIMEOUT` |
| SDK 等待 | 可在 `expires_at` 之后仍短轮询以获取权威终态，避免时钟漂移误判 |

---

## 11. 端到端时序（Happy Path）

```text
Client                Daemon                 Telegram              Human
  │                     │                       │                   │
  │ intervention.create │                       │                   │
  │────────────────────►│                       │                   │
  │                     │ persist PENDING       │                   │
  │ intervention.created│                       │                   │
  │◄────────────────────│                       │                   │
  │                     │ send card (backoff)   │                   │
  │                     │──────────────────────►│                   │
  │                     │ NOTIFIED/AWAITING     │  show buttons     │
  │ intervention.updated│                       │──────────────────►│
  │◄────────────────────│                       │                   │
  │ (blocked/awaiting)  │                       │   tap "deny"      │
  │                     │◄──────────────────────│◄──────────────────│
  │                     │ CAS → RESOLVED        │                   │
  │ intervention.updated│                       │                   │
  │◄────────────────────│                       │                   │
  │ resume / raise      │                       │                   │
```

### 竞态时序（Timeout 胜出）

```text
Human tap approve ──► Daemon (CAS fail, already TIMEOUT)
Timer fire     ──► Daemon (CAS ok → TIMEOUT) ──► Client: InterventionTimeoutError
```

---

## 12. 安全与隐私（协议层最低要求）

- 不得在 `context` / 日志中传输明文密钥、Cookie、完整支付凭证；
- Telegram Token 等仅存在于 Daemon 环境变量；
- `snapshot.base64` 应设上限（建议 ≤ 512 KiB）；超限拒绝或剥落后继续；
- 决策回显默认不要回传完整原始页面 HTML。

---

## 13. 兼容性承诺

| 变更类型 | 版本策略 |
| --- | --- |
| 新增可选字段 | `1.0.x` 补丁，旧客户端忽略未知字段 |
| 新增状态 / 错误码 | 次版本（未来 `1.1.0`），旧客户端可映射为 `AG_INTERNAL` |
| 删除/重命名必填字段或改变状态语义 | 主版本（`2.0.0`） |

Phase 1 实现必须声明：`protocol_version = "1.0.0"`。

---

## 14. Phase 1 实现检查清单（协议合规）

- [ ] 所有消息携带 `protocol_version` / `message_id` / `timestamp`
- [ ] 未决干预写入 SQLite（WAL），重启可恢复
- [ ] 状态迁移 CAS / 事务化，终态幂等
- [ ] 重复 `client_request_id` 返回已有 intervention 且 `reused=true`
- [ ] SDK 超时 / Context Manager 退出时发送 `intervention.cancel`
- [ ] Telegram `callback_data` ≤ 64 字节（token 映射，见 §9.4）
- [ ] 超时与决策竞态有单测覆盖
- [ ] Telegram 具备指数退避；失败可降级 Terminal
- [ ] SDK 异常树与 `async with guardian.guard(...)` 语义对齐本文件 §8.3
- [ ] 非法 `option_id` 拒绝写入并返回 `AG_INVALID_REQUEST`

---

## 15. 修订记录

| 版本 | 日期 | 说明 |
| --- | --- | --- |
| `1.0.0` | 2026-08-09 | Phase 1.1 初稿：信封、状态机、Schema、错误码、并发与降级语义 |
| `1.0.0` | 2026-08-09 | 审阅补充：Telegram callback_data 映射、`reused` 幂等强制、SDK cancel 同步销毁悬挂按钮 |

---

*协议 1.0.0 已批准。实现以本文为准。*
