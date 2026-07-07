# 使用指南

从启动服务到下达诏令、再到查看结果的完整流程。术语见 [../reference/glossary.md](../reference/glossary.md)，环境准备见 [getting-started.md](getting-started.md)。

## 1. 启动服务

天枢是单一 FastAPI 后端（默认 `http://localhost:8000`）；Web、CLI、飞书、Telegram 都连同一个后端，走同一组 `/api` 契约。启动方式见 [getting-started.md](getting-started.md)。CLI 默认连环境变量 `TIANSHU_API_URL`（缺省 `http://localhost:8000`）。

## 2. 下达诏令（5 种入口）

一切从「诏令(Edict)」开始。五种入口殊途同归，最终都落到 `POST /api/edicts` → `fire("edict.submitted")`，返回 202 后由后台事件链处理。提交去重靠 `idempotency_key + submitter`。

| 入口 | 操作 |
|---|---|
| **Web** | 前端首页 → 新建 Edict → 填目标/上下文 → 提交 |
| **HTTP API** | `POST /api/edicts`（创建）/ `POST /api/edicts/parse`（自然语言先解析成草案） |
| **CLI** | `tianshu edict submit …` 下旨；`tianshu edict list` 列表；`tianshu edict get <id>` 详情 |
| **飞书** | 助手模式（自然对话）/ 敕令模式（纯文本下旨），配置见 [../ops/feishu-setup.md](../ops/feishu-setup.md) |
| **Telegram** | 同飞书双模式，配置见 [../ops/telegram-setup.md](../ops/telegram-setup.md) |

> 定时/周期任务**不**走 `edict submit`，由对话内的 `schedule_edict` 工具创建（自然语言描述时间）。

## 3. 任务怎么流转（可观测点）

下旨后链路为 排期(Scheduler)→规划(Planner)→执行(Agent/DAG/OuterLoop)→审计(Auditor)→通知(Notifier)，详见 [../design/runtime-flow.md](../design/runtime-flow.md)。每个环节都可观测：

| 观测项 | 入口 |
|---|---|
| 事件时间线 | `GET /api/edicts/{id}` 详情 / `tianshu event list` |
| 实时事件流 | WebSocket `/api/ws`（`stream.delta` / `audit.completed` / `outer_loop.*`） |
| 待审批工具 | `GET /api/approvals/pending_tool_calls` / 前端审批队列页 |
| 规划审批 | `POST /api/edicts/{id}/plan/approve` 或 `/plan/reject` |
| 执行结果（奏折） | `GET /api/edicts/{id}/memorial` / `tianshu memorial get <id>` |
| 审计 | `GET /api/audit/*` / 前端审计仪表板 |
| 成本 | `GET /api/cost/*` / `tianshu cost summary` |
| 记忆 | `GET /api/memory-palace/*` / 前端记忆宫殿页 |

## 4. 常见操作

| 操作 | 方式 |
|---|---|
| 续接对话（follow-up） | `POST /api/edicts/{id}/follow-up`（带本轮 override，不重走规划） |
| 暂停 / 恢复 | `POST /api/edicts/{id}/pause` / `/resume` |
| 查 / 管定时任务 | `tianshu schedule list` / `cancel`；`GET /api/scheduler/*` |
| 长任务人工决策（L3） | `GET /api/edicts/outer-loop/pending` → `POST …/outer-loop/decide`（continue / accept_as_is / abort / modify_acceptance） |
| 批红（工具审批） | `POST /api/decrees` / `tianshu decree submit` |
| 会诊 | `GET/POST /api/consultations*` / 前端会诊页 |
| 位面切换 / 分支 | `POST /api/universes/{id}/switch`、`/branch`（见 [../design/universe/](../design/universe/)） |

## 5. 各接口面详解

HTTP/WS、CLI、Web 页面、通知渠道的完整接口契约见 [../design/interfaces/](../design/interfaces/)。
