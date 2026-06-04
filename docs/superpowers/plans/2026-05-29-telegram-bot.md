# Telegram Bot 接入实现计划（与飞书并列）

> **For agentic workers:** 本计划逐任务实现。步骤用 `- [ ]` 复选框跟踪。
> **测试策略**：遵循项目偏好「功能优先，测试最后补」——先实现全部功能（Phase 1–6），最后统一补测试（Phase 7）。**不**按 per-task TDD。

**Goal:** 在天枢中实现 Telegram 机器人接入，作为飞书接入的并列兄弟通道，功能对齐（助手模式 + 敕令模式 + 审批 + /menu /list /budget + persona + 执行完成回推）。

**Architecture:** 镜像兄弟包 `src/tianshu/gateway/telegram/`，结构与 `gateway/feishu/` 对应。直接 import 复用平台无关核心（`EdictBridge`、`parse_approval_command`、`PersonaRenderer`、`markdown_compat`、`ApprovalManager`、`Executor`、`Storage`、`CostManager`、`event_bus`）；重写平台层（连接/出站/分支/审批 UI/卡片）。连接默认长轮询，支持 webhook。

**Tech Stack:** Python 3.12 + FastAPI + `python-telegram-bot>=22.6,<23`（异步 `Application`/`Bot`/`Update`），SQLite（复用 `channel_configs`，新增 `telegram_*` 会话表），Fernet vault 加密 bot_token，React/Vite 前端（通政司页）。

---

## 设计基线（来自双工作流深度调研）

### 复用 vs 镜像 决策表

| 飞书文件 | 处置 | 理由 |
|---|---|---|
| `edict_bridge.py` (`EdictBridge`, `EdictBusyError`, `EdictBridgeResult`) | **复用 + 微调** | 逻辑（history 构建/follow_up/executor 任务管理）有价值且易写错；硬编码 `channel="feishu"` → 加**向后兼容** `channel` 参数 |
| `approval_commands.py::parse_approval_command` / `ApprovalCommand` | **import 复用** | 纯函数，平台无关 |
| `approval_commands.py::ApprovalCommandHandler` | **镜像** | `_list_pending_for_chat` 直查 `feishu_pending_cards` 表 → telegram 变体查 `telegram_pending_buttons` |
| `persona_renderer.py` (`PersonaRenderer`) | **import 复用** | 纯文本模板（emoji+name+各类 reply 文案），平台无关 |
| `markdown_compat.py` (`convert_tables_to_lists`, `split_long`) | **import 复用** | 纯字符串工具 |
| `card_builder.py::format_status_label` | **import 复用** | status→label 纯函数 |
| `card_builder.py::CardBuilder` | **镜像** | 产出 lark 卡片 JSON → telegram 产出 `(text, InlineKeyboardMarkup)` |
| `mode_router.py` (`ModeRouter`, `ModeContext`) | **镜像（轻）** | 逻辑零飞书耦合（只用 `anchor.get`），但为包内聚 + 避免跨包 import FeishuMessage 类型，镜像一份（~100 行） |
| `assistant_branch.py` / `edict_branch.py` | **镜像** | 仅 3 处飞书耦合：`storage.delete_feishu_anchor`→`anchor.delete`；`outbound.add_reaction`+`storage.save_feishu_thinking`→telegram thinking 占位消息；`outbound.send_card(lark_dict)`→telegram `(text,kb)` |
| `dispatcher.py` (`Dispatcher`, `FeishuMessage`, `FeishuCardAction`) | **镜像** | 飞书事件解析专用；批处理/锁/命令路由模式照搬 → `TelegramMessage`/`TelegramCallback` |
| `outbound.py` (`FeishuOutbound`) | **镜像** | lark client → ptb Bot；事件订阅 `execution.completed/failed` 照搬，**新增 channel 过滤** |
| `approval_card.py` (`ApprovalCardHandler`) | **镜像** | lark 卡片 + EventBus 订阅 `tool.approval_required`/`decree.*` → telegram inline keyboard |
| `card_action_dispatcher.py` (`CardActionDispatcher`) | **镜像** | 飞书卡片按钮→合成命令 → telegram `callback_query` 解码 |
| `connection.py` (`WebSocketConnection`/`WebhookConnection`) | **镜像** | lark 连接 → ptb 长轮询/webhook |
| `security.py` (`is_allowed_user`, 签名校验) | **镜像** | allowlist 复用模式；签名 lark→telegram webhook secret header |
| `settings.py` (`FeishuSettings`) | **镜像** | `TelegramSettings` 字段对应 |
| `__init__.py` (`FeishuBot`) | **镜像** | `TelegramBot` 门面，构造签名/生命周期完全对齐 |

### 关键正确性约束（必须遵守）

1. **channel 路由隔离**：`EdictBridge` 写 `metadata.channel`；telegram 出站 `_on_execution_completed/_failed` 必须 `edict.metadata.channel == "telegram"` 才投递，否则 feishu/telegram 交叉投递（feishu 拿 telegram chat_id 走 lark 失败，反之亦然）。`home_channel` 兜底（无 channel 的 cron 敕令）两通道各自投递自己的 home（符合"并列"语义）。
   - **附带**：给飞书出站 `_lookup_chat_id` 加一行守卫——`metadata.channel` 已设且 != "feishu" 时返回 None（向后兼容：现有飞书敕令 channel="feishu"，cron 敕令无 channel 走 home）。这是唯一必要的飞书侧改动。
2. **thinking 指示**：Telegram 无 lark 那种任意消息 emoji reaction 流程。改为：发占位消息 `⏳ 思考中…`，记 `telegram_thinking_messages(memorial_id→chat_id, message_id)`；执行完成时先 `deleteMessage` 占位再发结果。
3. **callback_data ≤64 字节**：审批/菜单按钮 `callback_data` 用紧凑编码 `ea:<action>:<scope>:<mid8>` / `cmd:<name>`；必要时把完整态存 `telegram_pending_buttons` 用 approval_id 反查。
4. **MarkdownV2 转义 + UTF-16 4096 分片**：发送先转 MarkdownV2（失败回退纯文本 `parse_mode=None`）；按 UTF-16 码元 ≤4096 分片（emoji 占 2 单元）。复用 `convert_tables_to_lists`/`split_long`（split_long 默认 8000，telegram 传 `max_len=4000` 留转义余量）。
5. **409 Conflict**：长轮询启动若另一进程占用同 token → ptb 抛 `Conflict`；记录并降级（与飞书锁冲突一致：不阻塞 web 启动）。沿用 app-lock：`~/.tianshu/telegram_app_lock.<token前8位hash>`。
6. **群组 @ 门控**：飞书靠 mentions 数组；telegram 群里 `@botname` 出现在文本/entities。`chat.type in (group,supergroup)` 时要求 entity `mention`==@bot 或 `reply_to_message.from.id==bot_id` 才处理；私聊直接处理。

---

## 文件结构

```
src/tianshu/gateway/telegram/
├── __init__.py            TelegramBot 门面（镜像 FeishuBot；构造签名一致）
├── settings.py            TelegramSettings + from_global_settings + 校验
├── connection.py          PollingConnection / WebhookConnection（ptb Application）
├── dispatcher.py          TelegramMessage / TelegramCallback + Dispatcher（dedup/锁/批处理/群门控）
├── outbound.py            TelegramOutbound（send_text/send_card/edit/delete + 事件订阅 + channel 过滤 + thinking）
├── mode_router.py         ModeRouter / ModeContext（镜像）
├── assistant_branch.py    AssistantBranch（镜像，3 处耦合改适配）
├── edict_branch.py        EdictBranch（镜像）
├── approval_kb.py          ApprovalKeyboardHandler（镜像 approval_card：inline keyboard + EventBus）
├── card_builder.py        TelegramCardBuilder（/list /menu /budget → (text, InlineKeyboardMarkup)）
├── card_action_dispatcher.py  CallbackDispatcher（callback_query → 合成命令）
├── approval_commands.py   TelegramApprovalCommandHandler（复用 parse_approval_command，查 telegram_pending_buttons）
├── security.py            is_allowed_user(int) + verify_webhook_secret
└── session_anchor.py      SessionAnchor（telegram_session_anchor，含 delete）

修改：
├── src/tianshu/config.py                   +telegram_* 字段
├── src/tianshu/storage.py                  +telegram_* 表 DDL + 方法
├── src/tianshu/gateway/feishu/edict_bridge.py   +channel 参数（向后兼容）
├── src/tianshu/gateway/feishu/outbound.py       _lookup_chat_id channel 守卫（1 行）
├── src/tianshu/gateway/tongzheng_api.py    +Telegram 三端点 + _build_telegram_settings_from_runtime
├── src/tianshu/app.py                      +Telegram lifespan 接线（镜像飞书块）
├── pyproject.toml                          +python-telegram-bot 依赖
└── web/src/api/tongzheng.ts + pages/TongzhengPage.tsx + i18n  +Telegram 配置区

新增测试（Phase 7）：tests/gateway/telegram/
```

---

## Phase 0：依赖

### Task 0.1：加 python-telegram-bot 依赖
**Files:** Modify `pyproject.toml`

- [ ] 在 `dependencies` 列表加 `"python-telegram-bot>=22.6,<23"`
- [ ] 运行 `uv sync`（或 `.venv/bin/python -m pip install "python-telegram-bot>=22.6,<23"`）
- [ ] 验证：`.venv/bin/python -c "import telegram; print(telegram.__version__)"`

---

## Phase 1：配置与存储基座

### Task 1.1：config.py 增 telegram 字段
**Files:** Modify `src/tianshu/config.py`（飞书字段在 37–55 行附近）

- [ ] 在飞书字段块后追加（env 前缀 `TIANSHU_`）：
```python
# --- Telegram ---
telegram_bot_token: str = ""                  # 空 → 不启用（向后兼容）
telegram_connection_mode: str = "polling"     # polling | webhook
telegram_allowed_users: str = ""              # 逗号分隔 user_id（int）
telegram_home_channel: str = ""               # cron/无源审批兜底 chat_id（str，群为负数）
telegram_webhook_path: str = "/telegram/webhook"
telegram_webhook_secret: str = ""             # webhook 模式 X-Telegram-Bot-Api-Secret-Token
telegram_poll_timeout: int = 30               # getUpdates 长轮询超时秒
telegram_text_batch_delay: float = 0.6        # 文本批处理静默期
telegram_dedup_cache_size: int = 2048
telegram_assistant_persona_id: str = "tongzheng"
telegram_disable_assistant_mode: bool = False
telegram_enable_edict_submission: bool = False
```

### Task 1.2：storage 增 telegram 表 + 方法
**Files:** Modify `src/tianshu/storage.py`（飞书表 DDL 在 392–417；方法在 2904–3060）

- [ ] 在飞书表 `executescript` 块后新增（紧邻 417 行后）：
```python
self._conn.executescript("""
    CREATE TABLE IF NOT EXISTS telegram_session_anchor (
        chat_id          TEXT PRIMARY KEY,
        current_edict_id TEXT,
        updated_at       TIMESTAMP NOT NULL
    );
    CREATE TABLE IF NOT EXISTS telegram_seen_messages (
        update_id   TEXT PRIMARY KEY,
        seen_at     TIMESTAMP NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tg_seen_at ON telegram_seen_messages(seen_at);
    CREATE TABLE IF NOT EXISTS telegram_pending_buttons (
        approval_id TEXT PRIMARY KEY,
        chat_id     TEXT NOT NULL,
        message_id  TEXT NOT NULL,
        kind        TEXT NOT NULL,
        created_at  TIMESTAMP NOT NULL
    );
    CREATE TABLE IF NOT EXISTS telegram_thinking_messages (
        memorial_id TEXT PRIMARY KEY,
        chat_id     TEXT NOT NULL,
        message_id  TEXT NOT NULL,
        created_at  TIMESTAMP NOT NULL
    );
""")
```
- [ ] 新增方法（镜像飞书对应方法，表名替换；放在飞书方法附近）：
  `get_telegram_anchor` / `set_telegram_anchor` / `delete_telegram_anchor` /
  `list_telegram_active_anchor_chats` / `list_telegram_chats_anchored_to(edict_id)` /
  `is_telegram_update_seen(update_id)` / `mark_telegram_update_seen(update_id, max_entries=2048)` /
  `save_telegram_thinking(*, memorial_id, chat_id, message_id)` / `pop_telegram_thinking(memorial_id) -> dict|None` /
  `save_telegram_pending_button(*, approval_id, chat_id, message_id, kind)` /
  `pop_telegram_pending_button(approval_id) -> dict|None` /
  `list_telegram_pending_for_chat(chat_id) -> list[str]`（查 kind='tool.approval_required'）
- [ ] `channel_configs` 表无需改（telegram 复用 `get/save/load_channel_runtime_config("telegram")`）

### Task 1.3：TelegramSettings
**Files:** Create `src/tianshu/gateway/telegram/settings.py`, `src/tianshu/gateway/telegram/__init__.py`(占位)

- [ ] 镜像 `feishu/settings.py`：
```python
@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    connection_mode: str           # polling | webhook
    allowed_users: tuple[int, ...]
    home_channel: str
    webhook_path: str
    webhook_secret: str
    poll_timeout: int
    text_batch_delay: float
    dedup_cache_size: int
    assistant_persona_id: str = "tongzheng"
    disable_assistant_mode: bool = False
    enable_edict_submission: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token)

    def validate_or_raise(self) -> None:
        if not self.enabled:
            return
        if self.connection_mode not in ("polling", "webhook"):
            raise RuntimeError(f"invalid connection_mode: {self.connection_mode}")
        if self.connection_mode == "webhook" and not self.webhook_secret:
            raise RuntimeError("telegram webhook_secret required in webhook mode")

def from_global_settings(s) -> TelegramSettings: ...   # 解析 allowed_users csv→tuple[int]
```

---

## Phase 2：核心复用层微调

### Task 2.1：EdictBridge 加 channel 参数（向后兼容）
**Files:** Modify `src/tianshu/gateway/feishu/edict_bridge.py`

- [ ] `__init__` 增 `channel: str = "feishu"`、`user_meta_key: str = "feishu_user"`，存 `self._channel`/`self._user_meta_key`
- [ ] `create_new`/`ensure_chat_edict` 中 `metadata={"channel": "feishu", ..., "feishu_user": ...}` 改用 `self._channel`/`self._user_meta_key`；`producer="feishu_bot"` 改 `producer=f"{self._channel}_bot"`；`payload`/`append_event` 中 `"channel": "feishu"` 改 `self._channel`；标题 `"飞书助手对话"` 改按 channel（feishu 保持原文案，telegram 用 "Telegram 助手对话"）→ 用 `self._chat_title_prefix: str = "飞书助手对话"` 参数，默认不变
- [ ] **验证向后兼容**：默认参数下飞书行为字节级不变

### Task 2.2：feishu 出站 channel 守卫
**Files:** Modify `src/tianshu/gateway/feishu/outbound.py::_lookup_chat_id`（300–319）

- [ ] `edict = self._storage.get_edict(...)` 后加：
```python
ch = (edict.metadata or {}).get("channel")
if ch and ch != "feishu":
    return None   # 非飞书敕令不由飞书投递（telegram 等并列通道各自处理）
```
（保持 cron 无 channel 敕令走 home_channel）

---

## Phase 3：Telegram 平台层 — 连接 / 出站 / 入站

### Task 3.1：security.py
**Files:** Create `src/tianshu/gateway/telegram/security.py`
- [ ] `is_allowed_user(user_id: int, allowed: tuple[int,...]) -> bool`（空 allowed=放行任意，与飞书一致）
- [ ] `verify_webhook_secret(header_value: str, expected: str) -> bool`（常量时间比较）

### Task 3.2：session_anchor.py
**Files:** Create `src/tianshu/gateway/telegram/session_anchor.py`
- [ ] `SessionAnchor`：`get/set/delete(chat_id)` → `storage.{get,set,delete}_telegram_anchor`（注意：飞书 anchor 无 delete 方法在类上，分支直接调 storage；telegram 把 delete 收进 anchor 类，分支统一走 `anchor.delete`）

### Task 3.3：dispatcher.py
**Files:** Create `src/tianshu/gateway/telegram/dispatcher.py`
- [ ] `@dataclass TelegramMessage(update_id, chat_id, chat_type, sender_id, text, raw, message_id)`（chat_id/sender_id 用 str 归一，内部转 int 发送）
- [ ] `@dataclass TelegramCallback(update_id, chat_id, sender_id, message_id, data: str)`
- [ ] `Dispatcher`：构造接 `settings, message_handler, callback_handler`；提供 `handle_message(TelegramMessage)`、`handle_callback(TelegramCallback)`（由 connection 的 ptb handler 调用）。内含：
  - dedup（`storage.is_telegram_update_seen`/`mark`）
  - allowlist（`is_allowed_user`）
  - 群门控（chat_type group/supergroup 需 @bot 或 reply_to_bot）
  - `/` 命令直发（带 per-chat asyncio.Lock）；纯文本走 `text_batch_delay` 批处理合并（照搬飞书 `_enqueue_for_batch`）

### Task 3.4：outbound.py（含 channel 过滤 + thinking）
**Files:** Create `src/tianshu/gateway/telegram/outbound.py`
- [ ] `TelegramOutbound(settings, storage, event_bus)`，持 `telegram.Bot`
- [ ] `start()`：建 Bot + 订阅 `execution.completed`/`execution.failed`（priority=200）；`rebuild_client()`：仅重建 Bot
- [ ] `async send_text(chat_id, content) -> str|None`：`format_message`(MarkdownV2)→UTF-16 分片→逐片 `bot.send_message(parse_mode=MarkdownV2)`，`BadRequest`(parse) 回退 `parse_mode=None`；返回首片 message_id
- [ ] `async send_card(chat_id, payload: tuple[str, InlineKeyboardMarkup|None]) -> str|None`：`bot.send_message(text, reply_markup=kb)`
- [ ] `async edit_message(chat_id, message_id, text, reply_markup=None) -> bool`
- [ ] `async delete_message(chat_id, message_id) -> bool`（非致命）
- [ ] `async send_thinking(chat_id) -> str|None`：发 `⏳ 思考中…` 返回 message_id（供分支登记 thinking 表）
- [ ] `format_message(content) -> str`：markdown→MarkdownV2（保护代码块、`**`→`*`、转义 `_*[]()~\`>#+-=|{}.!`、GFM 表用 ```包裹）+ `utf16_len`/`truncate_message` 工具（移植 hermes `telegram.py` 逻辑）
- [ ] `_on_execution_completed/_failed`：**先 `metadata.channel=="telegram"` 守卫**；`_lookup_chat_id`（metadata.chat_id → `list_telegram_chats_anchored_to` → `settings.home_channel`）；`pop_telegram_thinking`→`delete_message` 占位→`convert_tables_to_lists`+`split_long(max_len=4000)` 分片发送

### Task 3.5：connection.py（长轮询 + webhook）
**Files:** Create `src/tianshu/gateway/telegram/connection.py`
- [ ] `PollingConnection(settings, dispatcher)`：`Application.builder().token().build()`；注册 `MessageHandler(filters.TEXT...)`→归一为 `TelegramMessage`→`dispatcher.handle_message`；`CallbackQueryHandler`→`TelegramCallback`→`dispatcher.handle_callback`；`start()`: `initialize/start/updater.start_polling(timeout, allowed_updates=ALL, drop_pending_updates=False)`，先 `bot.delete_webhook(drop_pending_updates=False)`；`error_callback` 检测 `Conflict`（致命，记录）/`NetworkError`（退避）
- [ ] `WebhookConnection(settings, dispatcher)`：暴露 FastAPI `router`（POST `webhook_path`，校验 `X-Telegram-Bot-Api-Secret-Token`），解析 `Update.de_json`→分发；`start()`: `bot.set_webhook(url, secret_token)`（url 由 home 域名拼，或留空仅注册路由，文档说明）

---

## Phase 4：分支 / 路由 / 卡片（镜像）

### Task 4.1：mode_router.py
**Files:** Create `src/tianshu/gateway/telegram/mode_router.py`
- [ ] 镜像飞书 `ModeRouter`/`ModeContext`，类型注解换 telegram（运行时无关）；`dispatch(TelegramMessage)` 逻辑同：`ensure_chat_edict`→`resolve_mode`→分支

### Task 4.2：card_builder.py（telegram 原生）
**Files:** Create `src/tianshu/gateway/telegram/card_builder.py`
- [ ] `TelegramCardBuilder(storage, cost_manager)`：`build_list_card(edicts, current_anchor) -> (text, InlineKeyboardMarkup)`（每敕令一行文本 + `/select` 按钮 `callback_data="cmd:select:<id8>"`）；`build_menu_card() -> (text, kb)`（命令按钮）；`async build_budget_card() -> (text, kb)`（查 cost_ledger，移植飞书预算逻辑）
- [ ] 复用 `from tianshu.gateway.feishu.card_builder import format_status_label`

### Task 4.3：approval_commands.py
**Files:** Create `src/tianshu/gateway/telegram/approval_commands.py`
- [ ] `from tianshu.gateway.feishu.approval_commands import parse_approval_command, ApprovalCommand`
- [ ] `TelegramApprovalCommandHandler`：镜像飞书 `ApprovalCommandHandler.handle`，`_list_pending_for_chat` 改查 `telegram_pending_buttons`（用 `storage.list_telegram_pending_for_chat`）

### Task 4.4：assistant_branch.py / edict_branch.py
**Files:** Create both（镜像，改 3 处耦合）
- [ ] 复用 import：`parse_approval_command`、`format_status_label`、`PersonaRenderer`(类型)、`EdictStatus`、`EdictBusyError`
- [ ] `_send_thinking` 改：`mid = await self._outbound.send_thinking(msg.chat_id)`；`if mid: self._storage.save_telegram_thinking(memorial_id=..., chat_id=..., message_id=mid)`（不再用 reaction/message_id 入参）
- [ ] `/clear`、`/exit`、`/cancel` 中 `storage.delete_feishu_anchor` → `self._anchor.delete(chat_id)`
- [ ] `send_card(card)` 处：card 现为 `(text, kb)`，`outbound.send_card` 接受该元组

### Task 4.5：approval_kb.py（审批 inline keyboard）
**Files:** Create `src/tianshu/gateway/telegram/approval_kb.py`
- [ ] `ApprovalKeyboardHandler(settings, storage, event_bus, approval_manager, outbound)`
- [ ] `start()`：订阅 `tool.approval_required`（→发审批消息 + 4 按钮：准/准敕/准永/驳，`callback_data="ea:approve:once:<mid8>"` 等）、`decree.approved`/`decree.rejected`（→`edit_message` 去按钮 + 标注结果）。登记 `save_telegram_pending_button(approval_id=memorial_id, chat_id, message_id, kind="tool.approval_required")`
- [ ] `home_channel` 兜底投递（无 chat_id 的审批）

### Task 4.6：card_action_dispatcher.py（callback → 命令）
**Files:** Create `src/tianshu/gateway/telegram/card_action_dispatcher.py`
- [ ] `CallbackDispatcher(mode_router, approval_kb)`：`handle(TelegramCallback)`：
  - `data` 前缀 `ea:` → 审批：解码 `action/scope/mid`，调 `approval_manager.submit_tool_decision`（或经 approval_commands），`edit_message` 反馈
  - `cmd:` → 合成 `TelegramMessage`（text=`/select <id>` 等）重入 `mode_router.dispatch`
  - 始终 `callback_query.answer()` 消除 loading 圈

---

## Phase 5：TelegramBot 门面

### Task 5.1：__init__.py（TelegramBot）
**Files:** `src/tianshu/gateway/telegram/__init__.py`
- [ ] 构造签名**完全对齐** FeishuBot：`(*, storage, event_bus, approval_manager, executor, notifier, settings, persona_loader=None, provider_manager=None, cost_manager=None)`
- [ ] 内部装配：`SessionAnchor` → `EdictBridge(channel="telegram", user_meta_key="telegram_user", chat_title_prefix="Telegram 助手对话")` → `TelegramOutbound` → `TelegramApprovalCommandHandler` → `PersonaRenderer`(import 复用) → `TelegramCardBuilder` → `AssistantBranch`/`EdictBranch` → `ModeRouter` → `ApprovalKeyboardHandler` → `CallbackDispatcher` → `Dispatcher` → `connection`
- [ ] `start()`：app-lock(`telegram_app_lock.<hash>`)、建 connection（polling/webhook）、`dispatcher` 由 connection 驱动、`outbound.start()`、`approval_kb.start()`
- [ ] `stop()`：connection 停、释放锁
- [ ] `reload(new_settings)`：镜像飞书（停 connection→换 settings→重建 connection→`outbound.rebuild_client()`+换引用→换 persona renderer→同步 branches/router）
- [ ] `attach_webhook_router(app)`：webhook 模式挂 `connection.router`
- [ ] `_on_message`/`_on_callback`：disable_assistant_mode 时走 legacy（可简化：仅 /new /status /cancel /help），否则 `mode_router.dispatch` / `callback_dispatcher.handle`

---

## Phase 6：接线（app.py / API / 前端）

### Task 6.1：app.py lifespan 接线
**Files:** Modify `src/tianshu/app.py`（飞书块 371–409；shutdown 635–636；env 注册 299–305）
- [ ] 在飞书块后镜像 Telegram 块（DB>env 加载、构造、`start()` try/except 降级、`app.state.telegram_bot`、webhook 模式 `attach_webhook_router`）
- [ ] shutdown 加 `if telegram_bot: await telegram_bot.stop()`
- [ ] notifier 通道：v1 不加 TelegramChannel（gateway 出站已覆盖完成投递；飞书 notifier 是 deprecated webhook 路径）

### Task 6.2：tongzheng_api Telegram 端点
**Files:** Modify `src/tianshu/gateway/tongzheng_api.py`
- [ ] `TelegramChannelConfig(BaseModel)`（bot_token 单独提交语义同 app_secret）
- [ ] `_build_telegram_settings_from_runtime(runtime_cfg)`
- [ ] `GET /channels/telegram`（env 回退 + 掩码 bot_token）、`PUT /channels/telegram`（save + reload + 同步 edict 工具集）、`GET /channels/telegram/status`

### Task 6.3：前端通政司 Telegram 配置区
**Files:** Modify `web/src/api/tongzheng.ts`, `web/src/pages/TongzhengPage.tsx`, `web/src/i18n/locales/{zh-modern,zh-classic,en}.json`
- [ ] `tongzheng.ts`：`TelegramChannelConfig`/`TelegramChannelView`/`TelegramStatus` 接口 + `getTelegramChannel`/`putTelegramChannel`/`getTelegramStatus`
- [ ] `TongzhengPage.tsx`：加 Telegram 配置卡/Tab（字段：bot_token、connection_mode、allowed_users、home_channel、webhook_path/secret、persona 下拉、enable_edict_submission）
- [ ] i18n：三语 key（label 文案；zh-classic 维持彩蛋风格基线）

---

## Phase 7：测试（最后统一补）

### Task 7.1：单元测试
**Files:** Create `tests/gateway/telegram/test_*.py`
- [ ] `test_settings.py`：validate_or_raise（polling/webhook/缺 secret）、from_global_settings csv 解析
- [ ] `test_format.py`：MarkdownV2 转义、UTF-16 分片（emoji=2 单元）、表格包裹、回退
- [ ] `test_dispatcher.py`：dedup、allowlist、群门控、命令直发 vs 文本批处理合并
- [ ] `test_callback.py`：`ea:`/`cmd:` 解码 + answer
- [ ] `test_approval_commands.py`：复用 parse + telegram pending 查询
- [ ] `test_channel_isolation.py`：telegram 出站只投 channel==telegram；飞书守卫拒投 telegram 敕令
- [ ] `test_edict_bridge_channel.py`：channel 参数向后兼容（默认 feishu 不变；telegram 写对 metadata）

### Task 7.2：集成测试（mock telegram.Bot）
- [ ] `test_telegram_bot.py`：构造/start/stop（mock ptb Application）；execution.completed → 删 thinking + 发结果 pipeline
- [ ] 运行 `.venv/bin/python -m pytest tests/gateway/telegram -v` 全绿
- [ ] 运行全量 `.venv/bin/python -m pytest tests/gateway -q` 确认飞书无回归

### Task 7.3：文档
**Files:** Create `docs/ops/telegram-setup.md`（BotFather 建 token、allowlist、polling/webhook、home_channel 取 chat_id）

---

## Self-Review 覆盖确认
- [x] 连接（polling+webhook）、出站、入站、分支、审批、菜单、persona、完成回推 — Phase 3/4/5 全覆盖
- [x] 配置（env+DB+热加载）、前端 — Phase 1/6
- [x] channel 隔离正确性约束 — Task 2.2/3.4
- [x] 测试 — Phase 7（功能优先，最后补）
- [x] 飞书零回归（仅 2 处向后兼容微调）— Task 2.1/2.2
