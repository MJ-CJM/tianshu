# 飞书机器人 v1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 tianshu 中接入飞书机器人，作为第二入口（飞书消息 → 创建/续接敕令）和第二审批通道（卡片审批 + web 双通道并行），实现方式与 hermes-agent 协议层对齐。

**Architecture:** 在 `src/tianshu/gateway/feishu/` 新建薄壳子包 9 个文件（每文件 < 400 行），通过 lark-oapi SDK 提供 WebSocket / Webhook 双连接模式，复用现有 EventBus / Storage / ApprovalManager / Notifier，不引入 hermes 的 BasePlatformAdapter 跨平台抽象。

**Tech Stack:** Python 3.11+ / FastAPI / SQLite / lark-oapi / httpx / pytest / pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-28-feishu-bot-design.md`

**用户偏好（来自 memory）：** 功能优先，测试最后补 —— Step 1-7 先实现可手工验证的功能；Step 8 统一补齐测试到 80%+ 覆盖。

**真实接口对照（spec 抽象 → tianshu 实际）：**
- "approval.requested" 事件 → 实际是 `tool.approval_required`
- "approval.resolved" 事件 → 实际是 `decree.approved` / `decree.rejected`
- 审批接口 → `ApprovalManager.submit_tool_decision(memorial_id, action, grant_scope, actor)`
- 飞书来源敕令 → `Edict.source = "channel"` + `Edict.metadata = {"chat_id": ..., "feishu_user": ...}`

---

## File Structure

```
src/tianshu/gateway/feishu/
├── __init__.py            FeishuBot 工厂 + 公共导出
├── settings.py            Pydantic Settings 子集 + 启动校验
├── connection.py          WebSocket (lark-oapi) + Webhook (FastAPI router) 双连接，共享 InboundQueue
├── dispatcher.py          入站流水线：security → group_gate → batcher → command_parser → router
├── session_anchor.py      会话锚 (chat_id → current_edict_id) 的 SQLite CRUD
├── edict_bridge.py        FeishuMessage → Edict / FollowUp（含已结案自动新建）
├── approval_card.py       出站卡片 + 入站 card.action.trigger → submit_tool_decision
├── outbound.py            EventBus 订阅 + 飞书消息发送 + Markdown→post 自动转 + fallback
└── security.py            signature/token verify + allowlist + dedup + 限流

修改：
- src/tianshu/config.py             新增 feishu_* settings
- src/tianshu/storage.py            _migrate 列表追加 3 张表 + 加方法
- src/tianshu/app.py                lifespan 接入 FeishuBot
- src/tianshu/notifier/channels/feishu.py  保留 + 标记 deprecated
- pyproject.toml                    新增 lark-oapi 依赖
- web/src/hooks/useWsPolicyToasts.ts  收到 decree.approved/rejected 时关闭 tool.approval_required toast

新增测试：
tests/gateway/feishu/
├── test_settings.py
├── test_security.py
├── test_session_anchor.py
├── test_dispatcher.py
├── test_edict_bridge.py
├── test_approval_card.py
├── test_outbound.py
└── test_e2e_webhook.py

新增文档：
- docs/ops/feishu-setup.md          用户配置指南
```

---

## Step 1: 配置层 + 启动壳（最小骨架）

**目标：** 启动可加载新模块但不影响现有部署（feishu_app_id 为空时跳过）。

### Task 1.1: 新增 settings 字段

**Files:**
- Modify: `src/tianshu/config.py`

- [ ] **Step 1: 在 `TianshuSettings` 类追加飞书字段**

```python
# 文件：src/tianshu/config.py
# 在已有 feishu_webhook 行下方追加（保留 feishu_webhook 兼容旧部署）

# Feishu Bot —— inbound + outbound (与 hermes 同名同义)
feishu_app_id: str = ""                       # 空 → 不启用机器人（向后兼容）
feishu_app_secret: str = ""
feishu_domain: str = "feishu"                 # feishu | lark
feishu_connection_mode: str = "websocket"     # websocket | webhook
feishu_allowed_users: str = ""                # 逗号分隔 open_id
feishu_home_channel: str = ""                 # cron 结果 / 无源审批兜底 chat_id
feishu_encrypt_key: str = ""                  # webhook 模式签名密钥
feishu_verification_token: str = ""           # webhook 模式 token 校验
feishu_bot_open_id: str = ""                  # 群 @ 检测
feishu_bot_name: str = ""                     # 群 @ 检测兜底
feishu_webhook_path: str = "/feishu/webhook"
feishu_ws_reconnect_interval: int = 120
feishu_text_batch_delay: float = 0.6
feishu_dedup_cache_size: int = 2048
```

- [ ] **Step 2: 验证字段读取**

```bash
TIANSHU_FEISHU_APP_ID=test_app uv run python -c "from tianshu.config import TianshuSettings; print(TianshuSettings().feishu_app_id, TianshuSettings().feishu_connection_mode)"
```
Expected: `test_app websocket`

- [ ] **Step 3: 提交**
```bash
git add src/tianshu/config.py
git commit -m "feat(feishu): 新增 feishu_* settings 字段"
```

### Task 1.2: 安装依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 在 `[project.dependencies]` 追加 lark-oapi**

```toml
# pyproject.toml dependencies 段，与 fastapi/httpx 同级
"lark-oapi>=1.4.0",
```

- [ ] **Step 2: 同步依赖**

```bash
uv sync
uv run python -c "import lark_oapi; print(lark_oapi.__version__)"
```
Expected: 打印版本号，无错误

- [ ] **Step 3: 提交**
```bash
git add pyproject.toml uv.lock
git commit -m "chore: 添加 lark-oapi 依赖"
```

### Task 1.3: 新增子包骨架 + FeishuBot 工厂

**Files:**
- Create: `src/tianshu/gateway/feishu/__init__.py`
- Create: `src/tianshu/gateway/feishu/settings.py`

- [ ] **Step 1: `__init__.py` 暴露 FeishuBot 工厂**

```python
# 文件：src/tianshu/gateway/feishu/__init__.py
"""Feishu (Lark) 机器人接入：双向入口 + 双通道审批。

设计文档：docs/superpowers/specs/2026-04-28-feishu-bot-design.md
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.notifier.notifier import Notifier
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class FeishuBot:
    """飞书机器人门面 —— 协调 connection / dispatcher / outbound。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        event_bus: "EventBus",
        approval_manager: "ApprovalManager",
        notifier: "Notifier",
        settings: "FeishuSettings",
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._approval_manager = approval_manager
        self._notifier = notifier
        self._settings = settings
        # 后续 step 会在这里挂 connection / dispatcher / outbound

    async def start(self) -> None:
        """生命周期启动：启动连接 + 注册事件订阅 + 初始化 anchor 表。"""
        logger.info("[feishu] starting bot (mode=%s, app=%s)",
                    self._settings.connection_mode, self._settings.app_id)
        # Step 2 起补全

    async def stop(self) -> None:
        logger.info("[feishu] stopping bot")
        # Step 6 起补全


__all__ = ["FeishuBot"]
```

- [ ] **Step 2: `settings.py` 局部值对象 + 启动校验**

```python
# 文件：src/tianshu/gateway/feishu/settings.py
"""FeishuSettings：从全局 TianshuSettings 抽取飞书相关字段，附启动校验。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeishuSettings:
    app_id: str
    app_secret: str
    domain: str
    connection_mode: str
    allowed_users: tuple[str, ...]
    home_channel: str
    encrypt_key: str
    verification_token: str
    bot_open_id: str
    bot_name: str
    webhook_path: str
    ws_reconnect_interval: int
    text_batch_delay: float
    dedup_cache_size: int

    @property
    def enabled(self) -> bool:
        """app_id 为空 → 整个机器人不启用，保持向后兼容。"""
        return bool(self.app_id)

    def validate_or_raise(self) -> None:
        """启动检查：v1 单人模式必须配 allowlist 避免误开放。"""
        if not self.enabled:
            return
        if not self.app_secret:
            raise RuntimeError("TIANSHU_FEISHU_APP_SECRET is required when app_id is set")
        if not self.allowed_users:
            raise RuntimeError(
                "TIANSHU_FEISHU_ALLOWED_USERS is required (avoid accidentally exposing the bot)"
            )
        if self.connection_mode not in ("websocket", "webhook"):
            raise RuntimeError(f"invalid connection_mode: {self.connection_mode}")
        if self.domain not in ("feishu", "lark"):
            raise RuntimeError(f"invalid domain: {self.domain}")


def from_global_settings(s) -> FeishuSettings:
    """从 TianshuSettings 构造 FeishuSettings。"""
    allowed = tuple(u.strip() for u in (s.feishu_allowed_users or "").split(",") if u.strip())
    return FeishuSettings(
        app_id=s.feishu_app_id,
        app_secret=s.feishu_app_secret,
        domain=s.feishu_domain,
        connection_mode=s.feishu_connection_mode,
        allowed_users=allowed,
        home_channel=s.feishu_home_channel,
        encrypt_key=s.feishu_encrypt_key,
        verification_token=s.feishu_verification_token,
        bot_open_id=s.feishu_bot_open_id,
        bot_name=s.feishu_bot_name,
        webhook_path=s.feishu_webhook_path,
        ws_reconnect_interval=s.feishu_ws_reconnect_interval,
        text_batch_delay=s.feishu_text_batch_delay,
        dedup_cache_size=s.feishu_dedup_cache_size,
    )
```

- [ ] **Step 3: 提交**
```bash
git add src/tianshu/gateway/feishu/__init__.py src/tianshu/gateway/feishu/settings.py
git commit -m "feat(feishu): 子包骨架 + FeishuSettings"
```

### Task 1.4: 接入 lifespan

**Files:**
- Modify: `src/tianshu/app.py`（在 ChannelRegistry 之后、Notifier 之前的合适位置）

- [ ] **Step 1: 在 lifespan 里构造 FeishuBot（暂只 log，不启动连接）**

定位 `app.py` 中 `# --- ChannelRegistry ---` 段下方（约 220 行附近）：

```python
# 文件：src/tianshu/app.py
# 在 channel_registry 注册完毕之后追加：

# --- Feishu Bot ---
from tianshu.gateway.feishu import FeishuBot
from tianshu.gateway.feishu.settings import from_global_settings as build_feishu_settings

feishu_settings = build_feishu_settings(settings)
feishu_settings.validate_or_raise()

feishu_bot: FeishuBot | None = None
if feishu_settings.enabled:
    feishu_bot = FeishuBot(
        storage=storage,
        event_bus=event_bus,
        approval_manager=approval_manager,  # 后续 step 注入；此处假设它在前文已构造
        notifier=notifier,
        settings=feishu_settings,
    )
    await feishu_bot.start()
    app.state.feishu_bot = feishu_bot
```

⚠️ **注意接入位置**：`approval_manager` 与 `notifier` 必须在 FeishuBot 之前构造完成。检查 `app.py` 现有顺序，必要时调整 FeishuBot 的代码位置到 `notifier` 实例化之后。

- [ ] **Step 2: 在 lifespan 关闭段调用 `feishu_bot.stop()`**

```python
# 文件：src/tianshu/app.py，lifespan 的 finally / shutdown 段
if feishu_bot is not None:
    await feishu_bot.stop()
```

- [ ] **Step 3: 验证现有部署不受影响**

```bash
# 不设 FEISHU_APP_ID 时启动应用 → 不应有任何飞书相关 log，全部既有测试通过
TIANSHU_DB_PATH=/tmp/test.db uv run pytest tests/test_gateway.py -v
```
Expected: 全部通过

- [ ] **Step 4: 验证 enabled 模式启动**

```bash
TIANSHU_FEISHU_APP_ID=test \
TIANSHU_FEISHU_APP_SECRET=secret \
TIANSHU_FEISHU_ALLOWED_USERS=ou_test \
TIANSHU_DB_PATH=/tmp/test.db \
uv run python -c "
import asyncio
from tianshu.app import create_app
app = create_app()
async def go():
    async with app.router.lifespan_context(app):
        print('feishu_bot=', getattr(app.state, 'feishu_bot', None))
asyncio.run(go())
"
```
Expected: 打印 `feishu_bot= <FeishuBot ...>` 且日志含 `[feishu] starting bot`

- [ ] **Step 5: 提交**
```bash
git add src/tianshu/app.py
git commit -m "feat(feishu): lifespan 接入 FeishuBot 骨架"
```

---

## Step 2: SQLite 表迁移 + 安全过滤 + Webhook 模式

**目标：** 启动 webhook 端点 → 验签 → 通过 allowlist → 经流水线（暂时只 echo "received: <text>"）。能用 curl 模拟飞书事件并看到日志。

### Task 2.1: SQLite 表迁移

**Files:**
- Modify: `src/tianshu/storage.py`

- [ ] **Step 1: 在 `Storage.__init__` 的 `CREATE TABLE` 段追加 3 张表**

定位 `storage.py` 中现有 `CREATE TABLE` 段尾部（约 380 行附近，最后一个 supervision_reports 之后）：

```python
# 文件：src/tianshu/storage.py
# 在 supervision_reports CREATE INDEX 之后追加：

self._conn.executescript("""
    CREATE TABLE IF NOT EXISTS feishu_session_anchor (
        chat_id          TEXT PRIMARY KEY,
        current_edict_id TEXT,
        updated_at       TIMESTAMP NOT NULL
    );
    CREATE TABLE IF NOT EXISTS feishu_seen_messages (
        message_id  TEXT PRIMARY KEY,
        seen_at     TIMESTAMP NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_feishu_seen_at ON feishu_seen_messages(seen_at);
    CREATE TABLE IF NOT EXISTS feishu_pending_cards (
        approval_id TEXT PRIMARY KEY,    -- 等于 memorial_id（v1 复用 memorial_id 作 approval 标识）
        chat_id     TEXT NOT NULL,
        message_id  TEXT NOT NULL,
        kind        TEXT NOT NULL,       -- tool.approval_required | plan.review | outer_loop
        created_at  TIMESTAMP NOT NULL
    );
""")
```

- [ ] **Step 2: 在 `Storage` 类追加方法（放在 `find_edict_by_idempotency_key` 附近）**

```python
# 文件：src/tianshu/storage.py
# 追加到 Storage 类内：

# --- Feishu session anchor ---

def get_feishu_anchor(self, chat_id: str) -> str | None:
    """返回当前 chat 绑定的 edict_id（None 表示未绑定）。"""
    row = self._conn.execute(
        "SELECT current_edict_id FROM feishu_session_anchor WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    return row[0] if row else None

def set_feishu_anchor(self, chat_id: str, edict_id: str) -> None:
    from datetime import datetime, UTC
    self._conn.execute(
        "INSERT INTO feishu_session_anchor (chat_id, current_edict_id, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET current_edict_id = excluded.current_edict_id, "
        "    updated_at = excluded.updated_at",
        (chat_id, edict_id, datetime.now(UTC).isoformat()),
    )
    self._conn.commit()

# --- Feishu dedup ---

def is_feishu_message_seen(self, message_id: str) -> bool:
    row = self._conn.execute(
        "SELECT 1 FROM feishu_seen_messages WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    return row is not None

def mark_feishu_message_seen(self, message_id: str, max_entries: int = 2048) -> None:
    from datetime import datetime, UTC
    now = datetime.now(UTC).isoformat()
    self._conn.execute(
        "INSERT OR IGNORE INTO feishu_seen_messages (message_id, seen_at) VALUES (?, ?)",
        (message_id, now),
    )
    # LRU evict：超出 max_entries 时删最早的
    self._conn.execute(
        "DELETE FROM feishu_seen_messages WHERE message_id IN ("
        "  SELECT message_id FROM feishu_seen_messages ORDER BY seen_at ASC "
        "  LIMIT MAX(0, (SELECT COUNT(*) FROM feishu_seen_messages) - ?))",
        (max_entries,),
    )
    self._conn.commit()

# --- Feishu pending cards (Step 5) ---

def save_feishu_pending_card(self, approval_id: str, chat_id: str, message_id: str, kind: str) -> None:
    from datetime import datetime, UTC
    self._conn.execute(
        "INSERT OR REPLACE INTO feishu_pending_cards (approval_id, chat_id, message_id, kind, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (approval_id, chat_id, message_id, kind, datetime.now(UTC).isoformat()),
    )
    self._conn.commit()

def pop_feishu_pending_card(self, approval_id: str) -> dict | None:
    row = self._conn.execute(
        "SELECT chat_id, message_id, kind FROM feishu_pending_cards WHERE approval_id = ?",
        (approval_id,),
    ).fetchone()
    if not row:
        return None
    self._conn.execute(
        "DELETE FROM feishu_pending_cards WHERE approval_id = ?", (approval_id,),
    )
    self._conn.commit()
    return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}
```

- [ ] **Step 3: 验证迁移在新旧 DB 都能成功**

```bash
# 新 DB
rm -f /tmp/migr_test.db
uv run python -c "
from tianshu.storage import Storage
s = Storage('/tmp/migr_test.db')
s.init_db()
print(s.get_feishu_anchor('chat_x'))  # None
s.set_feishu_anchor('chat_x', 'ed_1')
print(s.get_feishu_anchor('chat_x'))  # ed_1
s.mark_feishu_message_seen('msg_1')
print(s.is_feishu_message_seen('msg_1'))  # True
"
```
Expected: 输出 `None / ed_1 / True`

- [ ] **Step 4: 提交**
```bash
git add src/tianshu/storage.py
git commit -m "feat(feishu): 新增 session_anchor / seen_messages / pending_cards 表与方法"
```

### Task 2.2: security.py — 验签 / token / allowlist / dedup

**Files:**
- Create: `src/tianshu/gateway/feishu/security.py`

- [ ] **Step 1: 写完整 security 模块**

```python
# 文件：src/tianshu/gateway/feishu/security.py
"""Feishu webhook 安全：签名 / token / allowlist / dedup。"""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from tianshu.storage import Storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecurityConfig:
    encrypt_key: str
    verification_token: str
    allowed_users: frozenset[str]
    dedup_cache_size: int


def verify_signature(headers: dict[str, str], body_bytes: bytes, encrypt_key: str) -> bool:
    """SHA256(timestamp + nonce + encrypt_key + body) == X-Lark-Signature。

    encrypt_key 为空时跳过校验（dev 模式）。
    """
    if not encrypt_key:
        return True
    timestamp = headers.get("x-lark-request-timestamp") or headers.get("X-Lark-Request-Timestamp", "")
    nonce = headers.get("x-lark-request-nonce") or headers.get("X-Lark-Request-Nonce", "")
    expected_sig = headers.get("x-lark-signature") or headers.get("X-Lark-Signature", "")
    if not (timestamp and nonce and expected_sig):
        return False
    payload = f"{timestamp}{nonce}{encrypt_key}".encode() + body_bytes
    actual = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(actual, expected_sig)


def verify_token(payload: dict, expected_token: str) -> bool:
    """检查 payload['header']['token'] == expected_token。空 token 跳过校验。"""
    if not expected_token:
        return True
    actual = (payload.get("header") or {}).get("token", "") or payload.get("token", "")
    return hmac.compare_digest(actual, expected_token)


def is_allowed_user(open_id: str, allowed: Iterable[str]) -> bool:
    return open_id in set(allowed)


class DedupChecker:
    """基于 SQLite 的消息 ID 去重。"""

    def __init__(self, storage: Storage, max_entries: int = 2048) -> None:
        self._storage = storage
        self._max = max_entries

    def check_and_mark(self, message_id: str) -> bool:
        """True = 首见（处理）；False = 重复（丢弃）。"""
        if not message_id:
            return True  # 没 message_id 不去重
        if self._storage.is_feishu_message_seen(message_id):
            logger.debug("[feishu/dedup] dropped duplicate message_id=%s", message_id)
            return False
        self._storage.mark_feishu_message_seen(message_id, max_entries=self._max)
        return True
```

- [ ] **Step 2: 手工验证 verify_signature**

```bash
uv run python -c "
from tianshu.gateway.feishu.security import verify_signature
import hashlib
key = 'k123'
body = b'{\"ok\": 1}'
ts = '1700000000'
nonce = 'abc'
sig = hashlib.sha256(f'{ts}{nonce}{key}'.encode() + body).hexdigest()
headers = {'x-lark-request-timestamp': ts, 'x-lark-request-nonce': nonce, 'x-lark-signature': sig}
print('ok=', verify_signature(headers, body, key))
print('bad=', verify_signature({**headers, 'x-lark-signature': 'xx'}, body, key))
"
```
Expected: `ok= True` 与 `bad= False`

- [ ] **Step 3: 提交**
```bash
git add src/tianshu/gateway/feishu/security.py
git commit -m "feat(feishu): security — signature/token/allowlist/dedup"
```

### Task 2.3: connection.py — Webhook 模式骨架

**Files:**
- Create: `src/tianshu/gateway/feishu/connection.py`

- [ ] **Step 1: 写 WebhookConnection（FastAPI 子路由 + InboundQueue）**

```python
# 文件：src/tianshu/gateway/feishu/connection.py
"""Feishu 连接层：WebSocket (Step 6) + Webhook (本步)。共享 inbound_queue。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Protocol

from fastapi import APIRouter, Request, Response

from tianshu.gateway.feishu.security import DedupChecker, verify_signature, verify_token
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class FeishuConnection(Protocol):
    inbound_queue: asyncio.Queue
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class WebhookConnection:
    """Webhook 模式：挂到 FastAPI router 上。POST {webhook_path}。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        inbound_queue: asyncio.Queue,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self.inbound_queue = inbound_queue
        self._dedup = DedupChecker(storage, max_entries=settings.dedup_cache_size)
        self.router = APIRouter()
        self.router.post(settings.webhook_path)(self._handle_request)

    async def _handle_request(self, request: Request) -> Response:
        # 1. 限流（Step 7 实现，先 stub 直通）
        # 2. body 限制 1MB
        body_bytes = await request.body()
        if len(body_bytes) > 1024 * 1024:
            return Response("body too large", status_code=413)

        # 3. 验签
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not verify_signature(headers, body_bytes, self._settings.encrypt_key):
            return Response("invalid signature", status_code=401)

        try:
            payload = json.loads(body_bytes)
        except Exception:
            return Response("bad json", status_code=400)

        # 4. url_verification 自动响应
        if payload.get("type") == "url_verification":
            return Response(
                content=json.dumps({"challenge": payload.get("challenge", "")}),
                media_type="application/json",
            )

        # 5. token 校验
        if not verify_token(payload, self._settings.verification_token):
            return Response("invalid token", status_code=401)

        # 6. dedup（基于 header.event_id）
        event_id = ((payload.get("header") or {}).get("event_id")) or ""
        if event_id and not self._dedup.check_and_mark(event_id):
            return Response("ok", status_code=200)  # 静默吃掉重复

        # 7. 入队 → dispatcher 消费
        await self.inbound_queue.put(payload)
        return Response("ok", status_code=200)

    async def start(self) -> None:
        logger.info("[feishu/webhook] route registered at %s", self._settings.webhook_path)

    async def stop(self) -> None:
        pass
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/connection.py
git commit -m "feat(feishu): WebhookConnection 骨架（验签/token/url_verification/dedup）"
```

### Task 2.4: dispatcher.py — 流水线骨架（暂只 echo）

**Files:**
- Create: `src/tianshu/gateway/feishu/dispatcher.py`

- [ ] **Step 1: 写 dispatcher（含群 @ 网关、命令解析占位）**

```python
# 文件：src/tianshu/gateway/feishu/dispatcher.py
"""入站流水线：security → group_gate → batcher → command → router。"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tianshu.gateway.feishu.security import is_allowed_user
from tianshu.gateway.feishu.settings import FeishuSettings

logger = logging.getLogger(__name__)


@dataclass
class FeishuMessage:
    """归一化后的入站消息。"""
    event_id: str
    chat_id: str
    chat_type: str          # p2p | group
    sender_open_id: str
    text: str               # 已合并/解析后的纯文本
    raw: dict               # 原始 event payload，供 approval_card 等使用


@dataclass
class FeishuCardAction:
    """卡片按钮点击。"""
    event_id: str
    chat_id: str
    sender_open_id: str
    value: dict             # {"approval_id": ..., "choice": ...}


class Dispatcher:
    """消费 inbound_queue，分流到 message handler / card handler。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        inbound_queue: asyncio.Queue,
        message_handler: Callable[[FeishuMessage], Awaitable[None]],
        card_handler: Callable[[FeishuCardAction], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._queue = inbound_queue
        self._message_handler = message_handler
        self._card_handler = card_handler
        self._task: asyncio.Task | None = None
        self._chat_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _consume_loop(self) -> None:
        while True:
            payload = await self._queue.get()
            try:
                await self._dispatch(payload)
            except Exception:
                logger.exception("[feishu/dispatcher] failed payload=%.300s", json.dumps(payload)[:300])

    async def _dispatch(self, payload: dict) -> None:
        header = payload.get("header") or {}
        event_type = header.get("event_type", "")
        event_id = header.get("event_id", "")
        event = payload.get("event") or {}

        if event_type == "im.message.receive_v1":
            await self._handle_message_event(event_id, event)
        elif event_type == "card.action.trigger":
            await self._handle_card_event(event_id, event)
        else:
            logger.debug("[feishu/dispatcher] ignored event_type=%s", event_type)

    async def _handle_message_event(self, event_id: str, event: dict) -> None:
        msg = (event.get("message") or {})
        sender = (event.get("sender") or {}).get("sender_id") or {}
        sender_open_id = sender.get("open_id", "")
        chat_id = msg.get("chat_id", "")
        chat_type = msg.get("chat_type", "p2p")  # p2p | group

        # allowlist
        if not is_allowed_user(sender_open_id, self._settings.allowed_users):
            logger.info("[feishu/inbound] rejected non-allowlist sender=%s", sender_open_id)
            return  # 静默丢弃

        # 群 @ 网关
        mentions = msg.get("mentions") or []
        if chat_type == "group" and not self._is_bot_mentioned(mentions):
            return

        text = self._extract_text(msg)
        if not text:
            return

        fmsg = FeishuMessage(
            event_id=event_id, chat_id=chat_id, chat_type=chat_type,
            sender_open_id=sender_open_id, text=text, raw=event,
        )

        # per-chat 串行
        lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            await self._message_handler(fmsg)

    async def _handle_card_event(self, event_id: str, event: dict) -> None:
        action = event.get("action") or {}
        value = action.get("value") or {}
        operator = event.get("operator") or {}
        sender_open_id = operator.get("open_id", "")
        if not is_allowed_user(sender_open_id, self._settings.allowed_users):
            return
        chat_id = (event.get("context") or {}).get("open_chat_id", "")
        await self._card_handler(FeishuCardAction(
            event_id=event_id, chat_id=chat_id,
            sender_open_id=sender_open_id, value=value,
        ))

    def _is_bot_mentioned(self, mentions: list[dict]) -> bool:
        bot_id = self._settings.bot_open_id
        bot_name = self._settings.bot_name
        for m in mentions:
            mid = (m.get("id") or {}).get("open_id", "")
            mname = m.get("name", "")
            if bot_id and mid == bot_id:
                return True
            if bot_name and mname == bot_name:
                return True
        return False

    @staticmethod
    def _extract_text(msg: dict) -> str:
        """从 Feishu message.content 提取文本。"""
        content_str = msg.get("content", "")
        try:
            content = json.loads(content_str)
        except Exception:
            return ""
        msg_type = msg.get("message_type", "")
        if msg_type == "text":
            return (content.get("text") or "").strip()
        if msg_type == "post":
            # post = 富文本，简化提取所有 text 节点
            lines = []
            for row in content.get("content", []):
                line = "".join(seg.get("text", "") for seg in row if seg.get("tag") == "text")
                if line:
                    lines.append(line)
            return "\n".join(lines).strip()
        return ""
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/dispatcher.py
git commit -m "feat(feishu): dispatcher 流水线骨架（allowlist/群@网关/per-chat 串行）"
```

### Task 2.5: 串接 FeishuBot.start() + echo handler

**Files:**
- Modify: `src/tianshu/gateway/feishu/__init__.py`
- Modify: `src/tianshu/app.py`（注入 FeishuBot 之后挂 webhook router）

- [ ] **Step 1: `FeishuBot.start()` 实例化连接 + dispatcher，挂 echo handler**

```python
# 文件：src/tianshu/gateway/feishu/__init__.py
# 完整重写：

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.connection import WebhookConnection
from tianshu.gateway.feishu.dispatcher import Dispatcher, FeishuCardAction, FeishuMessage
from tianshu.gateway.feishu.settings import FeishuSettings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from tianshu.bus.event_bus import EventBus
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.notifier.notifier import Notifier
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class FeishuBot:
    def __init__(
        self,
        *,
        storage: "Storage",
        event_bus: "EventBus",
        approval_manager: "ApprovalManager",
        notifier: "Notifier",
        settings: FeishuSettings,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._approval_manager = approval_manager
        self._notifier = notifier
        self._settings = settings
        self._inbound: asyncio.Queue = asyncio.Queue()
        self._connection: WebhookConnection | None = None
        self._dispatcher: Dispatcher | None = None

    async def start(self) -> None:
        logger.info("[feishu] starting (mode=%s, app=%s)",
                    self._settings.connection_mode, self._settings.app_id)
        if self._settings.connection_mode == "webhook":
            self._connection = WebhookConnection(
                settings=self._settings, storage=self._storage,
                inbound_queue=self._inbound,
            )
        else:
            # WebSocket 模式 Step 6 实现
            raise NotImplementedError("websocket mode 待 Step 6 实现")
        await self._connection.start()

        self._dispatcher = Dispatcher(
            settings=self._settings, inbound_queue=self._inbound,
            message_handler=self._on_message,
            card_handler=self._on_card,
        )
        await self._dispatcher.start()

    async def stop(self) -> None:
        logger.info("[feishu] stopping")
        if self._dispatcher:
            await self._dispatcher.stop()
        if self._connection:
            await self._connection.stop()

    def attach_webhook_router(self, app: "FastAPI") -> None:
        """Webhook 模式：把路由挂到 FastAPI app。Step 1.4 之外的额外步骤。"""
        if self._connection and isinstance(self._connection, WebhookConnection):
            app.include_router(self._connection.router)

    async def _on_message(self, msg: FeishuMessage) -> None:
        logger.info("[feishu/inbound] chat=%s sender=%s text=%.80s",
                    msg.chat_id, msg.sender_open_id, msg.text)
        # Step 3 起接 edict_bridge

    async def _on_card(self, action: FeishuCardAction) -> None:
        logger.info("[feishu/card] chat=%s value=%s", action.chat_id, action.value)
        # Step 5 起接 approval_card
```

- [ ] **Step 2: 在 `app.py` lifespan 调用 `attach_webhook_router(app)`**

```python
# 文件：src/tianshu/app.py
# 紧接 await feishu_bot.start() 之后追加：
if feishu_settings.connection_mode == "webhook":
    feishu_bot.attach_webhook_router(app)
```

- [ ] **Step 3: 端到端手工验证**

```bash
# 终端 1：启动 server（webhook 模式）
TIANSHU_FEISHU_APP_ID=test \
TIANSHU_FEISHU_APP_SECRET=secret \
TIANSHU_FEISHU_ALLOWED_USERS=ou_test \
TIANSHU_FEISHU_CONNECTION_MODE=webhook \
TIANSHU_DB_PATH=/tmp/feishu_test.db \
uv run uvicorn tianshu.app:create_app --factory --port 8000

# 终端 2：发模拟事件
curl -X POST http://localhost:8000/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev1"},
  "event": {
    "sender": {"sender_id": {"open_id": "ou_test"}},
    "message": {
      "chat_id": "oc_x", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"hello\"}"
    }
  }
}'
```

Expected: 终端 1 日志含 `[feishu/inbound] chat=oc_x sender=ou_test text=hello`

- [ ] **Step 4: 验证 url_verification challenge**

```bash
curl -X POST http://localhost:8000/feishu/webhook -H 'Content-Type: application/json' \
  -d '{"type": "url_verification", "challenge": "ch123"}'
```
Expected: 响应 `{"challenge": "ch123"}`

- [ ] **Step 5: 验证 allowlist 拒绝**

```bash
curl -X POST http://localhost:8000/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev2"},
  "event": {"sender": {"sender_id": {"open_id": "ou_BAD"}},
    "message": {"chat_id": "oc_x", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"hi\"}"}}}'
```
Expected: 服务返回 200，但日志含 `rejected non-allowlist sender=ou_BAD`，无 inbound 日志

- [ ] **Step 6: 提交**
```bash
git add src/tianshu/gateway/feishu/__init__.py src/tianshu/app.py
git commit -m "feat(feishu): Webhook 模式端到端打通（echo handler）"
```

---

## Step 3: 会话锚 + 入口型（A）—— 创建/续接敕令

**目标：** 飞书消息 → 创建/续接 Edict，写入 Storage，发射 `edict.submitted` 事件。

### Task 3.1: session_anchor.py 薄封装

**Files:**
- Create: `src/tianshu/gateway/feishu/session_anchor.py`

- [ ] **Step 1**

```python
# 文件：src/tianshu/gateway/feishu/session_anchor.py
"""Feishu 会话锚 (chat_id → current_edict_id) 的薄封装。"""
from __future__ import annotations

from tianshu.storage import Storage


class SessionAnchor:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def get(self, chat_id: str) -> str | None:
        return self._storage.get_feishu_anchor(chat_id)

    def set(self, chat_id: str, edict_id: str) -> None:
        self._storage.set_feishu_anchor(chat_id, edict_id)
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/session_anchor.py
git commit -m "feat(feishu): SessionAnchor 薄封装"
```

### Task 3.2: edict_bridge.py — FeishuMessage → Edict

**Files:**
- Create: `src/tianshu/gateway/feishu/edict_bridge.py`

- [ ] **Step 1: 写 EdictBridge（含 X1 已结案自动新建分支）**

```python
# 文件：src/tianshu/gateway/feishu/edict_bridge.py
"""把飞书消息桥接到 tianshu 敕令模型。

决策：
- 默认续接当前 chat 锚定的 Edict（follow_up）
- /new <goal> → 显式新建并更新锚
- 子决策 X1：anchor 指向的 Edict 已结案 → 自动新建（无感）
"""
from __future__ import annotations

import logging

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.session_anchor import SessionAnchor
from tianshu.models.common import EdictStatus, TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.events import make_event
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

CLOSED_STATES = {EdictStatus.COMPLETED, EdictStatus.FAILED, EdictStatus.CANCELLED}


class EdictBridge:
    def __init__(
        self,
        *,
        storage: Storage,
        event_bus: EventBus,
        anchor: SessionAnchor,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._anchor = anchor

    async def continue_or_create(self, *, chat_id: str, sender_open_id: str, text: str) -> str:
        """主入口。返回最终绑定的 edict_id。"""
        current_edict_id = self._anchor.get(chat_id)
        if current_edict_id:
            edict = self._storage.get_edict(current_edict_id)
            if edict and edict.status not in CLOSED_STATES:
                await self._follow_up(edict, text, sender_open_id)
                return edict.id
            # X1: 已结案 → 自动新建（无感）
            logger.info("[feishu/edict] anchor edict %s closed (status=%s), auto-new",
                        current_edict_id, edict.status if edict else "missing")
        return await self.create_new(chat_id=chat_id, sender_open_id=sender_open_id, goal=text)

    async def create_new(self, *, chat_id: str, sender_open_id: str, goal: str) -> str:
        """显式新建（来自 /new 或自动新建）。"""
        title = goal[:20] + ("…" if len(goal) > 20 else "")
        edict = Edict(
            title=title, goal=goal,
            source="channel",
            submitter="emperor",  # v1 单人固定
            metadata={
                "channel": "feishu",
                "chat_id": chat_id,
                "feishu_user": sender_open_id,
            },
        )
        self._storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.SUBMITTED)
        self._storage.save_memorial(memorial)
        self._anchor.set(chat_id, edict.id)
        self._event_bus.fire(make_event(
            "edict.submitted",
            edict_id=edict.id, memorial_id=memorial.id,
            producer="feishu_bot",
            payload={"goal": edict.goal, "channel": "feishu", "chat_id": chat_id},
        ))
        logger.info("[feishu/edict] created edict=%s chat=%s sender=%s",
                    edict.id, chat_id, sender_open_id)
        return edict.id

    async def _follow_up(self, edict: Edict, text: str, sender_open_id: str) -> None:
        """对已锚定的活跃 Edict 做 follow_up（创建新 Memorial 并 fire follow_up.requested）。"""
        memorial = Memorial(
            edict_id=edict.id, instruction=text, status=TaskStatus.SUBMITTED,
        )
        self._storage.save_memorial(memorial)
        self._event_bus.fire(make_event(
            "follow_up.requested",
            edict_id=edict.id, memorial_id=memorial.id,
            producer="feishu_bot",
            payload={"instruction": text, "channel": "feishu",
                     "feishu_user": sender_open_id},
        ))
        logger.info("[feishu/edict] follow_up edict=%s memorial=%s", edict.id, memorial.id)
```

> ⚠️ **核对 follow_up 事件名**：Step 5/6 之前请运行 `grep -rn "follow_up" src/tianshu` 确认现有 follow_up handler 监听的事件名。如不一致，对齐为现状（可能是 `edict.follow_up_requested` 或类似），不要保留 `follow_up.requested` 这个推测名。

- [ ] **Step 2: 验证 follow_up 事件名**

```bash
grep -rn "follow_up\." src/tianshu --include="*.py" | head -10
```

根据输出修正 `_follow_up()` 中的事件名。如果现有代码没有显式 follow_up 事件，改用直接调用 `gateway.api.follow_up_edict` 的内部逻辑（参考 `src/tianshu/gateway/api.py:328` 的 `follow_up_edict` 函数），把它的核心逻辑提取为可复用的内部函数。

- [ ] **Step 3: 提交**
```bash
git add src/tianshu/gateway/feishu/edict_bridge.py
git commit -m "feat(feishu): edict_bridge — 续接/新建/X1 自动新建"
```

### Task 3.3: dispatcher 接 edict_bridge + /new 命令

**Files:**
- Modify: `src/tianshu/gateway/feishu/__init__.py`

- [ ] **Step 1: `FeishuBot._on_message` 接 edict_bridge + 命令解析**

```python
# 文件：src/tianshu/gateway/feishu/__init__.py
# 在 __init__ 里追加：
from tianshu.gateway.feishu.edict_bridge import EdictBridge
from tianshu.gateway.feishu.session_anchor import SessionAnchor

# __init__ 末尾追加属性初始化：
self._anchor = SessionAnchor(storage)
self._edict_bridge = EdictBridge(
    storage=storage, event_bus=event_bus, anchor=self._anchor,
)

# 替换 _on_message 实现：
async def _on_message(self, msg: FeishuMessage) -> None:
    logger.info("[feishu/inbound] chat=%s sender=%s text=%.80s",
                msg.chat_id, msg.sender_open_id, msg.text)
    text = msg.text.strip()
    if text.startswith("/new "):
        goal = text[len("/new "):].strip()
        if not goal:
            await self._reply(msg.chat_id, "用法：/new <目标描述>")
            return
        edict_id = await self._edict_bridge.create_new(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
        )
        await self._reply(msg.chat_id, f"✅ 新敕令 #{edict_id[:8]} 已创建")
        return
    if text.startswith("/"):
        # 其它命令 Step 7 补全；此处仅 /help 兜底
        await self._reply(msg.chat_id, "可用命令：/new <目标>（其它命令开发中）")
        return
    # 默认：续接或自动新建
    edict_id = await self._edict_bridge.continue_or_create(
        chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, text=text,
    )
    await self._reply(msg.chat_id, f"✅ 已收到（敕令 #{edict_id[:8]}）")

async def _reply(self, chat_id: str, text: str) -> None:
    """临时占位，Step 4 替换为真实出站。"""
    logger.info("[feishu/outbound:stub] chat=%s text=%s", chat_id, text)
```

- [ ] **Step 2: 端到端验证**

```bash
# 启动同 Task 2.5 Step 3
# 终端 2：发第一条消息（自动新建）
curl -X POST http://localhost:8000/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev3"},
  "event": {"sender": {"sender_id": {"open_id": "ou_test"}},
    "message": {"chat_id": "oc_chat1", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"帮我查一下 fastapi 最新版本\"}"}}}'

# 终端 2：第二条（续接）
curl -X POST http://localhost:8000/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev4"},
  "event": {"sender": {"sender_id": {"open_id": "ou_test"}},
    "message": {"chat_id": "oc_chat1", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"补充一下：要看 release notes\"}"}}}'

# 终端 2：/new 显式新建
curl -X POST http://localhost:8000/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev5"},
  "event": {"sender": {"sender_id": {"open_id": "ou_test"}},
    "message": {"chat_id": "oc_chat1", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"/new 写个 todo app\"}"}}}'

# 验证 DB
sqlite3 /tmp/feishu_test.db "SELECT chat_id, current_edict_id FROM feishu_session_anchor"
sqlite3 /tmp/feishu_test.db "SELECT id, title, source FROM edicts"
```

Expected: 第一条创建新 Edict、anchor 指向它；第二条挂同一 Edict 的 follow_up Memorial；/new 后 anchor 指向新 Edict。

- [ ] **Step 3: 提交**
```bash
git add src/tianshu/gateway/feishu/__init__.py
git commit -m "feat(feishu): /new 命令 + 默认续接路由"
```

---

## Step 4: 出站基础（事件 → 飞书消息）

**目标：** 替换 `_reply` stub 为真实出站；订阅 `memorial.completed / execution.failed`，回写到 chat。

### Task 4.1: outbound.py — lark-oapi 客户端 + send_text/send_post

**Files:**
- Create: `src/tianshu/gateway/feishu/outbound.py`

- [ ] **Step 1: 写出站基础（先只支持 text 与 post）**

```python
# 文件：src/tianshu/gateway/feishu/outbound.py
"""Feishu 出站：lark-oapi 客户端 + 事件订阅 → 飞书消息。"""
from __future__ import annotations

import json
import logging
import re

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest, CreateMessageRequestBody,
    PatchMessageRequest, PatchMessageRequestBody,
)

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_MD_HINT_RE = re.compile(r"(\n#+\s|\n\s*[-*]\s|\*\*|`{3}|\[.+\]\(.+\))")


class FeishuOutbound:
    """事件订阅 + 飞书消息发送。Step 5 起会扩展卡片下行。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        event_bus: EventBus,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._event_bus = event_bus
        self._client: lark.Client | None = None

    def start(self) -> None:
        """构造 lark client + 注册 EventBus 订阅。"""
        self._client = self._build_client()
        self._event_bus.on("memorial.completed", self._on_memorial_completed, priority=200)
        self._event_bus.on("execution.failed", self._on_execution_failed, priority=200)

    def _build_client(self) -> lark.Client:
        builder = lark.Client.builder() \
            .app_id(self._settings.app_id) \
            .app_secret(self._settings.app_secret)
        if self._settings.domain == "lark":
            builder = builder.domain(lark.LARK_DOMAIN)
        else:
            builder = builder.domain(lark.FEISHU_DOMAIN)
        return builder.log_level(lark.LogLevel.WARNING).build()

    # --- 公共 API：供 dispatcher 等模块直接调用 ---

    async def send_text(self, chat_id: str, content: str) -> str | None:
        """发送文本/post（自动选择）。返回 message_id，失败返 None。"""
        if not chat_id or not content:
            return None
        if _MD_HINT_RE.search(content):
            return await self._send_post(chat_id, content)
        return await self._send_plain_text(chat_id, content)

    async def send_card(self, chat_id: str, card_payload: dict) -> str | None:
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(chat_id)
                          .msg_type("interactive")
                          .content(json.dumps(card_payload, ensure_ascii=False))
                          .build()) \
            .build()
        return await self._send(req)

    async def update_card(self, message_id: str, card_payload: dict) -> bool:
        req = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(PatchMessageRequestBody.builder()
                          .content(json.dumps(card_payload, ensure_ascii=False))
                          .build()) \
            .build()
        try:
            resp = await self._client.im.v1.message.apatch(req)
            ok = resp.success()
            if not ok:
                logger.warning("[feishu/outbound] patch failed code=%s msg=%s",
                               resp.code, resp.msg)
            return ok
        except Exception:
            logger.exception("[feishu/outbound] patch crashed")
            return False

    # --- 内部 ---

    async def _send_plain_text(self, chat_id: str, text: str) -> str | None:
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(chat_id).msg_type("text")
                          .content(json.dumps({"text": text}, ensure_ascii=False))
                          .build()) \
            .build()
        return await self._send(req)

    async def _send_post(self, chat_id: str, markdown: str) -> str | None:
        post_payload = {"zh_cn": {"title": "", "content": [
            [{"tag": "md", "text": markdown}]
        ]}}
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(chat_id).msg_type("post")
                          .content(json.dumps(post_payload, ensure_ascii=False))
                          .build()) \
            .build()
        msg_id = await self._send(req)
        if msg_id:
            return msg_id
        # fallback: post 失败 → 退回纯文本
        plain = re.sub(r"[*_`#>]", "", markdown)
        return await self._send_plain_text(chat_id, plain)

    async def _send(self, req) -> str | None:
        try:
            resp = await self._client.im.v1.message.acreate(req)
            if not resp.success():
                logger.warning("[feishu/outbound] send failed code=%s msg=%s",
                               resp.code, resp.msg)
                return None
            return resp.data.message_id if resp.data else None
        except Exception:
            logger.exception("[feishu/outbound] send crashed")
            return None

    # --- 事件订阅 handlers ---

    async def _on_memorial_completed(self, event: EventEnvelope) -> None:
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        memorial = self._storage.get_memorial(event.memorial_id) if event.memorial_id else None
        if not memorial or not memorial.result:
            return
        title = (event.payload or {}).get("title", "")
        snippet = memorial.result[:500] + ("…" if len(memorial.result) > 500 else "")
        await self.send_text(chat_id, f"✅ **{title or '完成'}**\n\n{snippet}")

    async def _on_execution_failed(self, event: EventEnvelope) -> None:
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        reason = (event.payload or {}).get("error", "未知错误")
        await self.send_text(chat_id, f"❌ 执行失败：{reason}")

    def _lookup_chat_id(self, event: EventEnvelope) -> str | None:
        """根据 edict.metadata.chat_id 反查；没有 → 兜底 home_channel。"""
        if not event.edict_id:
            return self._settings.home_channel or None
        edict = self._storage.get_edict(event.edict_id)
        if not edict:
            return self._settings.home_channel or None
        chat_id = (edict.metadata or {}).get("chat_id")
        return chat_id or (self._settings.home_channel or None)
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/outbound.py
git commit -m "feat(feishu): outbound — lark-oapi 客户端 + memorial.completed/execution.failed 订阅"
```

### Task 4.2: 串接 FeishuBot._reply → outbound

**Files:**
- Modify: `src/tianshu/gateway/feishu/__init__.py`

- [ ] **Step 1: 替换 _reply stub 为 outbound.send_text**

```python
# 文件：src/tianshu/gateway/feishu/__init__.py
# 在 __init__ 追加：
from tianshu.gateway.feishu.outbound import FeishuOutbound

# 在 self._edict_bridge = ... 下追加：
self._outbound = FeishuOutbound(
    settings=settings, storage=storage, event_bus=event_bus,
)

# 在 start() 末尾追加：
self._outbound.start()

# 替换 _reply：
async def _reply(self, chat_id: str, text: str) -> None:
    await self._outbound.send_text(chat_id, text)
```

- [ ] **Step 2: 验证（需要真实飞书 app）**

```bash
# 用真实 FEISHU_APP_ID/SECRET + 把自己加到 allowlist + 在飞书私聊发消息
# 应该收到 "✅ 已收到（敕令 #xxx）" 回复
```

如无真实环境，改用：把 `lark.Client` mock 掉，验证调用参数即可（留待 Step 8 单测）。

- [ ] **Step 3: 提交**
```bash
git add src/tianshu/gateway/feishu/__init__.py
git commit -m "feat(feishu): _reply 串接 outbound.send_text"
```

### Task 4.3: 升级旧 FeishuChannel → 兼容新 outbound

**Files:**
- Modify: `src/tianshu/notifier/channels/feishu.py`
- Modify: `src/tianshu/app.py`

- [ ] **Step 1: 在文件顶部加 deprecated 警告注释**

```python
# 文件：src/tianshu/notifier/channels/feishu.py 顶部追加：
"""Feishu (Lark) Bot webhook channel.

DEPRECATED (2026-04-28): 新部署应配置 TIANSHU_FEISHU_APP_ID/SECRET 启用 app bot 模式
（gateway.feishu 子包），它通过 lark-oapi 直接发消息，不再需要 incoming webhook URL。
本文件保留以兼容旧部署。当 app_id 未配时仍然生效。
"""
```

- [ ] **Step 2: `app.py` 注册逻辑：app bot 与 incoming webhook 互斥（app bot 优先）**

```python
# 文件：src/tianshu/app.py
# 替换原有 if settings.feishu_webhook 段：
if settings.feishu_app_id:
    # app bot 模式：FeishuOutbound 已在 FeishuBot.start() 内部启动事件订阅
    # 此处注册一个 ChannelRegistry 适配器供 dispatch_external 使用
    pass  # FeishuOutbound 不通过 ChannelRegistry，因为它直接订阅 EventBus
elif settings.feishu_webhook:
    from tianshu.notifier.channels.feishu import FeishuChannel
    channel_registry.register(FeishuChannel(settings.feishu_webhook))
```

> 备注：旧 `FeishuChannel` 通过 Notifier `_dispatch_external` 接 `edict.dispatch.channels`；新 `FeishuOutbound` 通过 EventBus 订阅。两套机制并存但**互斥启用**：app bot 模式下 EventBus 直发，旧通道完全跳过。

- [ ] **Step 3: 提交**
```bash
git add src/tianshu/notifier/channels/feishu.py src/tianshu/app.py
git commit -m "feat(feishu): 旧 FeishuChannel 标记 deprecated；app bot 优先"
```

---

## Step 5: 审批型（B）—— 双通道并行卡片

**目标：** `tool.approval_required` 事件 → 飞书卡片下发；按钮点击 → `submit_tool_decision`；`decree.approved/rejected` → 卡片刷新作废。

### Task 5.1: approval_card.py — 卡片构造

**Files:**
- Create: `src/tianshu/gateway/feishu/approval_card.py`

- [ ] **Step 1: 写卡片构造 + 入站处理**

```python
# 文件：src/tianshu/gateway/feishu/approval_card.py
"""出站审批卡片 + 入站 card.action.trigger 处理 + 双通道作废。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.feishu.dispatcher import FeishuCardAction
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

if TYPE_CHECKING:
    from tianshu.gateway.feishu.outbound import FeishuOutbound

logger = logging.getLogger(__name__)


def build_approval_card(
    *,
    memorial_id: str,
    edict_id: str,
    tool_name: str,
    args_summary: dict | None,
    reason: str,
) -> dict:
    """构造审批卡片 payload。按钮 value 包含 memorial_id（v1 用 memorial_id 作 approval 标识）。"""
    summary_lines = []
    if args_summary:
        for k, v in list(args_summary.items())[:5]:
            summary_lines.append(f"- **{k}**：`{v}`")
    summary_md = "\n".join(summary_lines) or "_(无参数摘要)_"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": f"🛡️ 审批：{tool_name}"},
        },
        "elements": [
            {"tag": "markdown",
             "content": f"**敕令** `#{edict_id[:8]}`\n**原因**：{reason}\n\n{summary_md}"},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "✅ 单次允许"},
                 "type": "primary",
                 "value": {"memorial_id": memorial_id, "action": "approve", "scope": "once"}},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "🔄 本敕令允许"},
                 "value": {"memorial_id": memorial_id, "action": "approve", "scope": "edict"}},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "♾️ 总是允许"},
                 "value": {"memorial_id": memorial_id, "action": "approve", "scope": "always"}},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                 "type": "danger",
                 "value": {"memorial_id": memorial_id, "action": "reject"}},
            ]},
        ],
    }


def build_resolved_card(*, tool_name: str, source: str, action: str) -> dict:
    """已响应的卡片：按钮置灰，标题刷新。"""
    icon = "✅" if action == "approve" else "❌"
    label = "批准" if action == "approve" else "拒绝"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "grey",
            "title": {"tag": "plain_text", "content": f"{icon} 已{label}：{tool_name}"},
        },
        "elements": [
            {"tag": "markdown", "content": f"_已在 **{source}** 处响应。_"},
        ],
    }


class ApprovalCardHandler:
    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        event_bus: EventBus,
        approval_manager: ApprovalManager,
        outbound: "FeishuOutbound",
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._event_bus = event_bus
        self._approval = approval_manager
        self._outbound = outbound

    def start(self) -> None:
        """订阅 EventBus：tool.approval_required → 下发卡片；decree.* → 刷新。"""
        self._event_bus.on("tool.approval_required", self._on_approval_required, priority=200)
        self._event_bus.on("decree.approved", self._on_decree_resolved, priority=200)
        self._event_bus.on("decree.rejected", self._on_decree_resolved, priority=200)

    async def _on_approval_required(self, event: EventEnvelope) -> None:
        """tool.approval_required → 找 chat_id → 下发卡片 → 记录 pending_card。"""
        edict_id = event.edict_id
        memorial_id = event.memorial_id
        if not (edict_id and memorial_id):
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            return
        chat_id = (edict.metadata or {}).get("chat_id") or self._settings.home_channel
        if not chat_id:
            logger.debug("[feishu/approval] no chat_id for edict %s, skip card", edict_id)
            return
        payload = event.payload or {}
        card = build_approval_card(
            memorial_id=memorial_id,
            edict_id=edict_id,
            tool_name=payload.get("tool_name", "unknown"),
            args_summary=payload.get("args_summary"),
            reason=payload.get("reason", ""),
        )
        message_id = await self._outbound.send_card(chat_id, card)
        if message_id:
            self._storage.save_feishu_pending_card(
                approval_id=memorial_id, chat_id=chat_id,
                message_id=message_id, kind="tool.approval_required",
            )

    async def handle_button_click(self, action: FeishuCardAction) -> None:
        """入站按钮点击 → submit_tool_decision。"""
        value = action.value or {}
        memorial_id = value.get("memorial_id")
        act = value.get("action")
        scope = value.get("scope")
        if not (memorial_id and act in ("approve", "reject")):
            logger.warning("[feishu/card] malformed value=%s", value)
            return
        try:
            await self._approval.submit_tool_decision(
                memorial_id=memorial_id,
                action=act,
                grant_scope=scope if act == "approve" else None,
                actor=f"feishu:{action.sender_open_id}",
            )
        except ValueError as e:
            # 没有 pending → 已被 web 端响应（幂等场景）
            logger.info("[feishu/card] submit_tool_decision skipped: %s", e)

    async def _on_decree_resolved(self, event: EventEnvelope) -> None:
        """web 或飞书响应 → 刷新另一侧（或本侧）卡片为"已响应"状态。"""
        memorial_id = event.memorial_id
        if not memorial_id:
            return
        pending = self._storage.pop_feishu_pending_card(memorial_id)
        if not pending:
            return
        action = "approve" if event.event_type == "decree.approved" else "reject"
        # 从 payload 判断响应来源
        payload = event.payload or {}
        actor = payload.get("actor", "")
        source = "飞书" if actor.startswith("feishu:") else "web"
        tool_name = payload.get("tool_name", "")
        new_card = build_resolved_card(tool_name=tool_name, source=source, action=action)
        await self._outbound.update_card(pending["message_id"], new_card)
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/approval_card.py
git commit -m "feat(feishu): approval_card — 卡片构造 + tool.approval_required 订阅 + 按钮处理"
```

### Task 5.2: 串接 ApprovalCardHandler 到 FeishuBot

**Files:**
- Modify: `src/tianshu/gateway/feishu/__init__.py`

- [ ] **Step 1: FeishuBot 持有 ApprovalCardHandler，挂到 _on_card**

```python
# 文件：src/tianshu/gateway/feishu/__init__.py
# 在已有 imports 追加：
from tianshu.gateway.feishu.approval_card import ApprovalCardHandler

# __init__ 中 self._outbound 之后追加：
self._approval_card = ApprovalCardHandler(
    settings=settings, storage=storage, event_bus=event_bus,
    approval_manager=approval_manager, outbound=self._outbound,
)

# start() 中 self._outbound.start() 之后追加：
self._approval_card.start()

# 替换 _on_card：
async def _on_card(self, action: FeishuCardAction) -> None:
    logger.info("[feishu/card] chat=%s value=%s", action.chat_id, action.value)
    await self._approval_card.handle_button_click(action)
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/__init__.py
git commit -m "feat(feishu): FeishuBot 串接 ApprovalCardHandler"
```

### Task 5.3: 端到端验证 — 双通道自动作废（前端无代码改动）

> **重要发现：** `web/src/hooks/useWsPolicyToasts.ts` 第 49-75 行**已经实现**了"收到 `decree.approved`/`decree.rejected` 时清理对应 `memorial_id` 的 toast 缓存并 `notification.destroy(...)`"逻辑。
>
> 端到端链路已自然打通，**无需前端改动**：
> 1. 飞书点按钮 → `ApprovalCardHandler.handle_button_click` → `ApprovalManager.submit_tool_decision`
> 2. `submit_tool_decision` 内部 emit `decree.approved`/`decree.rejected` 事件（含 `memorial_id`，见 `src/tianshu/executor/approvals.py:230`）
> 3. EventBus → Notifier → WebSocket broadcast → 浏览器
> 4. `useWsPolicyToasts.ts:49-75` 已存在的分支自动 `notification.destroy(...)` 关闭 web 弹窗
>
> 同时 `ApprovalCardHandler._on_decree_resolved`（Task 5.1 实现）会监听同一个 `decree.approved/rejected` 事件，刷新飞书卡片为"已响应"状态。**双通道自动作废由现有基础设施天然支持。**

**Files:** 仅核对，无修改

- [ ] **Step 1: 核对 `useWsPolicyToasts.ts` 现有清理逻辑**

```bash
sed -n '49,75p' <repo>/web/src/hooks/useWsPolicyToasts.ts
```

Expected：看到这段已存在的分支：

```typescript
if (
  memorialId &&
  (type === "decree.approved" || type === "decree.rejected")
) {
  for (const key of Array.from(seenRef.current)) {
    if (key.startsWith(`approval:${memorialId}:`)) {
      seenRef.current.delete(key);
      const nkey = notifKeyRef.current.get(key);
      if (nkey) {
        notification.destroy(nkey);
        notifKeyRef.current.delete(key);
      }
    }
  }
  // ...
  return;
}
```

如该段被人删除（应不会发生），按上面代码原样恢复，并在文件 7-16 行的 `ToastPayload` 接口内保持现状。

- [ ] **Step 2: 端到端验证（需真实飞书 app + 前端 dev 环境）**

```bash
# 终端 1：后端 + 飞书
TIANSHU_FEISHU_APP_ID=cli_xxx TIANSHU_FEISHU_APP_SECRET=secret_xxx \
TIANSHU_FEISHU_ALLOWED_USERS=ou_xxx \
uv run uvicorn tianshu.app:create_app --factory --port 8000

# 终端 2：前端
cd web && pnpm dev

# 终端 3 / 浏览器：在 web 创建一个会触发 shell_exec 审批的敕令
# 浏览器：应看到 antd notification "需要审批"
# 飞书：应收到审批卡片
# 在飞书点 "✅ 单次允许"
# 验证：浏览器 antd notification 自动消失 + 飞书卡片刷新为 "✅ 已批准（来自 飞书）"
```

- [ ] **Step 3: 反向验证（web → 飞书卡片刷新）**

同上场景，但在 web 点 "通过" → 验证飞书卡片刷新为 "✅ 已批准（来自 web）"。

- [ ] **Step 4: 无代码改动则无需提交**

仅当 Step 1 发现已存在分支已被删除而你恢复了它，才提交：
```bash
git add web/src/hooks/useWsPolicyToasts.ts
git commit -m "fix(web/feishu): 恢复 decree 事件清理 toast 的逻辑"
```

---

## Step 6: WebSocket 模式

**目标：** 切换到 lark-oapi SDK 反向长连作为运行时默认，无需公网。

### Task 6.1: connection.py 追加 WebSocketConnection

**Files:**
- Modify: `src/tianshu/gateway/feishu/connection.py`

- [ ] **Step 1: 引入 lark.ws.Client + 事件回调适配**

```python
# 文件：src/tianshu/gateway/feishu/connection.py
# 在 WebhookConnection 后追加：

import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.api.application.v6 import P2ApplicationCardActionTriggerV1


class WebSocketConnection:
    """lark-oapi SDK 反向长连。SDK 内部跑独立线程，事件 dispatch 回主 loop。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        inbound_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self.inbound_queue = inbound_queue
        self._loop = loop
        self._client: lark.ws.Client | None = None
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        event_handler = lark.EventDispatcherHandler.builder(
            self._settings.encrypt_key, self._settings.verification_token,
        )
        event_handler.register_p2_im_message_receive_v1(self._on_message)
        event_handler.register_p2_application_card_action_trigger_v1(self._on_card)
        handler = event_handler.build()

        log_level = lark.LogLevel.WARNING
        builder = lark.ws.Client(
            self._settings.app_id, self._settings.app_secret,
            event_handler=handler, log_level=log_level,
        )
        self._client = builder
        # SDK 内部 _start() 是阻塞的；放到独立线程
        self._thread = threading.Thread(target=self._client.start, daemon=True)
        self._thread.start()
        logger.info("[feishu/ws] started (app=%s, domain=%s)",
                    self._settings.app_id, self._settings.domain)

    async def stop(self) -> None:
        if self._client and hasattr(self._client, "_stop"):
            try:
                self._client._stop()
            except Exception:
                logger.exception("[feishu/ws] stop failed")

    def _on_message(self, event: P2ImMessageReceiveV1) -> None:
        """SDK 在独立线程触发，需要 thread-safe schedule 到主 loop。"""
        # 把 SDK 事件对象转成 webhook 兼容的字典 payload
        payload = self._sdk_message_to_payload(event)
        asyncio.run_coroutine_threadsafe(
            self.inbound_queue.put(payload), self._loop,
        )

    def _on_card(self, event: P2ApplicationCardActionTriggerV1) -> None:
        payload = self._sdk_card_to_payload(event)
        asyncio.run_coroutine_threadsafe(
            self.inbound_queue.put(payload), self._loop,
        )

    @staticmethod
    def _sdk_message_to_payload(event) -> dict:
        """把 SDK 的 P2ImMessageReceiveV1 转成 webhook event payload 格式，
        让 dispatcher 处理逻辑无需感知连接模式。"""
        ev = event.event
        msg = ev.message
        return {
            "header": {
                "event_type": "im.message.receive_v1",
                "event_id": event.header.event_id,
            },
            "event": {
                "sender": {"sender_id": {
                    "open_id": ev.sender.sender_id.open_id,
                    "user_id": ev.sender.sender_id.user_id,
                    "union_id": ev.sender.sender_id.union_id,
                }},
                "message": {
                    "message_id": msg.message_id,
                    "chat_id": msg.chat_id,
                    "chat_type": msg.chat_type,
                    "message_type": msg.message_type,
                    "content": msg.content,
                    "mentions": [
                        {"id": {"open_id": m.id.open_id}, "name": m.name}
                        for m in (msg.mentions or [])
                    ],
                },
            },
        }

    @staticmethod
    def _sdk_card_to_payload(event) -> dict:
        return {
            "header": {
                "event_type": "card.action.trigger",
                "event_id": event.header.event_id,
            },
            "event": {
                "operator": {"open_id": event.event.operator.open_id},
                "action": {"value": event.event.action.value},
                "context": {"open_chat_id": getattr(event.event.context, "open_chat_id", "")},
            },
        }
```

> 注：lark-oapi SDK 的 P2 类型路径与方法名以实际 SDK 版本为准。如果 import 报错，运行 `uv run python -c "from lark_oapi.api.im.v1 import *"` 看实际可用导出。

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/connection.py
git commit -m "feat(feishu): WebSocketConnection 实现（lark-oapi SDK 反向长连）"
```

### Task 6.2: FeishuBot 支持 ws 模式 + 进程互斥锁

**Files:**
- Modify: `src/tianshu/gateway/feishu/__init__.py`

- [ ] **Step 1: 在 start() 中按 connection_mode 分支 + 占进程锁**

```python
# 文件：src/tianshu/gateway/feishu/__init__.py
import asyncio as _asyncio
import os
from pathlib import Path

from tianshu.gateway.feishu.connection import WebSocketConnection, WebhookConnection

# 替换 start() 中的连接构造段：
async def start(self) -> None:
    logger.info("[feishu] starting (mode=%s, app=%s)",
                self._settings.connection_mode, self._settings.app_id)
    self._acquire_app_lock()
    if self._settings.connection_mode == "websocket":
        loop = _asyncio.get_running_loop()
        self._connection = WebSocketConnection(
            settings=self._settings, storage=self._storage,
            inbound_queue=self._inbound, loop=loop,
        )
    else:
        self._connection = WebhookConnection(
            settings=self._settings, storage=self._storage,
            inbound_queue=self._inbound,
        )
    await self._connection.start()
    self._dispatcher = Dispatcher(...)
    await self._dispatcher.start()
    self._outbound.start()
    self._approval_card.start()

def _acquire_app_lock(self) -> None:
    """启动时占进程锁，避免双开同一 app_id。"""
    lock_path = Path.home() / ".tianshu" / f"feishu_app_lock.{self._settings.app_id}"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            os.kill(pid, 0)  # 检查进程是否存活
            raise RuntimeError(
                f"Another tianshu process (pid={pid}) is using feishu app {self._settings.app_id}"
            )
        except ProcessLookupError:
            # 锁文件残留，清理
            pass
    lock_path.write_text(str(os.getpid()))
    self._lock_path = lock_path

# stop() 末尾追加锁清理：
async def stop(self) -> None:
    ...  # 已有逻辑
    if hasattr(self, "_lock_path") and self._lock_path.exists():
        try:
            self._lock_path.unlink()
        except Exception:
            pass
```

- [ ] **Step 2: 验证（需真实飞书 app）**

```bash
TIANSHU_FEISHU_APP_ID=cli_xxx \
TIANSHU_FEISHU_APP_SECRET=secret_xxx \
TIANSHU_FEISHU_ALLOWED_USERS=ou_xxx \
TIANSHU_FEISHU_CONNECTION_MODE=websocket \
TIANSHU_DB_PATH=/tmp/feishu_ws.db \
uv run uvicorn tianshu.app:create_app --factory --port 8000

# 期望日志：
# [feishu] starting (mode=websocket, app=cli_xxx)
# [feishu/ws] started ...

# 在飞书私聊机器人发消息 → 应触发 inbound 日志 + 收到 echo
```

- [ ] **Step 3: 双开测试**

```bash
# 同时启动两个进程 → 第二个应 fatal 退出
```

- [ ] **Step 4: 提交**
```bash
git add src/tianshu/gateway/feishu/__init__.py
git commit -m "feat(feishu): WebSocket 模式接入 + 进程互斥锁"
```

---

## Step 7: 优化（命令补全 + 限流 + 重连告警 + 批处理）

### Task 7.1: 文本批处理（debounce 0.6s）

**Files:**
- Modify: `src/tianshu/gateway/feishu/dispatcher.py`

- [ ] **Step 1: 在 Dispatcher 加文本合并逻辑**

```python
# 文件：src/tianshu/gateway/feishu/dispatcher.py
# 在 Dispatcher 类追加：

self._batch_buffers: dict[str, list[str]] = {}      # chat_id → 待合并文本
self._batch_timers: dict[str, asyncio.Task] = {}    # chat_id → flush task

async def _enqueue_for_batch(self, fmsg: FeishuMessage) -> None:
    """文本消息进批处理缓冲。0.6s 静默期后合并 flush。"""
    buf = self._batch_buffers.setdefault(fmsg.chat_id, [])
    buf.append(fmsg.text)
    existing = self._batch_timers.get(fmsg.chat_id)
    if existing and not existing.done():
        existing.cancel()

    async def _flush() -> None:
        await asyncio.sleep(self._settings.text_batch_delay)
        merged = "\n".join(self._batch_buffers.pop(fmsg.chat_id, []))
        self._batch_timers.pop(fmsg.chat_id, None)
        if not merged:
            return
        merged_msg = FeishuMessage(
            event_id=fmsg.event_id, chat_id=fmsg.chat_id, chat_type=fmsg.chat_type,
            sender_open_id=fmsg.sender_open_id, text=merged, raw=fmsg.raw,
        )
        lock = self._chat_locks.setdefault(fmsg.chat_id, asyncio.Lock())
        async with lock:
            await self._message_handler(merged_msg)

    self._batch_timers[fmsg.chat_id] = asyncio.create_task(_flush())

# 修改 _handle_message_event 末尾（"# per-chat 串行" 段）：
# 命令（以 / 开头）跳过批处理直接派发；纯文本走批处理
if text.startswith("/"):
    lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        await self._message_handler(fmsg)
else:
    await self._enqueue_for_batch(fmsg)
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/dispatcher.py
git commit -m "feat(feishu): 文本批处理（0.6s 静默期合并）"
```

### Task 7.2: Webhook 限流

**Files:**
- Modify: `src/tianshu/gateway/feishu/connection.py`

- [ ] **Step 1: 在 WebhookConnection 加滑动窗口限流**

```python
# 文件：src/tianshu/gateway/feishu/connection.py
# WebhookConnection 顶部加：
import time
from collections import deque, OrderedDict

# WebhookConnection.__init__ 追加：
self._rate_state: OrderedDict[str, deque] = OrderedDict()
self._RATE_WINDOW = 60.0
self._RATE_LIMIT = 120
self._RATE_MAX_KEYS = 4096

# _handle_request 开头，验签前追加：
client_ip = request.client.host if request.client else "unknown"
rate_key = f"{self._settings.app_id}:{self._settings.webhook_path}:{client_ip}"
if not self._allow_rate(rate_key):
    return Response("rate limited", status_code=429)

# 新增方法：
def _allow_rate(self, key: str) -> bool:
    now = time.monotonic()
    bucket = self._rate_state.get(key)
    if bucket is None:
        if len(self._rate_state) >= self._RATE_MAX_KEYS:
            self._rate_state.popitem(last=False)
        bucket = deque()
        self._rate_state[key] = bucket
    self._rate_state.move_to_end(key)
    while bucket and now - bucket[0] > self._RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= self._RATE_LIMIT:
        return False
    bucket.append(now)
    return True
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/connection.py
git commit -m "feat(feishu): Webhook IP 限流（60s/120 req）"
```

### Task 7.3: WS 重连告警

**Files:**
- Modify: `src/tianshu/gateway/feishu/connection.py`

- [ ] **Step 1: WS 客户端连续重连失败 → 发 feishu.connection_lost 事件**

由于 lark-oapi SDK 内部自动重连，无法直接获得失败计数。简化方案：在独立线程跑一个 watchdog，每 30s 检查最近一次成功事件时间戳，超过 `reconnect_interval × 5` 视为长时间无心跳。

```python
# 文件：src/tianshu/gateway/feishu/connection.py
# WebSocketConnection 加：
import time

self._last_event_at = time.monotonic()
self._watchdog_task: asyncio.Task | None = None

# start() 末尾追加：
self._last_event_at = time.monotonic()
self._watchdog_task = asyncio.create_task(self._watchdog())

# _on_message / _on_card 入口先更新：
self._last_event_at = time.monotonic()

async def _watchdog(self) -> None:
    threshold = self._settings.ws_reconnect_interval * 5
    while True:
        await asyncio.sleep(30)
        idle = time.monotonic() - self._last_event_at
        if idle > threshold:
            logger.warning("[feishu/ws] no events for %ds, possible disconnection", int(idle))

# stop() 追加：
if self._watchdog_task:
    self._watchdog_task.cancel()
```

- [ ] **Step 2: 提交**
```bash
git add src/tianshu/gateway/feishu/connection.py
git commit -m "feat(feishu): WS 心跳 watchdog（长时间无事件告警）"
```

### Task 7.4: 命令集补全 (/status, /cancel, /set-home, /help)

**Files:**
- Modify: `src/tianshu/gateway/feishu/__init__.py`

- [ ] **Step 1: 在 _on_message 扩展命令分支**

```python
# 文件：src/tianshu/gateway/feishu/__init__.py
# 替换 _on_message 中 text.startswith("/") 分支：

text = msg.text.strip()
parts = text.split(maxsplit=1)
cmd = parts[0] if parts else ""

if cmd == "/new":
    goal = parts[1].strip() if len(parts) > 1 else ""
    if not goal:
        await self._reply(msg.chat_id, "用法：/new <目标描述>")
        return
    edict_id = await self._edict_bridge.create_new(
        chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
    )
    await self._reply(msg.chat_id, f"✅ 新敕令 #{edict_id[:8]} 已创建")
    return

if cmd == "/status":
    target = parts[1].strip() if len(parts) > 1 else self._anchor.get(msg.chat_id) or ""
    if not target:
        await self._reply(msg.chat_id, "当前会话无活跃敕令。用 /new 创建一个。")
        return
    edict = self._storage.get_edict(target)
    if not edict:
        await self._reply(msg.chat_id, f"敕令 #{target[:8]} 不存在")
        return
    await self._reply(msg.chat_id, f"敕令 #{edict.id[:8]}\n标题：{edict.title}\n状态：{edict.status}")
    return

if cmd == "/cancel":
    target = parts[1].strip() if len(parts) > 1 else self._anchor.get(msg.chat_id) or ""
    if not target:
        await self._reply(msg.chat_id, "用法：/cancel [edict_id]")
        return
    edict = self._storage.get_edict(target)
    if not edict:
        await self._reply(msg.chat_id, f"敕令 #{target[:8]} 不存在")
        return
    from tianshu.models.common import EdictStatus
    edict.status = EdictStatus.CANCELLED
    self._storage.save_edict(edict)
    await self._reply(msg.chat_id, f"✅ 敕令 #{edict.id[:8]} 已取消")
    return

if cmd == "/set-home":
    # 持久化到一个新的 storage 方法（key-value 表）；v1 简化：写日志 + 提示用户用 env
    await self._reply(
        msg.chat_id,
        f"当前 chat_id = `{msg.chat_id}`\n"
        f"请将其设置到 `TIANSHU_FEISHU_HOME_CHANNEL` 环境变量后重启服务。"
    )
    return

if cmd == "/help":
    await self._reply(msg.chat_id,
        "可用命令：\n"
        "- `/new <目标>` 显式新建敕令\n"
        "- `/status [敕令id]` 查看当前/指定敕令状态\n"
        "- `/cancel [敕令id]` 取消敕令\n"
        "- `/set-home` 显示当前 chat_id（用于配置 home channel）\n"
        "- `/help` 显示帮助\n\n"
        "默认行为：纯文本消息会续接当前会话锚定的敕令。")
    return

if cmd.startswith("/"):
    await self._reply(msg.chat_id, f"未知命令：{cmd}。输入 /help 查看帮助。")
    return
# 默认：续接或自动新建（已有逻辑）
```

- [ ] **Step 2: 验证 + 提交**
```bash
git add src/tianshu/gateway/feishu/__init__.py
git commit -m "feat(feishu): /status, /cancel, /set-home, /help 命令"
```

---

## Step 8: 测试 + 文档

**目标：** 补齐测试到 80%+ 覆盖；写用户文档。

### Task 8.1: 单元测试 — settings + security + session_anchor

**Files:**
- Create: `tests/gateway/feishu/__init__.py`（空）
- Create: `tests/gateway/feishu/test_settings.py`
- Create: `tests/gateway/feishu/test_security.py`
- Create: `tests/gateway/feishu/test_session_anchor.py`

- [ ] **Step 1: test_settings.py**

```python
# 文件：tests/gateway/feishu/test_settings.py
import pytest
from types import SimpleNamespace

from tianshu.gateway.feishu.settings import FeishuSettings, from_global_settings


def _global(**overrides):
    base = dict(
        feishu_app_id="cli_x", feishu_app_secret="sec",
        feishu_domain="feishu", feishu_connection_mode="websocket",
        feishu_allowed_users="ou_a,ou_b", feishu_home_channel="",
        feishu_encrypt_key="", feishu_verification_token="",
        feishu_bot_open_id="", feishu_bot_name="",
        feishu_webhook_path="/feishu/webhook",
        feishu_ws_reconnect_interval=120, feishu_text_batch_delay=0.6,
        feishu_dedup_cache_size=2048,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_enabled_when_app_id_present():
    s = from_global_settings(_global())
    assert s.enabled is True
    assert s.allowed_users == ("ou_a", "ou_b")


def test_disabled_when_app_id_empty():
    s = from_global_settings(_global(feishu_app_id=""))
    assert s.enabled is False
    s.validate_or_raise()  # 不抛


def test_validate_requires_secret():
    s = from_global_settings(_global(feishu_app_secret=""))
    with pytest.raises(RuntimeError, match="APP_SECRET"):
        s.validate_or_raise()


def test_validate_requires_allowlist():
    s = from_global_settings(_global(feishu_allowed_users=""))
    with pytest.raises(RuntimeError, match="ALLOWED_USERS"):
        s.validate_or_raise()


def test_validate_rejects_unknown_mode():
    s = from_global_settings(_global(feishu_connection_mode="grpc"))
    with pytest.raises(RuntimeError, match="connection_mode"):
        s.validate_or_raise()
```

- [ ] **Step 2: test_security.py**

```python
# 文件：tests/gateway/feishu/test_security.py
import hashlib

from tianshu.gateway.feishu.security import (
    DedupChecker, is_allowed_user, verify_signature, verify_token,
)


def test_signature_valid():
    key = "k1"; ts = "1700"; nonce = "n"; body = b'{"x":1}'
    sig = hashlib.sha256(f"{ts}{nonce}{key}".encode() + body).hexdigest()
    headers = {"x-lark-request-timestamp": ts, "x-lark-request-nonce": nonce, "x-lark-signature": sig}
    assert verify_signature(headers, body, key) is True


def test_signature_invalid():
    headers = {"x-lark-request-timestamp": "1", "x-lark-request-nonce": "n", "x-lark-signature": "bad"}
    assert verify_signature(headers, b"body", "k1") is False


def test_signature_skip_when_no_key():
    assert verify_signature({}, b"body", "") is True


def test_verify_token_match():
    assert verify_token({"header": {"token": "t1"}}, "t1") is True


def test_verify_token_mismatch():
    assert verify_token({"header": {"token": "x"}}, "t1") is False


def test_verify_token_skip_when_empty():
    assert verify_token({}, "") is True


def test_is_allowed_user():
    assert is_allowed_user("ou_a", ["ou_a", "ou_b"]) is True
    assert is_allowed_user("ou_c", ["ou_a", "ou_b"]) is False


def test_dedup_first_then_repeat(storage):
    d = DedupChecker(storage, max_entries=10)
    assert d.check_and_mark("m1") is True
    assert d.check_and_mark("m1") is False    # 重复
    assert d.check_and_mark("m2") is True


def test_dedup_no_message_id_passes(storage):
    d = DedupChecker(storage)
    assert d.check_and_mark("") is True
```

- [ ] **Step 3: test_session_anchor.py**

```python
# 文件：tests/gateway/feishu/test_session_anchor.py
from tianshu.gateway.feishu.session_anchor import SessionAnchor


def test_anchor_get_set(storage):
    anchor = SessionAnchor(storage)
    assert anchor.get("oc_x") is None
    anchor.set("oc_x", "ed_1")
    assert anchor.get("oc_x") == "ed_1"
    anchor.set("oc_x", "ed_2")
    assert anchor.get("oc_x") == "ed_2"


def test_anchor_per_chat(storage):
    anchor = SessionAnchor(storage)
    anchor.set("oc_a", "ed_1")
    anchor.set("oc_b", "ed_2")
    assert anchor.get("oc_a") == "ed_1"
    assert anchor.get("oc_b") == "ed_2"
```

- [ ] **Step 4: 运行 + 提交**
```bash
uv run pytest tests/gateway/feishu/test_settings.py tests/gateway/feishu/test_security.py tests/gateway/feishu/test_session_anchor.py -v
```
Expected: 全部通过

```bash
git add tests/gateway/feishu/
git commit -m "test(feishu): settings/security/session_anchor 单元测试"
```

### Task 8.2: 单元测试 — edict_bridge

**Files:**
- Create: `tests/gateway/feishu/test_edict_bridge.py`

- [ ] **Step 1**

```python
# 文件：tests/gateway/feishu/test_edict_bridge.py
import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.edict_bridge import EdictBridge
from tianshu.gateway.feishu.session_anchor import SessionAnchor
from tianshu.models.common import EdictStatus
from tianshu.models.edict import Edict


@pytest.fixture
def bridge(storage):
    bus = EventBus(storage=storage)
    anchor = SessionAnchor(storage)
    return EdictBridge(storage=storage, event_bus=bus, anchor=anchor), bus, anchor


@pytest.mark.asyncio
async def test_create_new_writes_edict_and_anchor(bridge):
    b, _, anchor = bridge
    eid = await b.create_new(chat_id="oc_x", sender_open_id="ou_a", goal="hello world")
    assert anchor.get("oc_x") == eid
    edict = b._storage.get_edict(eid)
    assert edict.source == "channel"
    assert edict.submitter == "emperor"
    assert edict.metadata.get("chat_id") == "oc_x"


@pytest.mark.asyncio
async def test_continue_or_create_no_anchor_creates(bridge):
    b, _, anchor = bridge
    eid = await b.continue_or_create(chat_id="oc_x", sender_open_id="ou_a", text="first")
    assert anchor.get("oc_x") == eid


@pytest.mark.asyncio
async def test_continue_or_create_with_active_anchor_follow_up(bridge, storage):
    b, _, anchor = bridge
    eid = await b.create_new(chat_id="oc_x", sender_open_id="ou_a", goal="g")
    eid2 = await b.continue_or_create(chat_id="oc_x", sender_open_id="ou_a", text="more")
    assert eid2 == eid    # 续接，不新建
    # 验证 anchor 不变
    assert anchor.get("oc_x") == eid


@pytest.mark.asyncio
async def test_continue_or_create_with_closed_anchor_auto_new(bridge, storage):
    """子决策 X1：anchor 指向已结案 Edict → 自动新建。"""
    b, _, anchor = bridge
    eid_old = await b.create_new(chat_id="oc_x", sender_open_id="ou_a", goal="old")
    # 模拟敕令完成
    edict = storage.get_edict(eid_old)
    edict.status = EdictStatus.COMPLETED
    storage.save_edict(edict)
    eid_new = await b.continue_or_create(chat_id="oc_x", sender_open_id="ou_a", text="next")
    assert eid_new != eid_old
    assert anchor.get("oc_x") == eid_new
```

- [ ] **Step 2: 运行 + 提交**
```bash
uv run pytest tests/gateway/feishu/test_edict_bridge.py -v
```

```bash
git add tests/gateway/feishu/test_edict_bridge.py
git commit -m "test(feishu): edict_bridge 含 X1 已结案自动新建"
```

### Task 8.3: 集成测试 — webhook 端到端

**Files:**
- Create: `tests/gateway/feishu/test_e2e_webhook.py`

- [ ] **Step 1: 用 FastAPI TestClient 模拟事件**

```python
# 文件：tests/gateway/feishu/test_e2e_webhook.py
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_feishu(monkeypatch):
    monkeypatch.setenv("TIANSHU_FEISHU_APP_ID", "test_app")
    monkeypatch.setenv("TIANSHU_FEISHU_APP_SECRET", "test_sec")
    monkeypatch.setenv("TIANSHU_FEISHU_ALLOWED_USERS", "ou_test")
    monkeypatch.setenv("TIANSHU_FEISHU_CONNECTION_MODE", "webhook")
    from tianshu.app import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client, app


def _msg_payload(text: str, *, sender="ou_test", chat="oc_e2e", event_id="ev_e2e_1") -> dict:
    return {
        "header": {"event_type": "im.message.receive_v1", "event_id": event_id},
        "event": {
            "sender": {"sender_id": {"open_id": sender}},
            "message": {
                "message_id": event_id,
                "chat_id": chat, "chat_type": "p2p", "message_type": "text",
                "content": json.dumps({"text": text}),
            },
        },
    }


def test_url_verification(app_with_feishu):
    client, _ = app_with_feishu
    r = client.post("/feishu/webhook", json={"type": "url_verification", "challenge": "ch1"})
    assert r.status_code == 200
    assert r.json() == {"challenge": "ch1"}


def test_message_creates_edict(app_with_feishu):
    client, app = app_with_feishu
    r = client.post("/feishu/webhook", json=_msg_payload("hello"))
    assert r.status_code == 200
    # 等待 dispatcher 异步处理（debounce 0.6s + 实际任务）
    import time; time.sleep(1.0)
    storage = app.state.storage
    edicts = storage.list_edicts()
    assert len(edicts) >= 1
    e = edicts[0]
    assert e.source == "channel"
    assert e.metadata.get("chat_id") == "oc_e2e"


def test_allowlist_rejects_silent(app_with_feishu):
    client, app = app_with_feishu
    r = client.post("/feishu/webhook", json=_msg_payload("hi", sender="ou_BAD", event_id="ev_bad"))
    assert r.status_code == 200  # 不暴露
    import time; time.sleep(1.0)
    storage = app.state.storage
    assert storage.list_edicts() == []  # 没创建


def test_dedup_repeated_event_id(app_with_feishu):
    client, app = app_with_feishu
    payload = _msg_payload("once", event_id="ev_dup")
    r1 = client.post("/feishu/webhook", json=payload)
    r2 = client.post("/feishu/webhook", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    import time; time.sleep(1.0)
    edicts = app.state.storage.list_edicts()
    assert len(edicts) == 1   # 第二次被去重
```

- [ ] **Step 2: 运行 + 提交**
```bash
uv run pytest tests/gateway/feishu/test_e2e_webhook.py -v
```

```bash
git add tests/gateway/feishu/test_e2e_webhook.py
git commit -m "test(feishu): webhook 端到端集成测试"
```

### Task 8.4: 单元测试 — approval_card

**Files:**
- Create: `tests/gateway/feishu/test_approval_card.py`

- [ ] **Step 1**

```python
# 文件：tests/gateway/feishu/test_approval_card.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.approval_card import (
    ApprovalCardHandler, build_approval_card, build_resolved_card,
)
from tianshu.gateway.feishu.dispatcher import FeishuCardAction
from tianshu.gateway.feishu.settings import FeishuSettings


@pytest.fixture
def settings():
    return FeishuSettings(
        app_id="x", app_secret="y", domain="feishu", connection_mode="webhook",
        allowed_users=("ou_test",), home_channel="",
        encrypt_key="", verification_token="", bot_open_id="", bot_name="",
        webhook_path="/feishu/webhook", ws_reconnect_interval=120,
        text_batch_delay=0.6, dedup_cache_size=2048,
    )


def test_build_approval_card_has_buttons():
    card = build_approval_card(
        memorial_id="m1", edict_id="ed_1", tool_name="shell_exec",
        args_summary={"cmd": "git push"}, reason="dangerous",
    )
    actions = card["elements"][1]["actions"]
    assert len(actions) == 4
    values = [a["value"] for a in actions]
    assert values[0] == {"memorial_id": "m1", "action": "approve", "scope": "once"}
    assert values[3] == {"memorial_id": "m1", "action": "reject"}


def test_build_resolved_card_grey_header():
    card = build_resolved_card(tool_name="shell_exec", source="飞书", action="approve")
    assert card["header"]["template"] == "grey"
    assert "已批准" in card["header"]["title"]["content"]


@pytest.mark.asyncio
async def test_handle_button_click_calls_submit(settings, storage):
    bus = MagicMock()
    am = MagicMock()
    am.submit_tool_decision = AsyncMock()
    outbound = MagicMock()
    h = ApprovalCardHandler(
        settings=settings, storage=storage, event_bus=bus,
        approval_manager=am, outbound=outbound,
    )
    action = FeishuCardAction(
        event_id="e", chat_id="oc",
        sender_open_id="ou_test",
        value={"memorial_id": "m1", "action": "approve", "scope": "once"},
    )
    await h.handle_button_click(action)
    am.submit_tool_decision.assert_awaited_once_with(
        memorial_id="m1", action="approve", grant_scope="once",
        actor="feishu:ou_test",
    )


@pytest.mark.asyncio
async def test_handle_button_idempotent_when_already_resolved(settings, storage):
    """ApprovalManager 抛 ValueError 时（已被 web 端响应）→ 不应崩溃。"""
    bus = MagicMock()
    am = MagicMock()
    am.submit_tool_decision = AsyncMock(side_effect=ValueError("no pending"))
    outbound = MagicMock()
    h = ApprovalCardHandler(
        settings=settings, storage=storage, event_bus=bus,
        approval_manager=am, outbound=outbound,
    )
    action = FeishuCardAction(
        event_id="e", chat_id="oc", sender_open_id="ou_test",
        value={"memorial_id": "m1", "action": "approve", "scope": "once"},
    )
    await h.handle_button_click(action)  # 不应抛
```

- [ ] **Step 2: 运行 + 提交**
```bash
uv run pytest tests/gateway/feishu/test_approval_card.py -v
```

```bash
git add tests/gateway/feishu/test_approval_card.py
git commit -m "test(feishu): approval_card 单元测试（含幂等场景）"
```

### Task 8.5: 单元测试 — outbound

**Files:**
- Create: `tests/gateway/feishu/test_outbound.py`

- [ ] **Step 1: mock lark client，验证消息构造**

```python
# 文件：tests/gateway/feishu/test_outbound.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.outbound import FeishuOutbound, _MD_HINT_RE
from tianshu.gateway.feishu.settings import FeishuSettings


def _settings(home=""):
    return FeishuSettings(
        app_id="x", app_secret="y", domain="feishu", connection_mode="webhook",
        allowed_users=("ou_test",), home_channel=home,
        encrypt_key="", verification_token="", bot_open_id="", bot_name="",
        webhook_path="/feishu/webhook", ws_reconnect_interval=120,
        text_batch_delay=0.6, dedup_cache_size=2048,
    )


def test_md_hint_regex_detects_markdown():
    assert _MD_HINT_RE.search("# title\n") is not None
    assert _MD_HINT_RE.search("- item") is not None
    assert _MD_HINT_RE.search("**bold**") is not None
    assert _MD_HINT_RE.search("plain text only") is None


@pytest.mark.asyncio
async def test_send_text_uses_post_when_markdown_detected(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_client.im.v1.message.acreate = AsyncMock(return_value=MagicMock(
        success=lambda: True, data=MagicMock(message_id="msg_1"),
    ))
    out._client = fake_client
    mid = await out.send_text("oc_x", "**hello**")
    assert mid == "msg_1"
    sent_req = fake_client.im.v1.message.acreate.await_args.args[0]
    body = sent_req.request_body
    # 验证 msg_type == post（具体属性以 lark-oapi SDK 实际接口为准；
    # 必要时改为反序列化 body 内的 content 字段）


@pytest.mark.asyncio
async def test_send_text_plain_when_no_markdown(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_client.im.v1.message.acreate = AsyncMock(return_value=MagicMock(
        success=lambda: True, data=MagicMock(message_id="m1"),
    ))
    out._client = fake_client
    await out.send_text("oc_x", "plain hello")
    assert fake_client.im.v1.message.acreate.await_count == 1
```

- [ ] **Step 2: 运行 + 提交**
```bash
uv run pytest tests/gateway/feishu/test_outbound.py -v
```

```bash
git add tests/gateway/feishu/test_outbound.py
git commit -m "test(feishu): outbound 单元测试（markdown 检测/客户端调用）"
```

### Task 8.6: 单元测试 — dispatcher（命令解析 + 群@网关）

**Files:**
- Create: `tests/gateway/feishu/test_dispatcher.py`

- [ ] **Step 1**

```python
# 文件：tests/gateway/feishu/test_dispatcher.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock

from tianshu.gateway.feishu.dispatcher import Dispatcher, FeishuCardAction, FeishuMessage
from tianshu.gateway.feishu.settings import FeishuSettings


def _settings(*, allowed=("ou_test",), bot_open_id="bot_x", bot_name="MyBot"):
    return FeishuSettings(
        app_id="x", app_secret="y", domain="feishu", connection_mode="webhook",
        allowed_users=allowed, home_channel="",
        encrypt_key="", verification_token="",
        bot_open_id=bot_open_id, bot_name=bot_name,
        webhook_path="/feishu/webhook", ws_reconnect_interval=120,
        text_batch_delay=0.0,  # 测试关掉 batch 防止异步等待
        dedup_cache_size=2048,
    )


def _msg_event(*, text="hi", chat_id="oc_x", chat_type="p2p", sender="ou_test", mentions=None):
    return {
        "sender": {"sender_id": {"open_id": sender}},
        "message": {
            "chat_id": chat_id, "chat_type": chat_type, "message_type": "text",
            "content": json.dumps({"text": text}),
            "mentions": mentions or [],
        },
    }


@pytest.mark.asyncio
async def test_p2p_text_dispatches():
    q = asyncio.Queue()
    handler = AsyncMock()
    d = Dispatcher(settings=_settings(), inbound_queue=q,
                   message_handler=handler, card_handler=AsyncMock())
    await d.start()
    await q.put({"header": {"event_type": "im.message.receive_v1", "event_id": "e1"},
                 "event": _msg_event(text="hello")})
    await asyncio.sleep(0.05)
    handler.assert_awaited_once()
    fmsg: FeishuMessage = handler.await_args.args[0]
    assert fmsg.text == "hello"
    await d.stop()


@pytest.mark.asyncio
async def test_group_without_mention_ignored():
    q = asyncio.Queue()
    handler = AsyncMock()
    d = Dispatcher(settings=_settings(), inbound_queue=q,
                   message_handler=handler, card_handler=AsyncMock())
    await d.start()
    await q.put({"header": {"event_type": "im.message.receive_v1", "event_id": "e2"},
                 "event": _msg_event(chat_type="group", text="hi everyone")})
    await asyncio.sleep(0.05)
    handler.assert_not_awaited()
    await d.stop()


@pytest.mark.asyncio
async def test_group_with_bot_mention_dispatches():
    q = asyncio.Queue()
    handler = AsyncMock()
    d = Dispatcher(settings=_settings(), inbound_queue=q,
                   message_handler=handler, card_handler=AsyncMock())
    await d.start()
    await q.put({"header": {"event_type": "im.message.receive_v1", "event_id": "e3"},
                 "event": _msg_event(chat_type="group", text="@bot help",
                                     mentions=[{"id": {"open_id": "bot_x"}, "name": "MyBot"}])})
    await asyncio.sleep(0.05)
    handler.assert_awaited_once()
    await d.stop()


@pytest.mark.asyncio
async def test_card_action_dispatches():
    q = asyncio.Queue()
    h = AsyncMock()
    card = AsyncMock()
    d = Dispatcher(settings=_settings(), inbound_queue=q,
                   message_handler=h, card_handler=card)
    await d.start()
    await q.put({"header": {"event_type": "card.action.trigger", "event_id": "e4"},
                 "event": {"operator": {"open_id": "ou_test"},
                           "action": {"value": {"memorial_id": "m1", "action": "approve"}},
                           "context": {"open_chat_id": "oc"}}})
    await asyncio.sleep(0.05)
    card.assert_awaited_once()
    fa: FeishuCardAction = card.await_args.args[0]
    assert fa.value["memorial_id"] == "m1"
    await d.stop()


@pytest.mark.asyncio
async def test_unallowlisted_silently_dropped():
    q = asyncio.Queue()
    h = AsyncMock()
    d = Dispatcher(settings=_settings(allowed=("ou_only",)), inbound_queue=q,
                   message_handler=h, card_handler=AsyncMock())
    await d.start()
    await q.put({"header": {"event_type": "im.message.receive_v1", "event_id": "e5"},
                 "event": _msg_event(sender="ou_NOT_ALLOWED")})
    await asyncio.sleep(0.05)
    h.assert_not_awaited()
    await d.stop()
```

- [ ] **Step 2: 运行 + 提交**
```bash
uv run pytest tests/gateway/feishu/test_dispatcher.py -v
```

```bash
git add tests/gateway/feishu/test_dispatcher.py
git commit -m "test(feishu): dispatcher（p2p/群@/卡片/allowlist）"
```

### Task 8.7: 覆盖率核对

- [ ] **Step 1: 跑全量测试 + 覆盖率**

```bash
uv run pytest tests/gateway/feishu/ --cov=src/tianshu/gateway/feishu --cov-report=term-missing
```

Expected: `src/tianshu/gateway/feishu` 总覆盖率 ≥ 80%。如某文件低于 80%，回到对应 Task 补测试。

- [ ] **Step 2: 提交（如有补测）**

### Task 8.8: 用户配置文档

**Files:**
- Create: `docs/ops/feishu-setup.md`

- [ ] **Step 1: 写最小可用配置 + 故障排查**

```markdown
# 飞书机器人配置指南

## 概述

天枢支持作为飞书 / Lark 机器人接入，提供两个能力：
1. **入口**：在飞书私聊或群聊里 @ 机器人，创建/续接敕令。
2. **审批**：审批弹窗可通过飞书卡片送达，与 web 弹窗双通道并行（任一侧响应即生效）。

## 步骤 1：在飞书开发者后台创建应用

- 飞书 China：https://open.feishu.cn/
- Lark 国际：https://open.larksuite.com/

创建应用后：
1. 在「凭证与基础信息」获取 **App ID** 与 **App Secret**
2. 启用「机器人」能力
3. 在「事件订阅」添加事件：
   - `im.message.receive_v1`（消息接收）
   - `card.action.trigger`（卡片按钮点击）
4. 在「权限管理」开启：
   - `im:message`
   - `im:message.group_at_msg`
   - `im:message.p2p_msg`
   - `im:message:send_as_bot`

## 步骤 2：配置环境变量

最小可用配置（WebSocket 模式，单人）：

\`\`\`bash
TIANSHU_FEISHU_APP_ID=cli_xxx
TIANSHU_FEISHU_APP_SECRET=secret_xxx
TIANSHU_FEISHU_ALLOWED_USERS=ou_你自己的_open_id
\`\`\`

完整配置：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `TIANSHU_FEISHU_APP_ID` | ✅ | — | 飞书 App ID（空 = 不启用） |
| `TIANSHU_FEISHU_APP_SECRET` | ✅ | — | 飞书 App Secret |
| `TIANSHU_FEISHU_ALLOWED_USERS` | ✅ | — | 逗号分隔的 open_id 白名单 |
| `TIANSHU_FEISHU_DOMAIN` | — | `feishu` | `feishu` 或 `lark` |
| `TIANSHU_FEISHU_CONNECTION_MODE` | — | `websocket` | `websocket` 或 `webhook` |
| `TIANSHU_FEISHU_HOME_CHANNEL` | — | _(空)_ | cron 触发结果 / 无源审批兜底 chat_id |
| `TIANSHU_FEISHU_ENCRYPT_KEY` | — | _(空)_ | webhook 模式签名密钥 |
| `TIANSHU_FEISHU_VERIFICATION_TOKEN` | — | _(空)_ | webhook 模式 token 校验 |
| `TIANSHU_FEISHU_BOT_OPEN_ID` | — | _(空)_ | 群 @ 检测 |
| `TIANSHU_FEISHU_WEBHOOK_PATH` | — | `/feishu/webhook` | webhook 模式路径 |

## 步骤 3：启动 + 验证

启动后日志应包含：

\`\`\`
[feishu] starting (mode=websocket, app=cli_xxx)
[feishu/ws] started (app=cli_xxx, domain=feishu)
\`\`\`

在飞书私聊机器人发送 "你好" → 应收到 "✅ 已收到（敕令 #xxx）" 回复。

## 命令

| 命令 | 说明 |
|------|------|
| `<纯文本>` | 续接当前会话锚定的敕令（无锚则新建） |
| `/new <目标>` | 显式新建敕令 |
| `/status [id]` | 查看敕令状态 |
| `/cancel [id]` | 取消敕令 |
| `/set-home` | 显示当前 chat_id（用于配置 home channel） |
| `/help` | 显示帮助 |

## 故障排查

| 问题 | 原因 / 解决 |
|------|------------|
| 启动报 `lark-oapi not installed` | `uv sync` 同步依赖 |
| 启动报 `ALLOWED_USERS is required` | v1 强制要求白名单，避免误开放 |
| 群里 @ 机器人无响应 | 检查 `FEISHU_BOT_OPEN_ID` 或 `BOT_NAME` 是否配置正确 |
| 卡片点击报 200340 | 在飞书后台启用「Interactive Card」能力，并配置 Card Request URL（仅 webhook 模式需要） |
| 启动报 `Another tianshu process is using feishu app` | 有其它实例占用同一 app_id；停掉旧进程或换 app_id |
| 飞书发消息后 web 不显示通知 | 飞书来源敕令的事件流进 EventBus，web 应通过 WebSocket 收到 `edict.submitted`。检查浏览器 console |

## 参考

- 设计文档：`docs/superpowers/specs/2026-04-28-feishu-bot-design.md`
- 实施计划：`docs/superpowers/plans/2026-04-28-feishu-bot.md`
```

- [ ] **Step 2: 提交**
```bash
git add docs/ops/feishu-setup.md
git commit -m "docs(feishu): 用户配置指南"
```

---

## 完成检查清单

- [ ] 所有 8 个 Step 已完成并提交
- [ ] `uv run pytest tests/gateway/feishu/ --cov=src/tianshu/gateway/feishu` 覆盖率 ≥ 80%
- [ ] `uv run ruff check src/tianshu/gateway/feishu/ tests/gateway/feishu/` 无 lint 错
- [ ] 端到端验证：发飞书消息 → 创建敕令 → 收到回复 → 审批卡片可点击
- [ ] 现有 tests 全部通过：`uv run pytest tests/ -x`
- [ ] 文档：`docs/ops/feishu-setup.md` 完整可用
- [ ] 旧 `notifier/channels/feishu.py` 标记 deprecated，与新 app bot 模式互斥但兼容

---

## 风险与回退

如某 Step 阻塞，可通过删除 `app.py` 中 `if feishu_settings.enabled:` 段或清空 `TIANSHU_FEISHU_APP_ID` 即可关闭机器人，不影响现有功能。

---

## 与 Spec 的对应

| Spec § | Plan Step / Task |
|--------|------------------|
| §3 整体架构 | Step 1.3-1.4 + 各模块步骤 |
| §4 配置层 | Step 1.1, Task 1.4 |
| §5 连接层 | Step 2 (Webhook), Step 6 (WS) |
| §6 入站调度 | Step 2.4, Step 7.1 (批处理) |
| §7 敕令桥接 + X1 | Step 3 (含 Task 8.2 已结案分支测试) |
| §8 审批桥接（双通道）| Step 5 |
| §9 出站 | Step 4 |
| §10 错误处理 / 限流 | Step 7.2-7.3 |
| §11 安全 | Step 2.2 (security.py) |
| §12 测试策略 | Step 8 |
| §13 部署 / 运维 | Task 1.4 (启动检查) + Task 8.8 (文档) |
| §16 一致性对照 | 隐含在各模块（lark-oapi、卡片协议、双连接模式、配置同名） |
