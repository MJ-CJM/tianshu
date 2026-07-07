# 飞书机器人接入设计

- **日期**：2026-04-28
- **作者**：mj-cjm
- **状态**：Draft（待用户复核）
- **方案路线**：薄壳子包 + 复用 hermes 协议层（参考 `/Users/chenjiamin/ai-example/hermes-agent`）
- **预计代码规模**：~1300 行（vs. hermes 同等功能 ~5000 行，因裁剪掉 28 个其它平台的兼容代码与富媒体细节）

---

## 1. 背景与目标

### 1.1 现状

- tianshu 现有 `src/tianshu/notifier/channels/feishu.py`（52 行）仅是**单向出站** —— 把邸报/审计结果通过 incoming webhook URL 推送到飞书群。
- 没有飞书入站入口：用户在飞书里说话、点按钮，tianshu 完全不知道。
- 已有的事件驱动架构（EventBus / Storage / ApprovalManager / Notifier / Executor）足够承载入站事件，无需重构。

### 1.2 目标

让飞书机器人成为 tianshu 的**第二入口**和**第二审批通道**：

1. **入口型（A）**：用户在飞书私聊或群 @ 机器人 → 创建/续接敕令 → 走完整六部流程 → 结果回到飞书。
2. **审批型（B）**：审批弹窗（`shell_exec` / `plan.review` / 危险工具等）通过飞书卡片送达，可直接在飞书点 Allow/Deny；与 web 弹窗**双通道并行**，任一侧响应即生效，另一侧自动作废。
3. **接入方式与 hermes 对齐**：环境变量、SDK（lark-oapi）、双连接模式（WebSocket / Webhook）、卡片协议、安全机制全部沿用 hermes 已验证的方式，方便用户复用 hermes 文档作为参考。

### 1.3 非目标（v1 不做）

- 多用户身份映射（v1 = 单人，emperor 固定为 submitter）
- 媒体上下行（图片 / 文件 / 语音）
- 文档评论智能回复（`drive.notice.comment_add_v1`）
- 多平台抽象（钉钉 / Slack / 企业微信）—— YAGNI
- 复用 hermes `BasePlatformAdapter`（2870 行 base）的全部跨平台兼容代码

---

## 2. 关键决策（已与用户对齐）

| # | 决策 | 取值 |
|---|------|------|
| 1 | 角色 | A+B：飞书既是入口，也是审批通道 |
| 2 | 使用范围 | 单人专用（emperor 本人）；私聊全触发；群聊仅 @ 机器人触发；allowlist 只放本人 open_id |
| 3 | 敕令粒度 | 一会话一敕令：默认续接同一会话锚定的 Edict；只有显式 `/new <goal>` 才另开新敕令；**不按时间自动切** |
| 4 | 审批路由 | 双通道并行（web + 飞书），任一侧响应即生效，另一侧卡片自动作废 |
| X | anchor 指向的 Edict 已结案后，下一条消息 | **自动新建并更新 anchor**（无感） |
| Y | v1 媒体支持 | **不支持**（仅文本 + 卡片） |
| Z | 命令前缀 | `/`（与 hermes 一致） |
| W | `feishu_home_channel` 用途 | cron 触发结果 + 无源敕令（web 提交）的审批卡片；普通敕令事件回原 chat |
| V | 默认连接模式 | **WebSocket**（lark-oapi SDK 反向长连，无需公网） |

---

## 3. 整体架构

```
                       飞书云
                      ┌──────┐
                      │ Lark │
                      └──┬───┘
                         │ WS (默认) / Webhook
                         ▼
       ┌────────────────────────────────────────────────────┐
       │  src/tianshu/gateway/feishu/   ← 新增子包          │
       │                                                     │
       │  ┌───────────┐    ┌────────────┐    ┌────────────┐ │
       │  │connection │ →  │ dispatcher │ →  │edict_bridge│ │ → POST /edicts (内部 fn)
       │  │ (WS/HTTP) │    │ (命令解析) │    └────────────┘ │ → POST /edicts/{id}/follow-up
       │  └───────────┘    └─────┬──────┘                   │
       │                         └─→ approval_card (入站)   │ → ApprovalManager.resolve(...)
       │                                                     │
       │  ┌────────────────┐                                 │
       │  │   outbound     │  ← EventBus 订阅                │ ← memorial.completed
       │  │  (回写飞书)    │  ← Approval.requested            │ ← edict.failed
       │  └────────────────┘                                 │ ← approval.requested → 卡片下行
       └────────────────────────────────────────────────────┘
                │                          ▲
                ▼                          │
       ┌──────────────┐    ┌──────────────────────────────┐
       │  Storage     │    │ EventBus / Notifier          │
       │ (会话锚表)   │    │ ApprovalManager / Executor   │
       └──────────────┘    └──────────────────────────────┘
```

### 3.1 启动流程（在 `src/tianshu/app.py` 的 lifespan 里追加）

1. 读取 `settings.feishu_*`。若 `feishu_app_id` 为空 → 跳过（保持向后兼容；现有部署无变化）。
2. 构造 `FeishuBot(...)`，注入 `Storage / EventBus / ApprovalManager / Notifier / ChannelRegistry`。
3. 根据 `feishu_connection_mode`：
   - `websocket`：后台 `asyncio.Task` 跑 lark-oapi 长连客户端
   - `webhook`：`app.include_router(feishu_router, prefix="")` 暴露 `feishu_webhook_path`
4. 订阅 `EventBus`：`memorial.completed / execution.failed / approval.requested / approval.resolved / outer_loop.* / cost.budget_exceeded`。
5. 注册 `FeishuOutboundChannel` 到 `ChannelRegistry`，替代旧 `FeishuChannel`（基于 incoming webhook URL）。
6. 创建/迁移 SQLite 表 `feishu_session_anchor`、`feishu_seen_messages`、`feishu_home_channel`。
7. 占进程互斥锁 `~/.tianshu/feishu_app_lock.{app_id}`，避免双开。

### 3.2 模块文件清单与行数预算

```
src/tianshu/gateway/feishu/
├── __init__.py            ~30 行   (FeishuBot 工厂 + 公共导出)
├── settings.py            ~40 行   (Pydantic settings 子集 + 校验)
├── connection.py         ~250 行   (WebSocket + Webhook 双模式)
├── dispatcher.py         ~200 行   (流水线 + 命令解析)
├── session_anchor.py      ~80 行   (会话锚 SQLite 读写)
├── edict_bridge.py       ~150 行   (FeishuMessage → Edict 决策)
├── approval_card.py      ~200 行   (出站卡片 + 入站按钮)
├── outbound.py           ~250 行   (事件 → 飞书消息映射)
├── security.py           ~120 行   (signature/token/allowlist/dedup)
└── tests/                          (镜像源码结构)
```

每个文件保持 < 400 行，符合 tianshu `coding-style.md` 的 "many small files" 原则。

---

## 4. 配置层（`settings.py`）

新增到 `src/tianshu/config.py`，env prefix `TIANSHU_`，与 hermes 同名（去掉 `FEISHU_` 前缀后挂在 tianshu 命名空间下）：

```python
# Feishu Bot —— inbound + outbound
feishu_app_id: str = ""                       # 空 → 不启用机器人
feishu_app_secret: str = ""
feishu_domain: str = "feishu"                 # feishu | lark
feishu_connection_mode: str = "websocket"     # websocket | webhook
feishu_allowed_users: str = ""                # 逗号分隔 open_id；单人模式只放本人
feishu_home_channel: str = ""                 # cron 结果 / 无源敕令审批的兜底 chat_id
feishu_encrypt_key: str = ""                  # webhook 模式签名密钥
feishu_verification_token: str = ""           # webhook 模式 token 校验
feishu_bot_open_id: str = ""                  # 群 @ 检测
feishu_bot_name: str = ""                     # 群 @ 检测兜底
feishu_webhook_path: str = "/feishu/webhook"
feishu_ws_reconnect_interval: int = 120       # WS 断线重连间隔（秒）
feishu_text_batch_delay: float = 0.6          # 文本合并静默期（与 hermes 默认一致）
feishu_dedup_cache_size: int = 2048

# 现有 feishu_webhook 字段保留以兼容旧部署，标记 deprecated；启动时若同时配置
# feishu_app_id 与 feishu_webhook，优先使用 app bot 模式，旧 webhook URL 忽略。
```

**最小可用配置**（单人 WS 模式）：

```bash
TIANSHU_FEISHU_APP_ID=cli_xxx
TIANSHU_FEISHU_APP_SECRET=secret_xxx
TIANSHU_FEISHU_ALLOWED_USERS=ou_自己的_open_id
```

---

## 5. 连接层（`connection.py`）

```python
class FeishuConnection(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    inbound_queue: asyncio.Queue  # 共享出站给 dispatcher

class WebSocketConnection:
    """lark-oapi SDK 反向长连。SDK 内部跑独立线程，事件 dispatch 回主 loop。"""
    # 重连：SDK 自动；ws_reconnect_interval=120s；连续失败 5 次发 feishu.connection_lost 事件
    # 心跳：SDK 内置 ping
    # 不需要公网，适合本地开发与私有部署

class WebhookConnection:
    """挂在 FastAPI router 上。POST {feishu_webhook_path}。"""
    # 1. 校验 signature (SHA256(timestamp + nonce + encrypt_key + body))
    # 2. 校验 verification_token
    # 3. url_verification challenge 自动响应
    # 4. 限流：60s 滑窗 / 120 req per (app_id, path, IP)
    # 5. Body 1MB / 30s 读超时
    # 6. 入队后立即 200 OK
```

两种连接共用一个 `inbound_queue: asyncio.Queue`。下游 dispatcher 完全不感知连接模式，改起来零成本。

---

## 6. 入站调度（`dispatcher.py` + `security.py`）

入站流水线：

```
raw_event
  → security.verify_signature      (webhook only)
  → security.verify_token          (webhook only)
  → security.allowlist_check       (sender open_id ∈ feishu_allowed_users)
  → security.dedup_check           (message_id 24h TTL，持久化到 feishu_seen_messages 表)
  → group_gate                     (群消息须 @bot 才放行；私聊直通)
  → batcher                        (同 sender 0.6s 静默期合并文本，hermes 默认参数)
  → command_parser                 (提取 / 命令)
  → router:
      ├─ /new <goal>            → edict_bridge.create_new(chat_id, sender, goal)
      ├─ /status [edict_id]     → outbound.send_status(chat_id, edict_id)
      ├─ /cancel [edict_id]     → executor.cancel(edict_id)
      ├─ /set-home              → 设当前 chat 为 feishu_home_channel（持久化覆盖 env）
      ├─ /help                  → 返回命令列表
      ├─ card.action.trigger    → approval_card.handle_button_click(...)
      └─ <plain text>           → edict_bridge.continue_or_create(chat_id, sender, text)
```

**关键性质：**

- **未授权直接静默丢弃**（不回复，避免被探测）。
- **Per-chat 串行**：同一 `chat_id` 用 `asyncio.Lock` 串行处理，保证对话连贯；不同 chat 并发。
- **Dedup 持久化**：`feishu_seen_messages` 表，2048 LRU；进程重启不会重复处理。

---

## 7. 敕令桥接（`edict_bridge.py` + `session_anchor.py`）

### 7.1 SessionAnchor 数据模型

```sql
CREATE TABLE feishu_session_anchor (
    chat_id          TEXT PRIMARY KEY,    -- 飞书 chat_id（私聊或群）
    current_edict_id TEXT,                -- 当前会话绑定的 Edict
    updated_at       TIMESTAMP NOT NULL
);
```

### 7.2 消息 → Edict 决策（无 `/new` 永远续接）

```python
async def continue_or_create(chat_id: str, sender: str, text: str) -> str:
    anchor = storage.get_feishu_anchor(chat_id)
    if anchor and anchor.current_edict_id:
        edict = storage.get_edict(anchor.current_edict_id)
        if edict and edict.status not in CLOSED_STATES:
            # 续接：复用现有 follow_up API（携带 runtime/acceptance override 能力）
            await follow_up(edict.id, instruction=text)
            return edict.id
        # else: anchor 指向已结案 Edict → 子决策 X1：自动新建（无感）
    return await create_new(chat_id, sender, text)
```

### 7.3 `/new <goal>` 命令

```python
async def create_new(chat_id, sender, goal: str) -> str:
    edict = Edict(
        title=goal[:20] + ("…" if len(goal) > 20 else ""),
        goal=goal,
        submitter="emperor",                            # v1 单人固定
        metadata={"source": "feishu", "chat_id": chat_id, "feishu_user": sender},
    )
    storage.save_edict(edict)
    storage.set_feishu_anchor(chat_id, edict.id)        # 更新会话锚
    event_bus.fire(make_event("edict.submitted",
        edict_id=edict.id, producer="feishu_bot", payload={"goal": goal}))
    return edict.id
```

### 7.4 已结案敕令的"自动新建"行为（子决策 X1）

当 `anchor.current_edict_id` 指向的 Edict 状态 ∈ `{COMPLETED, FAILED, CANCELLED}`：

1. 不报错、不向用户提示
2. 把当前消息文本作为 goal 创建新 Edict
3. 更新 anchor → 新 Edict
4. 飞书侧仅回 "✅ 已收到：{title}（敕令 #xxx）" —— 用户体验上像是"自然延续"

---

## 8. 审批桥接（`approval_card.py`）—— 双通道并行

### 8.1 出站（审批卡片下发）

订阅 `approval.requested` 事件。每条审批：

```python
{
  "approval_id": "ap_xxx",
  "edict_id": "ed_xxx",
  "kind": "shell_exec" | "plan.review" | "tool.dangerous" | ...,
  "summary": "执行 git push origin main",
  "details": {...}
}
```

**送达逻辑：**

1. 反查 `Edict.metadata.chat_id`（若为飞书来源）→ 直接送该 chat
2. 否则（web 来源）→ 送 `feishu_home_channel`
3. 若两者都为空 → 不送（仅 web 弹窗）

**卡片结构**（与 hermes 一致）：

```json
{
  "msg_type": "interactive",
  "card": {
    "header": {"title": {"tag": "plain_text", "content": "🛡️ 审批：shell_exec"}},
    "elements": [
      {"tag": "markdown", "content": "**操作**\n```\ngit push origin main\n```\n敕令 #ed_xxx"},
      {"tag": "action", "actions": [
        {"tag": "button", "text": {"tag": "plain_text", "content": "✅ 单次允许"},
         "type": "primary", "value": {"approval_id": "ap_xxx", "choice": "allow_once"}},
        {"tag": "button", "text": {"tag": "plain_text", "content": "🔄 本次会话允许"},
         "value": {"approval_id": "ap_xxx", "choice": "allow_session"}},
        {"tag": "button", "text": {"tag": "plain_text", "content": "♾️ 总是允许"},
         "value": {"approval_id": "ap_xxx", "choice": "allow_always"}},
        {"tag": "button", "text": {"tag": "plain_text", "content": "❌ 拒绝"},
         "type": "danger", "value": {"approval_id": "ap_xxx", "choice": "deny"}}
      ]}
    ]
  }
}
```

**记录卡片消息 ID**（用于后续作废刷新）：写到内存表 `pending_cards: dict[approval_id, (chat_id, message_id)]`。

### 8.2 入站（按钮点击）

`card.action.trigger` 事件 → 解析 `value.approval_id, value.choice` →

```python
await approval_manager.resolve(
    approval_id=value["approval_id"],
    choice=value["choice"],
    source="feishu",
    actor=event.sender_open_id,
)
```

`ApprovalManager.resolve()` 的幂等性已存在（见 `executor/approvals.py`），第二次同 `approval_id` 调用直接被忽略，不会脏写。

### 8.3 双通道竞态处理

**关键：** ApprovalManager 在 `resolve()` 成功后发射 `approval.resolved` 事件，已经存在。我们只需：

1. **飞书侧响应后**：
   - 把卡片就地刷新为"✅ 已批准（来自 feishu）by xxx"，按钮置灰（用 `update_message` API）
   - `approval.resolved` 事件由 EventBus 推到 web WebSocket → 前端 `useWsPolicyToasts` hook 已经监听 → 弹窗自动消失（**需要在前端 hook 加一个分支**：收到 `approval.resolved` 时关闭对应 toast）

2. **web 侧响应后**：
   - 同样发射 `approval.resolved` 事件
   - `outbound` 监听 → 找到 `pending_cards[approval_id]` → 调用 `update_message` 把卡片刷新为"✅ 已在 web 批准 by xxx"

**卡片 dedup**：15 分钟内同一 `(approval_id, button)` 重复点击直接忽略（hermes 同款，防止网络重传 / 用户多点）。

---

## 9. 出站（`outbound.py`）

### 9.1 事件 → 飞书消息映射

| EventBus 事件 | 触发条件 | 飞书消息 |
|--------------|---------|---------|
| `edict.submitted` | 飞书提交（`metadata.source == "feishu"`） | "✅ 已收到：{title}（敕令 #xxx）" |
| `plan.ready` | 计划生成（`plan_review=true`） | 卡片：审批计划 + 通过/驳回按钮 |
| `outer_loop.iteration_started` | 长任务持续模式新轮 | "🔄 第 N 轮迭代中..." |
| `memorial.completed` | 敕令完成 | 富文本：结果摘要（前 500 字）+ 链接到 web 详情页 |
| `execution.failed` | 执行失败 | "❌ 失败：{原因摘要}" |
| `approval.requested` | 见 §8 | 卡片下发 |
| `approval.resolved` | 双通道作废另一侧 | 卡片刷新（见 §8.3） |
| `cost.budget_exceeded` | 预算熔断 | "⚠️ 预算超支，敕令已暂停" |

### 9.2 路由规则

- 反查 `Edict.metadata.chat_id` → 回原 chat
- 找不到（web 提交）→ `feishu_home_channel`
- `feishu_home_channel` 也没设 → 跳过出站（仅 web 显示）

### 9.3 Markdown → Feishu post 自动转换

含 markdown 标记（标题、列表、代码块、链接、加粗）的文本 → 用 `post` 消息类型发送，启用富文本渲染。
若 Feishu API 拒绝 post（罕见，某些不支持的 md 构造）→ 自动降级 plain text + strip markdown。两段降级与 hermes 一致。

### 9.4 升级旧 `FeishuChannel`

- 旧 `notifier/channels/feishu.py`（incoming webhook URL）：保留文件，标记 deprecated。
- 新增 `FeishuOutboundChannel`（用 lark-oapi app bot send_message API）。
- 启动时优先注册 `FeishuOutboundChannel`（若 `feishu_app_id` 已配）；旧 `feishu_webhook` URL 仅在没配 app bot 时启用。
- `Notifier._dispatch_external` 不变（还是按 channel 名查 ChannelRegistry，行为透明切换）。

---

## 10. 错误处理与限流

| 场景 | 处理 |
|------|-----|
| WS 断线 | SDK 自动重连，间隔 `ws_reconnect_interval=120s`；连续 5 次失败发 `feishu.connection_lost` 事件让 Notifier 走其它通道告警 |
| WS 同 app_id 冲突 | 启动时 `~/.tianshu/feishu_app_lock.{app_id}` 占进程锁；二次启动直接 fatal 退出 |
| Webhook 限流 | aiohttp middleware：60s 滑窗 / 120 req per (app_id, path, IP) |
| Webhook body 异常 | 1MB 上限 / 30s 读超时 / 仅接受 `application/json` |
| Allowlist 失效 | 静默丢弃（不回复，避免被探测） |
| Edict 创建失败 | 飞书侧回 "❌ 创建敕令失败：{原因}"；不更新 anchor |
| 卡片 update_message 失败 | 重试 3 次（指数回退）；最终失败仅记日志，幂等性由 ApprovalManager 保证状态正确 |
| send_message 失败 | 重试 3 次；最终失败发 `feishu.send_failed` 事件 + 日志 |

---

## 11. 安全

- **TIANSHU_FEISHU_APP_SECRET / VERIFICATION_TOKEN / ENCRYPT_KEY** 仅从环境变量读取，不入库，不日志输出。
- **Allowlist 强制**：v1 单人模式必须配 `feishu_allowed_users`，未配置时启动 fatal 报错（避免误开放）。
- **Webhook 签名校验**：`SHA256(timestamp + nonce + encrypt_key + body)`，时间常量比较防侧信道。
- **WS 模式**：SDK 内置签名校验，无需手动。
- **Dedup 表持久化**：`feishu_seen_messages` SQLite，进程重启亦防重复。
- **Persona / submitter 隔离**：飞书来源敕令 `submitter="emperor"` 固定，`metadata.feishu_user` 仅作审计追溯。

---

## 12. 测试策略

按 tianshu `testing.md` 80%+ 覆盖：

| 层级 | 内容 | 工具 |
|------|------|------|
| 单元 | command_parser、session_anchor 读写、signature/token verify、卡片 payload 构造、消息 → Edict 决策（含已结案分支）、双通道作废 | pytest + pytest-asyncio |
| 集成 | inbound queue → dispatcher → edict_bridge → 真 SQLite Storage → 验证 Edict 落库 + anchor 更新 | pytest + tmpdir SQLite |
| 协议 | 用 hermes 的 `tests/gateway/test_feishu.py`、`test_feishu_approval_buttons.py` 作参考用例 | pytest |
| 端到端 | 启动本地 webhook server，用 curl 模拟飞书 url_verification / message / card.action 事件，断言 Edict + 审批副作用 | pytest |
| WS 模式 | SDK mock，无法离线 e2e；只测连接生命周期与重连逻辑 | pytest + AsyncMock |

新增测试文件位置（镜像源码）：

```
tests/gateway/feishu/
├── test_settings.py
├── test_security.py            (signature/token/allowlist/dedup)
├── test_dispatcher.py          (流水线/命令/路由)
├── test_session_anchor.py
├── test_edict_bridge.py        (含 X1 自动新建)
├── test_approval_card.py       (双通道作废竞态)
├── test_outbound.py            (事件映射 + markdown 转 post + fallback)
├── test_connection_webhook.py  (FastAPI TestClient)
└── test_e2e_webhook.py
```

**一致性测试**：用 hermes 已有测试用例的飞书事件 fixture（JSON）作为输入，验证 tianshu dispatcher 处理一致。

---

## 13. 部署 / 运维

### 13.1 启动检查

启动时若 `feishu_app_id` 已配但缺以下任一，fatal 退出：

- `feishu_app_secret`
- `feishu_allowed_users`（v1 强制要求，避免误开放）
- WS 模式：lark-oapi、websockets 依赖未安装
- Webhook 模式：aiohttp 依赖未安装；公网回调地址需自行配置（NGINX 转发等不在本设计范围）

### 13.2 日志

- 入站事件：`logger.info("[feishu/inbound] chat=xxx sender=xxx kind=text msg_id=xxx")`
- 出站消息：`logger.info("[feishu/outbound] chat=xxx kind=text len=xxx")`
- 卡片状态变更：`logger.info("[feishu/approval] approval_id=xxx source=feishu choice=allow_once")`
- 错误：堆栈 + 上下文。
- 不打印 token / secret / app_secret。

### 13.3 观测点

- `feishu.inbound.received{kind}` 计数
- `feishu.outbound.sent{kind}` 计数
- `feishu.approval.resolved{source}` 计数
- `feishu.connection.state{state=connected|reconnecting|failed}` gauge
- `feishu.dedup.hit` 计数（重复消息）

---

## 14. 实施顺序建议（落到 plan 的提示）

writing-plans 阶段会拆 Step。这里建议的演进顺序：

1. **配置层 + 启动壳**：settings、FeishuBot 工厂、lifespan 接入，empty 实现，确保不影响现有部署
2. **Webhook 模式 + 安全**：security.py + connection.py (Webhook) + dispatcher 最简骨架（只回 echo），先打通端到端
   - 注：虽然决策表 V 选定 **WebSocket 为运行时默认**，但实施先做 Webhook —— 因为 Webhook 可用 FastAPI TestClient 与 curl 离线 e2e 测试，开发反馈环更短；WS 模式涉及 SDK 后台线程难以离线 mock。两种模式共享下游流水线，先做哪个不影响最终行为。
3. **会话锚 + 入口型（A）**：edict_bridge + session_anchor + `/new` 命令
4. **出站基础**：outbound.py 订阅 `memorial.completed / execution.failed`，回基础文本
5. **审批型（B）**：approval_card 双通道（含 web 弹窗自动消失的前端 hook 改动）
6. **WebSocket 模式**：connection.py (WS) + lark-oapi SDK 集成 —— 切换到 V 决策的运行时默认
7. **优化**：批处理 / dedup / 限流 / 占锁 / 重连 / `/help`、`/status`、`/cancel`、`/set-home`
8. **测试补齐到 80%+**

---

## 15. 风险与回退

| 风险 | 缓解 |
|------|-----|
| lark-oapi SDK 升级 break 兼容 | 锁版本 + 隔离在 connection.py，更新有回归测试 |
| WS 长连占用进程资源 / 互斥失效 | 进程锁 + 启动检查；若失效仅影响重复消息（dedup 兜底） |
| 双通道作废刷新失败 | ApprovalManager 幂等性兜底，最坏情况是飞书卡片显示陈旧（按钮再点也无效），不会脏写 |
| 飞书 API 限流 | 出站重试 + 降级（post → text）；最坏情况 Notifier 层日志告警 |
| 单人 allowlist 配错（如填了别人的 open_id） | 启动检查 + 文档强调；首次连接成功后 logger 打出"bot now serving open_id=xxx"提示自检 |
| 旧 `feishu_webhook`（incoming URL）部署回退 | 新旧并存，新模块未启用时旧通道继续工作；启用新模块时旧 URL 自动忽略 |

---

## 16. 与 hermes 的对照表（"一致性"声明）

| 维度 | hermes | tianshu (本设计) | 一致性 |
|------|--------|----------------|--------|
| SDK | lark-oapi | lark-oapi | ✅ 完全一致 |
| 连接模式 | WebSocket / Webhook | WebSocket / Webhook | ✅ 完全一致 |
| 默认连接 | WebSocket | WebSocket | ✅ |
| 环境变量名 | `FEISHU_*` | `TIANSHU_FEISHU_*`（仅前缀差） | ✅ 语义一致 |
| 卡片协议 | interactive card + button value | 同 | ✅ |
| 群 @ 网关 | 是 | 是 | ✅ |
| Allowlist | 是 | 是 | ✅ |
| 签名/token 校验 | 是 | 是 | ✅ |
| Dedup 持久化 | JSON 文件 | SQLite 表 | 🟡 行为一致，存储位置不同 |
| Per-chat 串行 | 是 | 是 | ✅ |
| 文本批处理 | 0.6s 静默 | 0.6s 静默 | ✅ |
| BasePlatformAdapter | 是 | **否** | ❌ 故意不引入（YAGNI） |
| 媒体上下行 | 完整支持 | **v1 不做** | ❌ 故意裁剪 |
| 文档评论智能回复 | 是 | **不做** | ❌ 故意裁剪 |

**结论**：协议层、配置层、安全层与 hermes 完全对齐；裁掉的是 tianshu 不需要的多平台抽象与富媒体能力。

---

## 17. 待 writing-plans 阶段细化

- 每个文件的具体类/函数签名（接口契约）
- SQL 迁移脚本（feishu_session_anchor / feishu_seen_messages / feishu_home_channel 表）
- 前端 `useWsPolicyToasts` 改动细节（双通道作废自动消失）
- 测试用例清单（含 hermes fixture 复用列表）
- 文档：`docs/ops/feishu-setup.md` 用户配置指南
