# 接口层（interfaces）实现现状

**相关设计**：[../../design/interfaces/](../../design/interfaces/)

> 覆盖 `gateway/`（HTTP/WS）、`notifier/`（出站通知 + WS 广播）、`gateway/feishu` `gateway/telegram`（IM bot）、`web/`（前端）、`cli/`（命令行）。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」。

## 1. 模块清单

| 区域 | 路径 | 关键类 / 文件 |
|---|---|---|
| HTTP/WS 路由（兜底） | `gateway/api.py` | `gateway_router`：`/ws` WebSocket + `/consultations` |
| 域 router | `gateway/*_api.py` | auth、ownership、task、decision、evidence、audit、system 等领域路由；`app.py` 统一 include |
| IM 门面 | `gateway/feishu/__init__.py`、`gateway/telegram/__init__.py` | `FeishuBot`、`TelegramBot` |
| 多实例管理 | `gateway/bot_manager.py`、`gateway/instance.py` | `ChannelBotManager`、`ChannelInstance` |
| 出站通知 | `notifier/notifier.py`、`channel_registry.py`、`renderer.py` | `Notifier`、`ChannelRegistry`、`render_*` |
| 出站渠道 | `notifier/channels/*.py` | `NotificationChannel`、`FeishuChannel`、`DingTalkChannel`、`EmailChannel` |
| CLI | `cli/main.py`、`cli/client.py`、`cli/commands/*.py` | typer app + httpx 客户端 |
| 前端 | `web/src/router/AppRoutes.tsx`、`web/src/pages/*`、`web/src/api/*` | React 路由 + 页面 + api 层 |

## 2. 路由实现

- `gateway/api.py` 只保留 `/ws` 和 `/consultations`；`app.py` 明确 include 所有领域
  router。Router 数量和源码行号不是稳定契约。
- 处理函数从 `request.app.state.*` 取依赖（`storage` / `notifier` / `universe_manager` / `executor` / `config_manager` 等），无 DI 容器；各域 router 沿用同一模式。
- 位面路由在 `universes_api.py`；下划线前缀路由（`/universes/_diff`、`/universes/_status`）仍声明在 `/universes/{id}` 之前避免被吞。
- WebSocket：`gateway/api.py:25` `websocket_endpoint` → `notifier.register_ws/unregister_ws`。

任务资源在进入 handler 后还会经过 `authz.py`/`ownership.py`：普通 principal 按
`Edict.submitter` 过滤，跨 owner 返回 404；admin 可跨 owner。SystemAudit、全局审计/
network、Worker、memory/cost/config 等管理面由 auth middleware 要求 admin。

完整路由族见 [../../design/interfaces/gateway.md](../../design/interfaces/gateway.md)。

## 3. 出站通知实现

- `Notifier`（`notifier/notifier.py`）：WebSocket 连接/去抖；terminal external
  notification 通过 `InternalDeliveryOutbox` 逐渠道发送。
- V24 的 `accepted_channels_json` 在每个 adapter 成功后立即 CAS 持久化；retry 跳过已
  accepted 渠道。全部成功才 delivered，超期/超次数 dead-letter。
- `WebSocketStreamCallback`：`on_delta` / `on_tool_call_start` / `on_tool_call_end` 推 `stream.*`。
- `ChannelRegistry`：`register(channel, rpm)` + 每渠道滑动窗口限流（默认 10/分钟）+ `send_all` / `send_to`。
- 渲染器 `renderer.py`：`render_status`（dict）、`render_feishu` / `render_dingtalk` / `render_email`（str）。

## 4. 装配（`app.py` lifespan）

```text
ChannelRegistry()  →  按 settings 注册 FeishuChannel(webhook 旧模式) / DingTalkChannel / EmailChannel
Notifier(storage, channel_registry)  →  app.state.notifier
ChannelBotManager(...)  →  start_all()（失败不影响 web）  →  app.state.bot_manager
  app.state.feishu_bot = bot_manager.get("feishu-default")
  app.state.telegram_bot = bot_manager.get("telegram-default")
include_router(gateway_router, prefix="/api") + credentials/hongluisi/tongzheng
关停：bot_manager.stop_all()
```

互斥：配了 `TIANSHU_FEISHU_APP_ID` 走 app bot 模式（`FeishuOutbound` 在 `FeishuBot.start()` 内直接订阅 EventBus，不经 ChannelRegistry）；旧 incoming webhook URL 完全跳过。

## 5. IM bot 实现要点

| 关注点 | 落点 |
|---|---|
| 连接 | `feishu/connection.py`：`WebSocketConnection` / `WebhookConnection`（webhook 路由 `attach_webhook_router` 挂 FastAPI） |
| 双模式 | `feishu/mode_router.py` `ModeRouter` 读 `SessionAnchor` 分发 `AssistantBranch` / `EdictBranch` |
| 平台无关核心 | `core/edict_bridge.py` `EdictBridge`、`core/persona_renderer.py`、`approval_commands.py`、`markdown_compat.py`（telegram 直接 import 复用） |
| 审批 | `feishu/approval_card.py` + `card_action_dispatcher.py`；telegram `approval_kb.py`（inline keyboard） |
| 多实例隔离 | `instance_id` 贯穿 anchor / bridge / outbound / approval；进程锁 `~/.tianshu/feishu_app_lock.{app_id}` |
| 热加载 | `FeishuBot.reload`：重建 connection，切 persona renderer，不重订阅 EventBus |

## 6. CLI 实现要点

- `cli/main.py`：`typer.Typer` + `add_typer` 挂 11 个子应用 + `health`/`watch` 顶层命令。
- `cli/client.py`：httpx 同步，超时 360s，`TIANSHU_API_URL` 缺省 localhost:8000；连接/状态错误友好退出。
- 命令文件（`cli/commands/*.py`）各自 `app = typer.Typer()` + rich 渲染，支持 `--format table|json`。

## 7. 前端实现要点

- `web/src/router/AppRoutes.tsx`：页面全部 lazy，route error boundary 区分 chunk/render
  错误，wildcard 使用真正的三语 404。
- `web/src/navigation/departments.tsx`：默认五个一级目的地；Keqing、Evolution、
  Universes、Evals、Session Rules 仍可达但隐藏为实验路由。
- `web/src/api/client.ts`：axios 实例 baseURL `/api`，拦截器统一处理 `ApiResponse.error`。
- 实时：`hooks/useWebSocket.ts` 连 `/api/ws`；`useWsQueryInvalidation.ts` 用 WS 事件失效 react-query 缓存；`useWsPolicyToasts.ts` 弹策略 toast。
- `SchedulerPage` 支持 edit/pause/resume/run-now/history；`EdictForm` 默认折叠专家参数并禁止
  周期长任务组合。

## 8. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 加 HTTP 路由 | 在对应域 `*_api.py`（或新建一个）加 `@xxx_router.<verb>`，从 `request.app.state` 取依赖，`app.py` 里 `include_router` |
| 加出站渠道 | 实现 `NotificationChannel`（`name` + `async send`），在 `app.py` `channel_registry.register(...)` |
| 加 IM 平台 | 镜像 `gateway/telegram/` 结构，复用 `EdictBridge`/`PersonaRenderer`/审批解析，重写平台连接/出站层 |
| 加 CLI 命令族 | 新建 `cli/commands/<x>.py` 的 `typer.Typer()`，在 `main.py` `add_typer` |
| 加前端页面 | `web/src/pages/` 加组件 + `router/AppRoutes.tsx` 加 Route；只有稳定用户入口才改 `navigation/departments.tsx` |
