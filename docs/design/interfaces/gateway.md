# Gateway：HTTP / WebSocket 接口面

> FastAPI 提供天枢的主 API。路由按领域拆分到 `gateway/*_api.py`，由 `app.py`
> 统一以 `prefix="/api"` 挂载；`gateway/api.py` 只保留 WebSocket 和会诊兜底。Router
> 数量会随领域演进，本页不把数量当稳定契约。

## 1. Router 装配

| Router | 前缀 | 作用 |
|---|---|---|
| `gateway_router`（`gateway/api.py`） | `/api` | WebSocket + consultations 兜底 |
| `auth/audit/config/control_center/cost/credentials/decisions/edicts/evidence/evolution/estop/evals/execution/hongluisi/keqing/mcp/memory/model_providers/personas/providers/skills/system_audit/system/universes/workspace/tongzheng` routers | `/api` | 各领域 CRUD、治理与执行操作，完整装配见 `app.py` |
| `/health/live` `/health/ready` | 无 | 进程存活 / 依赖与恢复就绪；`/health` 仅保留旧 liveness 兼容 |
| static web | 无 | 按 `settings.static_dir` 条件挂载前端 |

## 2. Edict / Memorial CRUD

| 方法 路由 | 行为 |
|---|---|
| `POST /api/edicts` | 要求 `Idempotency-Key`；首次创建 Edict + 首条 Memorial + outbox 返回 202，相同请求重放返回 200 |
| `POST /api/edicts/parse` | 自然语言解析成 Edict 草案 |
| `GET /api/edicts` | 分页/状态/搜索列表（带 metadata） |
| `GET /api/edicts/{id}` | 任务详情 |
| `PATCH /api/edicts/{id}` | 仅 open 状态可改目标/上下文 |
| `DELETE /api/edicts/{id}` | 归档 tombstone：无未结束执行时隐藏列表项、取消 schedule，保留身份和治理证据 |
| `PATCH /api/edicts/{id}/status` | 更新业务状态；cancelled 同时标 lifecycle complete |
| `POST /api/edicts/{id}/pause` `/resume` | 深度任务在轮次边界 active↔paused |
| `POST /api/edicts/{id}/steer` | 给正在运行的深度任务追加下一轮指示 |
| `GET /api/edicts/{id}/memorial` `/memorials` | 最新 / 全部 Memorial |
| `GET /api/memorials`、`GET /api/memorials/{id}` | 全局 Memorial 列表与详情 |
| `GET /api/memorials/by-persona/{persona_id}` | 按执行人格查 |

## 3. follow-up（运行中续接）

`POST /api/edicts/{id}/follow-up`（202）要求 `Idempotency-Key`：拒绝已关闭 Edict 和
并发 active 根 Memorial → 在一个事务中创建/复用确定性 Memorial、attempt 与 outbox →
唤醒受监督的 planning/execution 链。相同 key 的相同 envelope 可安全重放，冲突内容拒绝。

## 4. Plan 审批

| 路由 | 行为 |
|---|---|
| `POST /api/edicts/{id}/plan/approve` | 审批 `plan.pending_review`，发 `plan.completed` 进入执行 |
| `POST /api/edicts/{id}/plan/reject` | 拒绝规划，Memorial → failed |

## 5. 审批队列（工具审批 / 长任务决策）

| 路由 | 行为 |
|---|---|
| `GET /api/approvals/pending_tool_calls` | 待人工裁决的工具调用 |
| `POST /api/approvals/...`（decree 兼容路径） | 提交裁决 |
| `POST /api/decrees`（201） | 创建旧式 Decree 兼容记录（工具审批/旧式奏折/L3 决策） |
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

## 7. Legacy Universe 与受治理 Evolution

Legacy `/api/universes` 负责快照、worktree 和评估，不再拥有 live mutation 权限：

| 路由 | 行为 |
|---|---|
| `GET /api/universes`、`GET /api/universes/{id}` | 列表 / 详情 |
| `GET /api/universes/_diff?a=&b=` | 行为配置差异 |
| `GET /api/universes/_status`、`POST /api/universes/enable` | 总开关状态 / ensure_genesis |
| `POST /api/universes/{id}/branch` `/archive` `/restore` | 分支、归档、恢复快照或 worktree；restore 不激活 live |
| `POST /api/universes/{id}/switch` | 兼容端点，固定 409 `promotion_preconditions_not_met` |
| `POST /api/universes/{id}/promote-code` | 兼容端点，固定 409 `promotion_preconditions_not_met` |
| `DELETE /api/universes/{id}` | 彻底删除非 champion 位面 |
| `POST /api/universes/feedback` `/evolve` | 反馈 / 手动触发 Legacy 演化建议 |
| `POST /api/universes/propose-code` `/propose-auto` | 代码变体提案、Gate 与评估；结果止于 `evaluated` / `recommended` |
| `GET /api/universes/{id}/code-diff` `/eval-runs` | 代码 diff / 评估记录 |

受治理 Candidate 的权威接口位于 `/api/evolution`：

| 路由 | 行为 |
|---|---|
| `GET /api/evolution` | Evolution Center 当前 Candidate、Gate 与 routing 快照 |
| `GET /api/evolution/candidates/{id}` | Candidate 当前版本与 lifecycle |
| `GET /api/evolution/candidates/{id}/gate` | 当前 Gate report |
| `POST /api/evolution/candidates/{id}/gate/evaluate` | 按 `expected_version` 重新评估 Gate |
| `POST /api/evolution/candidates/{id}/canary` | 经 `PromotionService` 开始受控 allocation |
| `POST /api/evolution/candidates/{id}/promote` | 经 `PromotionService` 激活并完成 lifecycle；Code 还要求绑定已批准的高风险 Decision |
| `POST /api/evolution/candidates/{id}/rollback` | 先把 allocation 归零，再由 adapter 恢复并写 rollback receipt |
| `GET /api/evolution/runs/{memorial_id}/assignment` | 按任务 owner 返回不可变 `RunAssignment` 与 `effective_overlay` |

`PromotionService` 是 lifecycle/routing 的唯一 mutation authority。生产装配目前只有 Skill
Candidate 具备真实 activation/rollback adapter；Memory/Policy/Persona/Code promotion
adapter 均 fail-closed，因此接口存在不等于这些类型已经可 live 晋升。当前也不存在
DeployPointer、自重启或健康检查自动回滚。

## 8. 其余路由族（概览）

| 前缀 | 覆盖 |
|---|---|
| `/api/scheduler/jobs*` | job 列表、取消、暂停、恢复、修改时间、立即运行、run 历史 |
| `/api/dag/*` | DAG 执行查看、取消、重试 |
| `/api/workers*` | WorkerPool 状态 |
| `/api/consultations*` | 会诊 |
| `/api/audit/*`、`/api/policy/*`、`/api/routing/rules` | 审计统计、网络事件、会话策略规则、路由规则 |
| `/api/cost/*` | 成本汇总、记录、预算、导出 |
| `/api/memory/*`、`/api/memory-palace/*` | 持久记忆 CRUD、召回、压缩、反思、记忆宫殿检索 |
| `/api/personas*`、`/api/persona-templates*`、`/api/departments*` | 六部人格、模板、部门 |
| `/api/skills*` | live 目录读取、候选提案/gate/stage 与 Pin；新建/修改不直接写 live，delete/archive 当前 409 |
| `/api/tools*`、`/api/mcp/*` | 工具开关、MCP server |
| `/api/providers*`、`/api/configs*`、`/api/agent-config`、`/api/config` | provider、LLM config、agent 配置 |
| `/api/system-prompt/*` | prompt 分层预览与文件编辑 |
| `/api/event-bus/*`、`/api/hooks/registry`、`/api/plugins*` | 事件总线、hook 注册表、实验插件清单；安装/激活 fail closed |
| `/api/notifications/channels` | 已注册通知渠道 |

## 9. 边界与约束

- 写操作统一 `ApiResponse{success, data, error}` 封套；列表带 `metadata`（total 等）。
- `POST /api/edicts` 先提交持久对象与 outbox，再后台调度，避免 HTTP 等待完整执行；
  首次接受返回 `202`，相同幂等请求重放返回 `200`。
- 提交去重靠 `idempotency_key + submitter`。
- 普通 principal 的 Edict、Memorial、Scheduler、DAG、决策和证据均按
  `Edict.submitter` 过滤；越权资源返回 `404`。`admin` 可跨用户访问；历史
  `submitter IS NULL` 行对普通 token fail closed。
- SystemAudit read/export、全局 audit/network events、Worker、配置、记忆和全局成本是
  admin 管理面。拥有自己的任务不等于拥有这些全局读取权限。
- 长任务只接受 `immediate/once + concurrency_policy=skip`；周期长任务返回明确
  `422`，不做隐式降级。
- `run-now` 必须带 `Idempotency-Key`；不会改变 job 原 schedule。
- 路由顺序敏感：`/universes/_diff` `/universes/_status` 等下划线前缀路由声明在 `/universes/{id}` 之前，避免被路径参数吞掉。

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)
