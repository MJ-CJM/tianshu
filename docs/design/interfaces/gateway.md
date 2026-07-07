# Gateway：HTTP / WebSocket 接口面

> FastAPI 提供天枢的主 API。路由按领域拆分到 15 个 `gateway/*_api.py` router，`gateway/api.py` 的 `gateway_router` 仅剩 WebSocket + 会诊兜底，均在 `app.py` 以 `prefix="/api"` 挂载。本篇讲接口契约与边界，路由对照源码核实。

## 1. Router 装配

| Router | 前缀 | 作用 |
|---|---|---|
| `gateway_router`（`gateway/api.py`） | `/api` | 兜底：WebSocket + 会诊（consultations）；Edict/Memorial/审批/记忆/成本/配置/位面等主 API 已拆到下一行 15 个域 router |
| `edicts_router` `execution_router` `audit_router` `cost_router` `credentials_router` `mcp_router` `memory_router` `personas_router` `providers_router` `skills_router` `system_router` `hongluisi_router` `tongzheng_router` `config_router` `universes_router` | `/api` | 各领域 CRUD/操作路由，端点明细见 §2-§8 |
| `/health` | 无 | 健康检查（沙箱/Deployer 探活也用它） |
| static web | 无 | 按 `settings.static_dir` 条件挂载前端 |

## 2. Edict / Memorial CRUD

| 方法 路由 | 行为 |
|---|---|
| `POST /api/edicts` | 创建 Edict + 首条 Memorial(submitted)，`fire("edict.submitted")` 后返回 202 |
| `POST /api/edicts/parse` | 自然语言解析成 Edict 草案 |
| `GET /api/edicts` | 分页/状态/搜索列表（带 metadata） |
| `GET /api/edicts/{id}` | 任务详情 |
| `PATCH /api/edicts/{id}` | 仅 open 状态可改目标/上下文 |
| `DELETE /api/edicts/{id}` | 已完成/取消或无 active memorial 时可删 |
| `PATCH /api/edicts/{id}/status` | 更新业务状态；cancelled 同时标 lifecycle complete |
| `POST /api/edicts/{id}/pause` `/resume` | lifecycle active↔paused |
| `GET /api/edicts/{id}/memorial` `/memorials` | 最新 / 全部 Memorial |
| `GET /api/memorials`、`GET /api/memorials/{id}` | 全局 Memorial 列表与详情 |
| `GET /api/memorials/by-persona/{persona_id}` | 按执行人格查 |

## 3. follow-up（运行中续接）

`POST /api/edicts/{id}/follow-up`（202）：拒绝已关闭 Edict、拒绝存在 active Memorial 的并发续接 → 从历史 Memorial 构建多轮上下文 → 创建带本轮 override 的新 Memorial → 直接调 `executor.execute_edict`，不重走 Scheduler/Planner。

## 4. Plan 审批

| 路由 | 行为 |
|---|---|
| `POST /api/edicts/{id}/plan/approve` | 审批 `plan.pending_review`，发 `plan.completed` 进入执行 |
| `POST /api/edicts/{id}/plan/reject` | 拒绝规划，Memorial → failed |

## 5. 审批队列（工具审批 / 长任务决策）

| 路由 | 行为 |
|---|---|
| `GET /api/approvals/pending_tool_calls` | 待人工批红的工具调用 |
| `POST /api/approvals/...`（decree 路径） | 提交批红决策 |
| `POST /api/decrees`（201） | 创建批红记录（工具审批/旧式奏折/L3 决策） |
| `GET /api/edicts/outer-loop/pending` | L3 待人工决策的长任务 |
| `POST /api/edicts/{id}/outer-loop/decide` | continue / accept_as_is / abort / modify_acceptance |
| `GET /api/edicts/{id}/supervision-report(s)` | 监督报告 |

## 6. WebSocket 实时事件流

`@gateway_router.websocket("/ws")`（即 `/api/ws`）：accept 后 `notifier.register_ws(ws)`，断线时 unregister。服务端单向推送，客户端 receive 仅用于保活。

推送的消息类型（由 Notifier 广播）：

| type | 触发 |
|---|---|
| `audit.completed` | 审计完成（urgent 跳过 debounce，否则 0.5s 去抖） |
| `execution.failed` | 执行失败 |
| `outer_loop.*` | 长任务迭代事件（实时，不去抖） |
| `stream.delta` / `stream.tool_start` / `stream.tool_end` | Agent 流式输出（`WebSocketStreamCallback`） |

## 7. 平行位面接口

| 路由 | 行为 |
|---|---|
| `GET /api/universes`、`GET /api/universes/{id}` | 列表 / 详情 |
| `GET /api/universes/_diff?a=&b=` | 行为配置差异 |
| `GET /api/universes/_status` | 总开关状态 |
| `POST /api/universes/enable` | 开启平行位面（ensure_genesis） |
| `POST /api/universes/{id}/branch` `/switch` `/archive` `/restore` | 分支/切换/归档/恢复 |
| `DELETE /api/universes/{id}` | 彻底删除 |
| `POST /api/universes/feedback` | 诏令结果赞踩 → 进 fitness |
| `POST /api/universes/evolve` | 手动触发演化 |
| `POST /api/universes/propose-code` | 触发代码变体提案闭环 |
| `POST /api/universes/{id}/promote-code` | 晋升代码变体（翻冠军 + 暂存 deploy 指针） |
| `GET /api/universes/{id}/code-diff` | 代码变体相对 fork 起点的 git diff |
| `GET /api/universes/{id}/eval-runs` | 评估记录 |

## 8. 其余路由族（概览）

| 前缀 | 覆盖 |
|---|---|
| `/api/scheduler/*` | 定时/周期 job 查询与取消 |
| `/api/dag/*` | DAG 执行查看、取消、重试 |
| `/api/workers*` | WorkerPool 状态 |
| `/api/consultations*` | 会诊 |
| `/api/audit/*`、`/api/policy/*`、`/api/routing/rules` | 审计统计、网络事件、会话策略规则、路由规则 |
| `/api/cost/*` | 成本汇总、记录、预算、导出 |
| `/api/memory/*`、`/api/memory-palace/*` | 持久记忆 CRUD、召回、压缩、反思、记忆宫殿检索 |
| `/api/personas*`、`/api/persona-templates*`、`/api/departments*` | 六部人格、模板、部门 |
| `/api/skills*`、`/api/tools*`、`/api/mcp/*` | 技能、工具开关、MCP server |
| `/api/providers*`、`/api/configs*`、`/api/agent-config`、`/api/config` | provider、LLM config、agent 配置 |
| `/api/system-prompt/*` | prompt 分层预览与文件编辑 |
| `/api/event-bus/*`、`/api/hooks/registry`、`/api/plugins*` | 事件总线、hook 注册表、插件 |
| `/api/notifications/channels` | 已注册通知渠道 |

## 9. 边界与约束

- 写操作统一 `ApiResponse{success, data, error}` 封套；列表带 `metadata`（total 等）。
- `POST /api/edicts` 走 `fire`（先持久化再后台调度），避免 HTTP 等待完整执行；返回 202。
- 提交去重靠 `idempotency_key + submitter`。
- 路由顺序敏感：`/universes/_diff` `/universes/_status` 等下划线前缀路由声明在 `/universes/{id}` 之前，避免被路径参数吞掉。

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)
