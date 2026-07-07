# Channels：通知渠道与 IM 接入

> 天枢的「出站通知」与「双向 IM 接入」两件事：Notifier + ChannelRegistry 负责把执行结果推出去；`gateway/feishu/`、`gateway/telegram/` 负责让用户从 IM 里下达诏令、做审批。本篇讲契约与边界，操作步骤见 `docs/ops/`。

## 1. 两条通路

| 通路 | 方向 | 组件 |
|---|---|---|
| 出站通知 | 平台 → 用户 | `Notifier`、`ChannelRegistry`、`channels/*`（webhook/邮件） |
| 双向 IM bot | 用户 ↔ 平台 | `gateway/feishu/FeishuBot`、`gateway/telegram/`（app bot，可下旨/审批） |
| 实时 Web 流 | 平台 → 浏览器 | `Notifier` 的 WebSocket 广播（见 [gateway.md](./gateway.md) §6） |

## 2. Notifier（广播 + 派发中枢）

`notifier/notifier.py` 的 `Notifier`：

| 能力 | 说明 |
|---|---|
| WebSocket 广播 | `register_ws` / `broadcast_ws`，死连接自动剔除 |
| 事件处理 | 订阅 `audit.completed`（urgent 跳去抖，否则 0.5s debounce）、`execution.failed`、`outer_loop.*`（实时不去抖） |
| 外部派发 | `_dispatch_external` 按 `edict.dispatch.channels` 渲染并发到对应渠道 |
| webhook | `send_webhook` 直发 POST |
| 流式回推 | `WebSocketStreamCallback`：Agent delta / tool_start / tool_end 推前端 |

渲染器（`renderer.py`）按渠道选择：`render_feishu` / `render_dingtalk` / `render_email` / `render_status`。

## 3. ChannelRegistry（出站渠道注册表）

`notifier/channel_registry.py` 的 `ChannelRegistry`：

| 能力 | 说明 |
|---|---|
| 注册 | `register(channel, rpm=10)`，按渠道名登记 |
| 限流 | 每渠道默认 10 条/分钟滑动窗口，超限 skip |
| 派发 | `send_all` / `send_to(names, ...)` 返回 `{channel: success}` |

渠道接口 `NotificationChannel`（`channels/base.py`，ABC）：`name` 属性 + `async send(message, rendered) -> bool` + `close`。

| 渠道实现 | name | 构造参数 |
|---|---|---|
| `FeishuChannel` | `feishu` | `webhook_url`（**已废弃**：新部署用 app bot 模式，仅 app_id 未配时生效） |
| `DingTalkChannel` | `dingtalk` | `webhook_url`, `secret` |
| `EmailChannel` | `email` | `smtp_host/port`, `username/password`, `from_addr`, `to_addrs`, `use_tls` |

> WebSocket 不是注册到 ChannelRegistry 的 channel，而是 Notifier 直接管理的连接集合。

## 4. 飞书接入（`gateway/feishu/`）

`FeishuBot` 是门面，协调 connection / dispatcher / outbound / 双模式分支。设计文档：`docs/superpowers/specs/2026-04-28-feishu-bot-design.md`、`2026-04-29-feishu-assistant-mode-design.md`。

| 关注点 | 设计 |
|---|---|
| 连接模式 | `WebSocketConnection` 或 `WebhookConnection`（按 `connection_mode`），webhook 模式路由挂到 FastAPI |
| 双模式（ModeRouter） | 读 `SessionAnchor`：`current_edict_id` 为空 → 助手模式（AssistantBranch，`/menu` `/list` `/budget` + 自然语言）；非空 → 敕令模式（EdictBranch，续接绑定的 edict） |
| 平台无关核心 | `EdictBridge`（下旨/续接）、`PersonaRenderer`、`approval_commands` 解析、`markdown_compat` 复用 |
| 审批 | `ApprovalCardHandler`（卡片按钮）+ `ApprovalCommandHandler`（命令），共享 `ApprovalManager` |
| 进程锁 | 启动占 `~/.tianshu/feishu_app_lock.{app_id}`，避免双开同一 app |
| 紧急逃生 | `disable_assistant_mode=True` 走 v1 legacy（无 ModeRouter，仅 `/new` `/status` `/cancel` `/help`） |
| 热加载 | `reload(new_settings)` 重建 connection、切 persona renderer，不重订阅 EventBus（避免重复回调） |

## 5. Telegram 接入（`gateway/telegram/`）

镜像飞书结构（`docs/superpowers/plans/2026-05-29-telegram-bot.md`）：复用平台无关核心（EdictBridge / PersonaRenderer / 审批解析 / ApprovalManager / Executor / Storage），重写平台层（python-telegram-bot 连接、出站、inline keyboard 审批/卡片）。同样有 ModeRouter 双模式、SessionAnchor、edict/assistant 分支。

## 6. 多 bot 实例（ChannelBotManager）

`gateway/bot_manager.py` 的 `ChannelBotManager`：每个渠道可跑 N 个 bot 实例。

| 关注点 | 设计 |
|---|---|
| 实例来源 | DB `channel_instances`（优先）/ 旧单配置 `channel_configs` / env 三级 |
| 旧配置迁移 | 旧单配置首次启动迁成默认实例（写回 `channel_instances`，供 Web UI 可见） |
| 实例身份 | `instance_id` 贯穿所有协作者，实现多实例隔离（敕令路由 / 会话锚 / 审批卡片）；reload 不改身份 |
| 生命周期 | `start_instance` / `stop_instance` / `reload_instance` / `start_all` / `stop_all` / `status` |

## 7. 边界

- 渠道发送是 best-effort：单渠道失败被 catch + log，不阻塞主链路。
- 限流是每渠道独立滑动窗口；urgent edict 在 Notifier 层跳过 WebSocket 去抖，但渠道层限流仍生效。
- IM bot 的 allowlist 为空时会响应任何可达用户（生产须配 `TIANSHU_FEISHU_ALLOWED_USERS` 或通政司页填「允许用户」）。

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)
