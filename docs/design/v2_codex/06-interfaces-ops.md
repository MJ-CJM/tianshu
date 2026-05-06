# 06 接口、前端与运维边界

## 1. FastAPI 路由面

`create_app()` 注册：

| Router | 前缀 | 作用 |
|---|---|---|
| `gateway_router` | `/api` | Edict、Memorial、审批、记忆、成本、配置、WebSocket 等主 API |
| `credentials_router` | `/api` | 外部凭证管理 |
| `hongluisi_router` | `/api` | 鸿胪寺网络能力配置/状态 |
| `tongzheng_router` | `/api` | 飞书通政司运行配置 |
| `/health` | 无 | 健康检查 |
| static web | 无 | 按 `settings.static_dir` 条件挂载 |

## 2. 任务 API

核心接口：

| 接口 | 行为 |
|---|---|
| `POST /api/edicts` | 创建 Edict + 首条 Memorial，后台触发事件链 |
| `GET /api/edicts` | 分页/状态/搜索列表 |
| `GET /api/edicts/{id}` | 任务详情 |
| `PATCH /api/edicts/{id}` | 仅 open 状态可改目标/上下文 |
| `DELETE /api/edicts/{id}` | 已完成/取消或无 active memorial 时可删 |
| `POST /api/edicts/{id}/pause` | lifecycle active -> paused |
| `POST /api/edicts/{id}/resume` | lifecycle paused -> active |
| `POST /api/edicts/{id}/follow-up` | 创建新 Memorial，带历史上下文继续执行 |
| `PATCH /api/edicts/{id}/status` | 更新业务状态，cancelled 会把 lifecycle 标 complete |

## 3. 规划、长任务与监督接口

| 接口 | 行为 |
|---|---|
| `POST /api/edicts/{id}/plan/approve` | 审批 plan.pending_review 并发 `plan.completed` |
| `POST /api/edicts/{id}/plan/reject` | 拒绝规划，Memorial failed |
| `GET /api/edicts/{id}/iterations` | 查看 outer loop 迭代 |
| `GET /api/edicts/outer-loop/pending` | 查看 L3 待人工决策 |
| `POST /api/edicts/{id}/outer-loop/decide` | 提交 continue/accept_as_is/abort/modify_acceptance |
| `GET /api/edicts/{id}/supervision-reports` | 获取多监督官报告 |

## 4. 批红与审批接口

| 接口 | 行为 |
|---|---|
| `POST /api/decrees` | 创建通用批红 |
| `GET /api/approvals/pending_tool_calls` | 查看等待工具审批 |
| `POST /api/approvals/tool_decision` | 批准/拒绝工具调用，可授予 session rule |

审批状态的权威来源是 `ApprovalManager` 的 in-memory pending 队列，并结合事件 payload 和 Decree 持久化。

## 5. WebSocket 与通知

`/api/ws` 由 `Notifier` 管理连接。Notifier 负责：

- 发送执行失败；
- 发送审计完成；
- 广播 outer loop 实时事件；
- 推送 daily digest；
- 通过 ChannelRegistry 发送飞书 webhook、钉钉、邮件等外部渠道。

飞书 app bot 模式下，`FeishuBot` 直接订阅 EventBus，不走旧 webhook ChannelRegistry。

## 6. 飞书助手模式

飞书入站管线位于 `gateway/feishu/`：

```text
connection/webhook
  -> Dispatcher
  -> security allowlist
  -> group mention gate
  -> text batching
  -> command / mode router
  -> assistant branch or edict branch
  -> outbound cards / reactions / approval cards
```

关键设计：

- 允许用户白名单。
- 群聊必须 @bot。
- 普通文本有短暂 batch delay，合并连续消息。
- slash command 直接处理，不进入 batch。
- card action 独立分发。
- chat anchor 记录当前 Edict，支持围绕一个任务持续对话。
- `submit_edict` 等敕令工具默认受通政司开关控制。

## 7. 前端页面映射

当前 Web 前端在 `web/src/pages/`。核心页面和后端关系：

| 页面 | 主要 API |
|---|---|
| `EdictCreatePage`, `EdictListPage`, `EdictDetailPage` | `/edicts`, `/memorials`, `/events`, follow-up, pause/resume |
| `ApprovalQueuePage` | `/approvals/pending_tool_calls`, `/approvals/tool_decision` |
| `MemoryDashboardPage` | `/memory/*`, `/memory-palace/*` |
| `PersonaDashboardPage`, `PersonaDetailPage` | `/personas/*`, prompt preview |
| `CostDashboardPage` | `/costs/*`, `/providers/*` |
| `OpsMonitorPage` | `/ws`, network events |
| `ConsultationPage` | `/consultation/*` |
| `TongzhengPage`, `HongluisiPage` | 飞书和网络能力配置 |
| `SystemManagementPage` | LLM/provider/plugin/tool/config |

## 8. 配置与数据路径

| 路径/表 | 用途 |
|---|---|
| `~/.tianshu/tianshu.db` | 主 SQLite 控制面 |
| `~/.tianshu/memory/` | Markdown 记忆 |
| `~/.tianshu/memory/drawers.sqlite3` | DrawerStore |
| `~/.tianshu/personas/{id}/` | 运行时 persona 覆盖 |
| `~/.tianshu/skills/` | 用户级 skills |
| `~/.tianshu/logs/` | 运行日志 |
| `personas/` | git 跟踪 persona 模板 |
| `src/tianshu/skills/builtin/` | 内建 skills |
| `llm_configs`, `providers` | 模型/provider 配置 |
| `network_credentials` | 外部网络凭证，Fernet 加密 |
| `channel_configs` | 飞书等通道运行配置 |

## 9. 运维注意点

- `TIANSHU_SECRET_MASTER_KEY` 缺失时，依赖 Fernet 的外部凭证能力会降级。
- `TIANSHU_FIRECRAWL_API_KEY`、`TIANSHU_TAVILY_API_KEY`、`TIANSHU_JINA_API_KEY` 按网络工具启用情况配置。
- LiteLLM provider 配置在 DB 与 env 初始值之间协作，ProviderManager 负责同步。
- SQLite 使用 WAL 和线程锁，适合当前单进程 FastAPI；多进程部署前要重新评估事件总线和 in-memory approval pending。
- `EventBus` 是进程内 async bus，不是分布式消息队列；横向扩容需要外置队列或单 worker 模型。
- `ApprovalManager` pending 状态在内存中，重启后需要依靠事件/Decree 恢复策略才能做到强恢复；当前更适合作为单进程常驻应用。
