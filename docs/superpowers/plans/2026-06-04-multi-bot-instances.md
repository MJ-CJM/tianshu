# 多机器人实例（每渠道多 bot）实现方案

> **For agentic workers:** 逐任务实现，步骤用 `- [ ]` 跟踪。
> **测试策略**：遵循项目偏好「功能优先，测试最后补」——先实现功能（Phase 1–6），最后统一补测试（Phase 7）。不按 per-task TDD。
> **状态**：待 review，**尚未实现**。

**Goal:** 让飞书 / Telegram 各自能配置并同时运行**多个 bot 实例**，每个实例有独立的凭证 / persona / allowlist / home，且实例之间的会话、审批、`/list` 列表相互**隔离**（web 看板仍全局可见所有敕令）。

**Architecture:** 引入 **`instance_id`** 作为唯一的「哪个 bot」维度。配置从「每渠道一行」改为「每实例一行」（新表 `channel_instances`）；运行期由 `ChannelBotManager` 持有 `dict[instance_id → Bot]`，统一启停/热加载/运行时增删；敕令在 `metadata.instance_id` 标记归属，出站/审批/会话/`/list` 全部按 `instance_id` 路由与隔离。现有单 bot 配置自动迁移为 `{channel}-default` 实例，向后兼容。

**Tech Stack:** Python 3.12 + FastAPI + SQLite（新增 `channel_instances` 表 + 会话表加 `instance_id` 维度）、Fernet vault、`lark-oapi` / `python-telegram-bot`、React/Vite 前端。

---

## 设计决策（已与用户确认）

| 维度 | 决策 |
|---|---|
| 多 bot 用途 | 不同 bot 绑不同 persona/部门 + 单纯多挂 token（两者都要） |
| 数据隔离 | **按实例隔离**：会话锚 / 待审批 / thinking / 聊天侧 `/list` 都按 `instance_id` 隔离 |
| web 看板 | **保持全局**：`GET /api/edicts` 不按实例过滤，运维仍能看到所有 bot 的敕令 |
| 凭证迁移 | 现有 `channel_configs` 的 feishu/telegram 行 → 自动迁移为 `{channel}-default` 实例 |
| 运行时增删 | 引入 `ChannelBotManager`，**支持运行时创建/启停实例**（顺带解决「首次启用需重启」的旧限制） |

### 核心不变量（必须保证）
1. **路由隔离**：`execution.completed/failed`、`tool.approval_required`、`decree.*` 事件，**只有 `edict.metadata.instance_id == 自己** 的那个 bot 出站投递。旧敕令（无 instance_id）归属 `{channel}-default`。
2. **会话隔离**：Telegram 私聊 `chat_id` = 用户 id，**跨 bot 会重复**（同一用户私聊 A、B 两个 bot 的 chat_id 相同）。所以会话/待审批表主键必须是 `(instance_id, chat_id)` 复合键，否则两个 bot 的会话互相覆盖。
3. **向后兼容**：未配置 DB 实例时，从 env 构造 `{channel}-default` 实例；旧的 `/channels/feishu|telegram` 端点保留为对 default 实例的薄封装。
4. **EventBus 不重复订阅**：N 个实例各订阅一次；单实例 reload 只重建 client，不重订阅（沿用现有 outbound.reload 行为）。

---

## 现状阻塞点（已核实，作为改造起点）

| 层 | 文件:行 | 现状 | 改造 |
|---|---|---|---|
| 配置主键 | `storage.py:446` | `channel_configs(channel_type PK)` | 新表 `channel_instances(instance_id PK)` |
| 运行实例 | `app.py:385/401/425/441` | `app.state.feishu_bot/telegram_bot` 单例 | `ChannelBotManager` 持 `dict` |
| 会话表 | `storage.py:393/420/430` | PK = `chat_id`/`approval_id`，无实例维度 | 加 `instance_id`，复合主键 |
| 出站路由 | `feishu/outbound.py` `_lookup_chat_id`、`telegram/outbound.py` | 按 `metadata.channel` 过滤 | 改按 `metadata.instance_id` 过滤 |
| 敕令标记 | `feishu/edict_bridge.py:141` | 写 `metadata.channel` | 增写 `metadata.instance_id` |
| `/list` | `assistant_branch.py` `_cmd_list` → `storage.list_edicts` | 全局列 | 加 `instance_id` 过滤 |
| 配置 API | `tongzheng_api.py` | 单 GET/PUT/status | 实例 CRUD 列表 |
| 前端 | `TongzhengPage.tsx` | 单表单 | 实例列表 + 增删 |

---

## 文件结构

```
新增：
  src/tianshu/gateway/bot_manager.py        ChannelBotManager（持 deps + dict[instance_id→Bot]，启停/reload/增删）
  src/tianshu/gateway/instance.py            ChannelInstance dataclass（instance_id, channel_type, label, enabled, settings）

修改（storage / 配置）：
  src/tianshu/storage.py                     +channel_instances 表与 CRUD；会话表加 instance_id（迁移）；list_edicts +instance_id 过滤
  src/tianshu/config.py                       （无需改字段；env 仍构造 default 实例）

修改（飞书 bot 核心 —— 加 instance_id 贯穿）：
  src/tianshu/gateway/feishu/__init__.py       FeishuBot.__init__(+instance_id)
  src/tianshu/gateway/feishu/edict_bridge.py   +instance_id 写入 metadata
  src/tianshu/gateway/feishu/session_anchor.py SessionAnchor(+instance_id)
  src/tianshu/gateway/feishu/outbound.py       _lookup_chat_id 按 instance_id 路由
  src/tianshu/gateway/feishu/approval_card.py  _on_approval_required +instance_id 守卫
  src/tianshu/gateway/feishu/assistant_branch.py /list 按 instance_id 过滤
  src/tianshu/gateway/feishu/settings.py       FeishuSettings +instance_id（或由 ChannelInstance 携带）

修改（Telegram bot 核心 —— 对称）：
  src/tianshu/gateway/telegram/__init__.py / edict_bridge 复用 / session_anchor.py /
  outbound.py / approval_kb.py / assistant_branch.py / settings.py

修改（接线 / API / 前端）：
  src/tianshu/app.py                          lifespan 用 ChannelBotManager
  src/tianshu/gateway/tongzheng_api.py        实例 CRUD + 旧端点薄封装
  web/src/api/tongzheng.ts                    实例列表 API
  web/src/pages/TongzhengPage.tsx             实例列表 UI
  web/src/components/config/{Feishu,Telegram}ChannelForm.tsx  改为按实例编辑

测试（Phase 7）：tests/gateway/test_bot_manager.py、test_instance_isolation.py、扩充现有 telegram/feishu 测试
```

---

## Phase 1：Storage —— 实例配置表 + 会话表实例化 + 列表过滤

### Task 1.1：新增 `channel_instances` 表
**Files:** Modify `src/tianshu/storage.py`（`_create_tables` 内，紧邻 `channel_configs` 建表后）

- [ ] 加建表：
```python
self._conn.executescript("""
    CREATE TABLE IF NOT EXISTS channel_instances (
        instance_id      TEXT PRIMARY KEY,
        channel_type     TEXT NOT NULL,          -- feishu | telegram
        label            TEXT NOT NULL DEFAULT '',
        enabled          INTEGER NOT NULL DEFAULT 1,
        config_json      TEXT NOT NULL,
        encrypted_secret BLOB,
        updated_at       TIMESTAMP NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_channel_instances_type
        ON channel_instances(channel_type);
""")
```

### Task 1.2：`channel_instances` CRUD 方法
**Files:** Modify `src/tianshu/storage.py`（channel_configs 方法附近）

- [ ] 实现（secret 仍走 vault 加密；解密键按 channel：feishu→`app_secret`，telegram→`bot_token`，复用现有 `load_channel_runtime_config` 的 `secret_key` 逻辑）：
```python
def list_channel_instances(self, channel_type: str | None = None) -> list[dict]:
    """列实例（不含明文 secret，含 _has_secret）。"""
def get_channel_instance(self, instance_id: str) -> dict | None:
    """单实例非敏感视图（含 _has_secret，secret 掩码由 API 层处理）。"""
def save_channel_instance(self, *, instance_id: str, channel_type: str, label: str,
                          enabled: bool, config: dict, secret_plaintext: str | None) -> None:
    """secret_plaintext: None=不改；""=清空；非空=加密替换（vault 缺失则 RuntimeError）。"""
def set_channel_instance_enabled(self, instance_id: str, enabled: bool) -> None: ...
def delete_channel_instance(self, instance_id: str) -> None: ...
def load_channel_instance_runtime(self, instance_id: str) -> dict | None:
    """含明文 secret 的运行时 dict（启动/reload 用）；vault 缺失或解密失败返 None。
       解密后的 secret 放到 channel 对应字段（feishu→app_secret / telegram→bot_token）。"""
```

### Task 1.3：会话/审批/thinking/seen 表加 `instance_id`（迁移）
**Files:** Modify `src/tianshu/storage.py`（`_migrate` 内）

- [ ] **设计**：现有 `feishu_session_anchor` / `telegram_session_anchor`（PK=chat_id）、`telegram_pending_buttons`/`feishu_pending_cards`（PK=approval_id）、`*_thinking_messages`（PK=memorial_id）、`*_seen_messages` 需要把「哪个 bot」并入键。
  - `*_thinking_messages`（PK=memorial_id）：memorial_id 全局唯一，**无需加 instance_id**（一个 memorial 只属一个 edict→一个实例）。保持不变。
  - `*_seen_messages`：去重表，加 `instance_id` 列避免不同 bot 的相同 update_id 误判；PK 改 `(instance_id, <id>)`。
  - `*_session_anchor`：PK 必须改为 `(instance_id, chat_id)`（Telegram 私聊 chat_id 跨 bot 重复）。
  - `*_pending_buttons`/`*_pending_cards`：approval_id=memorial_id 全局唯一，**无需加**；但 `list_*_pending_for_chat(chat_id)` 需按 `(instance_id, chat_id)` 过滤 → 加 `instance_id` 列（不改 PK）。
- [ ] **迁移方式**（SQLite 不能改 PK → 重建表，存量行赋默认实例 id）：
```python
def _migrate(self) -> None:
    ...
    self._migrate_session_tables_add_instance()

def _migrate_session_tables_add_instance(self) -> None:
    """把会话/去重表加 instance_id。存量行归属 {channel}-default。幂等。"""
    cur = self._conn
    # 已迁移检测：anchor 表是否已有 instance_id 列
    cols = [r[1] for r in cur.execute("PRAGMA table_info(telegram_session_anchor)")]
    if "instance_id" in cols:
        return
    cur.executescript("""
        -- telegram_session_anchor 重建为复合主键
        CREATE TABLE telegram_session_anchor_v2 (
            instance_id      TEXT NOT NULL,
            chat_id          TEXT NOT NULL,
            current_edict_id TEXT,
            updated_at       TIMESTAMP NOT NULL,
            PRIMARY KEY (instance_id, chat_id)
        );
        INSERT INTO telegram_session_anchor_v2 (instance_id, chat_id, current_edict_id, updated_at)
            SELECT 'telegram-default', chat_id, current_edict_id, updated_at FROM telegram_session_anchor;
        DROP TABLE telegram_session_anchor;
        ALTER TABLE telegram_session_anchor_v2 RENAME TO telegram_session_anchor;

        -- feishu_session_anchor 同理（默认 feishu-default）
        CREATE TABLE feishu_session_anchor_v2 (
            instance_id      TEXT NOT NULL,
            chat_id          TEXT NOT NULL,
            current_edict_id TEXT,
            updated_at       TIMESTAMP NOT NULL,
            PRIMARY KEY (instance_id, chat_id)
        );
        INSERT INTO feishu_session_anchor_v2 SELECT 'feishu-default', chat_id, current_edict_id, updated_at FROM feishu_session_anchor;
        DROP TABLE feishu_session_anchor;
        ALTER TABLE feishu_session_anchor_v2 RENAME TO feishu_session_anchor;

        -- pending 表加 instance_id 列（PK 不变）
        ALTER TABLE telegram_pending_buttons ADD COLUMN instance_id TEXT NOT NULL DEFAULT 'telegram-default';
        ALTER TABLE feishu_pending_cards   ADD COLUMN instance_id TEXT NOT NULL DEFAULT 'feishu-default';
        -- seen 表加 instance_id 列
        ALTER TABLE telegram_seen_messages ADD COLUMN instance_id TEXT NOT NULL DEFAULT 'telegram-default';
        ALTER TABLE feishu_seen_messages   ADD COLUMN instance_id TEXT NOT NULL DEFAULT 'feishu-default';
    """)
    self._conn.commit()
```
- [ ] 同步把**新库**的 `_create_tables` DDL 也更新成带 `instance_id` 的目标形态（新装用户直接拿到目标 schema，迁移只对老库生效）。

### Task 1.4：会话/审批 storage 方法全部加 `instance_id` 形参
**Files:** Modify `src/tianshu/storage.py`

- [ ] 飞书：`get_feishu_anchor(instance_id, chat_id)` / `set_feishu_anchor(instance_id, chat_id, edict_id)` / `delete_feishu_anchor(instance_id, chat_id)` / `list_active_anchor_chats(instance_id)` / `list_chats_anchored_to(instance_id, edict_id)` / `is_feishu_message_seen(instance_id, message_id)` / `mark_feishu_message_seen(instance_id, message_id, max)` / `save_feishu_pending_card(instance_id, ...)` / `pop_feishu_pending_card(approval_id)`（approval_id 唯一，不必加）/ `_list_pending_for_chat(instance_id, chat_id)`。
- [ ] Telegram：对称的 `*_telegram_*` 方法加 `instance_id`。
- [ ] **调用方同步**：`gateway/feishu/approval_commands.py` 的 `_list_pending_for_chat` SQL 加 `AND instance_id = ?`；telegram 同理。

### Task 1.5：`list_edicts` 增 `instance_id` 过滤
**Files:** Modify `src/tianshu/storage.py::list_edicts`

- [ ] 签名加 `instance_id: str | None = None`；为 None 时行为不变（web 看板用）。非 None 时加 WHERE：
```python
# metadata 是 JSON 列；用 json_extract 过滤归属实例（含旧敕令兜底）
if instance_id is not None:
    where.append(
        "(json_extract(metadata, '$.instance_id') = ? "
        " OR (json_extract(metadata, '$.instance_id') IS NULL "
        "     AND json_extract(metadata, '$.channel') = ?))"
    )
    params += [instance_id, instance_id.split('-', 1)[0]]  # 旧敕令: channel==instance 前缀
```
> 注：旧敕令无 instance_id，只有 channel；它们归属 `{channel}-default`，故 default 实例的 `/list` 用 `channel` 兜底匹配。

---

## Phase 2：实例模型 + Settings

### Task 2.1：`ChannelInstance` 数据模型
**Files:** Create `src/tianshu/gateway/instance.py`

- [ ] 定义：
```python
@dataclass(frozen=True)
class ChannelInstance:
    instance_id: str          # 如 "feishu-default" / ULID
    channel_type: str         # feishu | telegram
    label: str
    enabled: bool
    settings: object          # FeishuSettings | TelegramSettings

def default_instance_id(channel_type: str) -> str:
    return f"{channel_type}-default"
```

### Task 2.2：Settings 携带 instance_id（两渠道）
**Files:** Modify `gateway/feishu/settings.py`、`gateway/telegram/settings.py`

- [ ] 给 `FeishuSettings` / `TelegramSettings` 加字段 `instance_id: str = "{channel}-default"`（frozen dataclass 加默认值，向后兼容）。
- [ ] `from_global_settings(s)` 产出的实例 `instance_id` 设为 `"{channel}-default"`。
- [ ] runtime builder（`tongzheng_api._build_*_settings_from_runtime`）接受并填充 `instance_id`。

---

## Phase 3：Bot 核心 —— instance_id 贯穿（飞书 + Telegram 对称）

### Task 3.1：EdictBridge 写入 `metadata.instance_id`
**Files:** Modify `gateway/feishu/edict_bridge.py`（telegram 复用同一类）

- [ ] `__init__` 加 `instance_id: str = "feishu-default"`，存 `self._instance_id`。
- [ ] `create_new` / `ensure_chat_edict` 的 `metadata` 增 `"instance_id": self._instance_id`；`edict.submitted` 事件 payload 同增。
- [ ] 向后兼容：默认值保证旧调用（飞书 default）行为不变。

### Task 3.2：SessionAnchor 加 instance_id
**Files:** Modify `gateway/feishu/session_anchor.py`、`gateway/telegram/session_anchor.py`

- [ ] `__init__(storage, instance_id)`；`get/set/delete(chat_id)` 内部带 `self._instance_id` 调 storage。

### Task 3.3：出站按 instance_id 路由
**Files:** Modify `gateway/feishu/outbound.py`、`gateway/telegram/outbound.py`

- [ ] `__init__` 加 `instance_id`；`_lookup_chat_id` 守卫改为：
```python
inst = (edict.metadata or {}).get("instance_id")
if inst is None:
    # 旧敕令：按 channel 归属到 {channel}-default
    ch = (edict.metadata or {}).get("channel")
    inst = f"{ch}-default" if ch else None
if inst != self._instance_id:
    return None
```
- [ ] anchor/thinking/pending 反查全部带 `self._instance_id`。

### Task 3.4：审批 inline/卡片按 instance_id 守卫
**Files:** Modify `gateway/telegram/approval_kb.py`、`gateway/feishu/approval_card.py`

- [ ] `__init__` 加 `instance_id`；`_on_approval_required` 在取到 edict 后，用与 Task 3.3 相同的 `inst != self._instance_id: return` 守卫；`save_*_pending_*` 带 instance_id；`_on_decree_resolved` 的 pop 仍按 approval_id（唯一）但只有 owner 实例有该 pending 行。

### Task 3.5：`/list` 按实例隔离
**Files:** Modify `gateway/feishu/assistant_branch.py`、`gateway/telegram/assistant_branch.py`

- [ ] `_cmd_list` / `_cmd_select` / `_find_by_prefix` 调 `storage.list_edicts(..., instance_id=self._instance_id)`，分支需持有 `instance_id`（构造时注入）。

### Task 3.6：Bot 门面注入 instance_id
**Files:** Modify `gateway/feishu/__init__.py`、`gateway/telegram/__init__.py`

- [ ] `FeishuBot.__init__` / `TelegramBot.__init__` 加 `instance_id: str`；构造 EdictBridge / SessionAnchor / Outbound / ApprovalHandler / 分支时层层传入。
- [ ] app lock 已按 token/app_id 哈希，天然多实例不冲突 ✅（保持）。

---

## Phase 4：ChannelBotManager + app.py 接线

### Task 4.1：ChannelBotManager
**Files:** Create `src/tianshu/gateway/bot_manager.py`

- [ ] 持有共享 deps + 运行中 bot 字典：
```python
class ChannelBotManager:
    def __init__(self, *, storage, event_bus, approval_manager, executor,
                 notifier, persona_loader, provider_manager, cost_manager,
                 env_settings):
        self._deps = {...}
        self._bots: dict[str, object] = {}   # instance_id -> FeishuBot|TelegramBot

    def _build_instances(self) -> list[ChannelInstance]:
        """DB 实例优先；DB 无该渠道实例时，从 env 造一个 {channel}-default（向后兼容）。"""

    def _construct(self, inst: ChannelInstance):
        """按 channel_type 造 FeishuBot/TelegramBot，注入 instance_id + settings + deps。"""

    async def start_all(self) -> None:
        for inst in self._build_instances():
            if inst.enabled:
                await self.start_instance(inst)   # 单实例失败降级，不阻塞其它

    async def start_instance(self, inst) -> None: ...     # 构造 + start + 记入 _bots + webhook 挂载
    async def stop_instance(self, instance_id) -> None: ...
    async def reload_instance(self, instance_id, new_settings) -> None: ...  # 在线则 bot.reload；不在线则 start
    async def stop_all(self) -> None: ...
    def status(self) -> list[dict]: ...                   # 每实例 running/mode
```
- [ ] EventBus 订阅：每个 bot 的 outbound/approval 在 `start()` 内订阅一次；`stop_instance` 时需能退订（新增 `outbound.stop()/approval.stop()` 退订，避免 stop 后僵尸 handler 仍投递）。**这是新增需求**——补 `event_bus.off(name, handler)`（若无则加）。

### Task 4.2：app.py 用 manager 替换单例
**Files:** Modify `src/tianshu/app.py`

- [ ] 删除现有 feishu_bot / telegram_bot 单例块（371–409 / 425–441），替换为：
```python
from tianshu.gateway.bot_manager import ChannelBotManager
bot_manager = ChannelBotManager(
    storage=storage, event_bus=event_bus, approval_manager=approval_manager,
    executor=executor, notifier=notifier, persona_loader=persona_loader,
    provider_manager=provider_manager, cost_manager=cost_manager,
    env_settings=settings,
)
app.state.bot_manager = bot_manager
try:
    await bot_manager.start_all()
except Exception:
    logger.exception("[gateway] bot_manager start_all failed; web still up")
# webhook 模式实例的 router 挂载由 manager.start_instance 内部 app.include_router 完成
```
- [ ] shutdown：`await bot_manager.stop_all()`（替换原 feishu_bot/telegram_bot.stop）。
- [ ] webhook 路由：`start_instance` 内若 settings.connection_mode=="webhook" 调 `bot.attach_webhook_router(app)`（需把 `app` 传给 manager 或 start_instance）。

---

## Phase 5：tongzheng_api 实例 CRUD

### Task 5.1：实例列表/详情/增改删/状态端点
**Files:** Modify `src/tianshu/gateway/tongzheng_api.py`

- [ ] 新 Pydantic：`ChannelInstanceCreate`（channel_type, label, + 渠道配置字段 + secret）、`ChannelInstanceUpdate`。
- [ ] 端点：
```
GET    /tongzheng/instances                 列所有实例（secret 掩码 + running 状态）
GET    /tongzheng/instances/{id}            单实例
POST   /tongzheng/instances                 创建（生成 ULID id）→ save + manager.start_instance（在线生效）
PUT    /tongzheng/instances/{id}            改 → save + manager.reload_instance
PATCH  /tongzheng/instances/{id}/enabled    启/停 → manager.start/stop_instance
DELETE /tongzheng/instances/{id}            停 + 删
GET    /tongzheng/instances/{id}/status     running/mode
```
- [ ] 复用现有 `enable_edict_submission` → 工具集 toggle 逻辑（按实例聚合：任一实例开启即启用工具）。

### Task 5.2：旧端点保留为薄封装（向后兼容）
**Files:** Modify `src/tianshu/gateway/tongzheng_api.py`

- [ ] `GET/PUT /channels/feishu`、`/channels/telegram`、`/status` 改为读写对应 `{channel}-default` 实例，行为对老前端/脚本不变。

---

## Phase 6：前端 —— 实例列表 UI

### Task 6.1：API 客户端
**Files:** Modify `web/src/api/tongzheng.ts`

- [ ] 加 `ChannelInstance` 类型 + `listInstances/getInstance/createInstance/updateInstance/setInstanceEnabled/deleteInstance/getInstanceStatus`。

### Task 6.2：实例列表页
**Files:** Modify `web/src/pages/TongzhengPage.tsx`；改造 `web/src/components/config/{Feishu,Telegram}ChannelForm.tsx` 为「按实例编辑」（接 `instanceId` prop）

- [ ] 上方实例列表（每行：label、渠道、running 标签、启停开关、编辑/删除）；「+ 新增飞书实例 / + 新增 Telegram 实例」按钮 → 打开对应表单（复用现有飞书/Telegram 表单组件，提交走 create/update 实例端点）。
- [ ] i18n：`tongzheng.instances.*` 三语 key。

---

## Phase 7：测试（最后统一补）

### Task 7.1：实例隔离单测
**Files:** Create `tests/gateway/test_instance_isolation.py`
- [ ] 两个 telegram 实例 A/B：A 的 `_lookup_chat_id` 拒投 B 的敕令、接受自己的；旧敕令（无 instance_id）归 default。
- [ ] 同一 chat_id 在 A、B 各自 anchor 不串（复合键）。
- [ ] `list_edicts(instance_id=A)` 只返回 A 的敕令 + 旧 default 敕令；`instance_id=None` 返回全部。

### Task 7.2：ChannelBotManager 单测
**Files:** Create `tests/gateway/test_bot_manager.py`
- [ ] `_build_instances`：DB 有实例时用 DB；DB 空时从 env 造 default。
- [ ] start/stop/reload 改 `_bots` 字典；stop 后 EventBus handler 退订（fire 事件不再投递）。

### Task 7.3：迁移测试
**Files:** Create `tests/test_storage_instance_migration.py`
- [ ] 用旧 schema 建库塞数据 → `init_db` 迁移 → anchor 行 instance_id 变 `{channel}-default`、pending/seen 列已加、幂等（二次 init_db 不报错）。

### Task 7.4：回归
- [ ] `.venv/bin/python -m pytest tests/gateway -q` 全绿；扩充/修正现有 feishu/telegram 测试以传 instance_id。

### Task 7.5：文档
**Files:** Modify `docs/ops/telegram-setup.md` + 新增 `docs/ops/multi-bot.md`（如何加第二个 bot、隔离语义、web 看板全局可见说明）。

---

## 风险与权衡

1. **会话表 PK 迁移**（最大风险）：anchor 表重建迁移；务必幂等（PRAGMA 检测 instance_id 列）。失败会丢锚（用户下条消息重新 ensure，影响可控）。建议迁移前备份 `~/.tianshu/*.db`。
2. **EventBus 退订**：`EventBus.off(event_type, handler)` **已存在**（`bus/event_bus.py:90`）。只需给 outbound/approval 各加 `stop()` 持有并 `off` 自己的 handler 引用，多实例 stop 即可干净退订（小改动，无需新增 EventBus 能力）。
3. **运行时新增实例**：POST 后 `manager.start_instance` 在线拉起——比旧「重启生效」更好，但要保证依赖齐全（manager 持有全部 deps）。
4. **旧敕令归属**：无 instance_id 的历史敕令统一归 `{channel}-default`，由 default 实例投递/列出；若用户把 default 删了，这些旧敕令的回执将无出口（方案：禁止删除 default，或删除时提示）。
5. **web 看板保持全局**：`GET /api/edicts` 不传 instance_id（已确认），运维可见全部；仅聊天侧 `/list` 隔离。

## 工作量评估
- 后端：storage（表+迁移+方法签名扩散）、bot 核心 instance_id 贯穿（飞书+telegram 对称）、manager、API —— 约 12–15 个文件。
- 前端：实例列表页 + 表单参数化 —— 3 个文件。
- 比本次 telegram 接入略大（主要是 instance_id 要贯穿所有会话/路由调用点 + 一次表迁移）。

## Self-Review
- [x] 多实例配置（channel_instances）+ 运行（manager dict）—— Phase 1/4
- [x] 每实例独立 persona/allowlist/home —— settings 已含，实例各自一份 config
- [x] 隔离：会话(复合键)/审批(instance 列)/出站(instance 路由)/`/list`(instance 过滤) —— Phase 1/3
- [x] web 看板全局 —— list_edicts instance_id 默认 None
- [x] 向后兼容：env→default 实例、旧端点薄封装、旧敕令归 default、迁移幂等 —— Phase 1/2/4/5
- [x] 运行时增删 + 解决首次启用需重启 —— Phase 4 manager
- [x] 测试 + 迁移测试 + 文档 —— Phase 7
- [x] 风险点显式列出（PK 迁移 / EventBus 退订 / default 不可删）
