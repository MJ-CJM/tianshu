# 飞书机器人 v2 极简模型 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 v1.1 双模式飞书机器人简化为「聊天敕令 = 普通敕令 + 一个标记」的极简模型 —— 删除 IntentParser，纯文本走 executor 自然回应（含工具调用），首次接入自动建 chat 敕令。

**Architecture:** 复用 v1.1 已有结构（ModeRouter / AssistantBranch / EdictBranch / PersonaRenderer / CardBuilder），删除 IntentParser，改造 AssistantBranch 纯文本路径走 EdictBridge.continue_or_create，让 Executor 自适应处理。新增 /clear 命令 + storage list_edicts 支持 metadata.assistant_chat filter。

**Tech Stack:** Python 3.13 / FastAPI / SQLite (json_extract) / lark-oapi / pytest

**Spec:** `docs/superpowers/specs/2026-04-29-feishu-assistant-mode-design.md`

**用户偏好（来自 memory）：** 功能优先，测试最后补 —— Step 1-8 实施功能改动；Step 9 集中改测试；Step 10 文档。

**用户特殊约束：** 本地 commit 即可（不 push），整体完成后用户决定 push 时机。

**v1.1 现状摘要（Step 起点）：**
- `commit 006c9f4` 已实施 v1.1 双模式 + 192 测试
- `IntentParser` 在 `assistant_branch.py:194-197` 被 `_handle_natural_language` 调用
- `Storage.list_edicts(status, search, limit, offset) -> tuple[list[Edict], int]` 无 metadata filter
- AssistantBranch._cmd_list 当前用 `edicts, _total = self._storage.list_edicts(...)` 直接全列

---

## File Structure

```
删除：
- src/tianshu/gateway/feishu/intent_parser.py        ❌ 整个文件
- tests/gateway/feishu/test_intent_parser.py         ❌ 整个文件

修改：
- src/tianshu/gateway/feishu/assistant_branch.py     -50 +30 行
  · 删除 IntentParser 注入与 _handle_natural_language 中 intent 解析逻辑
  · 纯文本改走 EdictBridge.continue_or_create
  · 新增 _cmd_clear

- src/tianshu/gateway/feishu/__init__.py             -25 +10 行
  · 删除 IntentParser 实例化与 reload 路径
  · FeishuBot.__init__ 不再接 provider_manager（仅 IntentParser 用）
  · 新增 _ensure_chat_anchor 首次接入自动建 chat 敕令

- src/tianshu/gateway/feishu/edict_branch.py         -3 +5 行
  · _cmd_exit 改为切到 chat 敕令而非清 anchor

- src/tianshu/gateway/feishu/edict_bridge.py         +20 行
  · 新增 ensure_chat_edict(chat_id, sender) -> str（首次接入用）

- src/tianshu/storage.py                             +10 行
  · list_edicts 加 exclude_assistant_chat: bool 参数 + SQL json_extract filter

- src/tianshu/gateway/feishu/settings.py             -2 行
  · 删除 intent_llm_enabled 字段（v1.1 兼容性 fallback 留在 from_global_settings 里 setdefault）

- src/tianshu/gateway/tongzheng_api.py               -8 行
  · GET / PUT 不再返回 intent_llm_enabled（保留向后兼容 setdefault 即可）

- src/tianshu/gateway/feishu/persona_renderer.py    +3 行
  · help_assistant 加 /clear 命令说明

- web/src/api/tongzheng.ts                          -2 行
  · 类型定义中 intent_llm_enabled 标 deprecated（可选）

- web/src/pages/TongzhengPage.tsx                   -25 行
  · 删除「启用 LLM 意图增强」checkbox（v2 不再使用）

新增：
- docs/ops/feishu-assistant-mode.md                  整体改写
  · 反映 v2 极简模型；移除 IntentParser 段落

测试：
- tests/gateway/feishu/test_assistant_branch.py      -约 30 行 + 30 行
  · 删除 _handle_natural_language IntentParser mock 测试
  · 新增 _cmd_clear 测试
  · 新增「纯文本走 continue_or_create」测试

- tests/gateway/feishu/test_edict_branch.py          +5 行
  · /exit 切到 chat 敕令而非清 anchor 测试

- tests/gateway/feishu/test_card_builder.py          +5 行
  · build_list_card 不显示 assistant_chat=true 测试（间接：通过 storage.list_edicts 行为）

- tests/test_storage.py                              +10 行
  · list_edicts(exclude_assistant_chat=True) 单元测试
```

---

## Step 1: 删除 IntentParser 模块 + 关联测试

**目标：** v2 不再需要意图解析，从代码库中移除。先做最简单的删除，避免后续 import 误用。

**Files:**
- Delete: `src/tianshu/gateway/feishu/intent_parser.py`
- Delete: `tests/gateway/feishu/test_intent_parser.py`

### Sub-tasks

- [ ] **Step 1.1: 删除模块文件**

```bash
cd /Users/chenjiamin/tiangong/tianshu
rm src/tianshu/gateway/feishu/intent_parser.py
rm tests/gateway/feishu/test_intent_parser.py
```

- [ ] **Step 1.2: 验证删除（应该有 import 错误，正常 —— Step 2 修复）**

```bash
.venv/bin/python -c "from tianshu.gateway.feishu.intent_parser import IntentParser" 2>&1 | tail -2
```
Expected: `ModuleNotFoundError: No module named 'tianshu.gateway.feishu.intent_parser'`

- [ ] **Step 1.3: commit**

```bash
git add -A src/tianshu/gateway/feishu/intent_parser.py tests/gateway/feishu/test_intent_parser.py
git commit -m "refactor(feishu): 删除 IntentParser 模块（v2 极简模型不再需要意图解析）"
```

---

## Step 2: 修改 AssistantBranch — 删除 IntentParser 依赖 + 纯文本改 continue_or_create

**目标：** 让纯文本走 executor 自然回应（与 EdictBranch 行为对称），删除 silent reply / IntentParser 路径。

**Files:**
- Modify: `src/tianshu/gateway/feishu/assistant_branch.py`

### Sub-tasks

- [ ] **Step 2.1: 删除文件中所有 IntentParser 引用**

修改顺序（每处都改）：

(A) 顶部 docstring（行 4 附近）：
```python
"""助手模式（anchor=NULL）命令路由。

支持命令：/new /list /select /budget /menu /help /status /cancel /clear
不支持的纯文本：续接当前 anchor 敕令（让 executor + persona LLM 自然处理）。
"""
```

(B) imports 删除（约行 19）：
```python
# 删除：
    from tianshu.gateway.feishu.intent_parser import IntentParser
```

(C) `__init__` 签名（约行 30-55）：把 `intent_parser` 参数移除，把 `self._intent_parser` 字段移除。

```python
def __init__(
    self,
    *,
    storage: "Storage",
    anchor: "SessionAnchor",
    edict_bridge: "EdictBridge",
    outbound: "FeishuOutbound",
    renderer: "PersonaRenderer",
    card_builder: "CardBuilder",
) -> None:
    self._storage = storage
    self._anchor = anchor
    self._edict_bridge = edict_bridge
    self._outbound = outbound
    self._renderer = renderer
    self._card_builder = card_builder
```

(D) 完整替换 `_handle_natural_language`（约行 191-225）：

```python
async def _handle_natural_language(
    self, msg: "FeishuMessage", ctx: "ModeContext", text: str,
) -> None:
    """纯文本（无 / 前缀）→ 续接当前 anchor 敕令，让 executor + persona LLM 自然处理。

    v2：anchor 必为 chat 敕令（首次接入由 _ensure_chat_anchor 保证），
    所以等价于 EdictBridge.continue_or_create 行为。
    """
    from tianshu.gateway.feishu.edict_bridge import EdictBusyError
    try:
        edict_id = await self._edict_bridge.continue_or_create(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, text=text,
        )
    except EdictBusyError as exc:
        await self._reply(msg.chat_id, str(exc))
        return
    await self._reply(msg.chat_id, self._renderer.edict_received_reply(edict_id))
```

⚠️ **注意**：删除原方法体中所有 `self._intent_parser` / `intent_result` / `_synthesize_command` / `replace` import 等等。整段替换为上面的简洁版本。

(E) 删除 `_synthesize_command` 静态方法（v2 不再需要把 intent 反映射为命令）：

```python
# 删除整个方法（约行 227-247）
@staticmethod
def _synthesize_command(intent: str, args: dict) -> str | None:
    ...
```

- [ ] **Step 2.2: 验证 imports + 行为**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.assistant_branch import AssistantBranch
import inspect
sig = inspect.signature(AssistantBranch.__init__)
assert 'intent_parser' not in sig.parameters, 'intent_parser 应已移除'
assert '_synthesize_command' not in dir(AssistantBranch), '_synthesize_command 应已删除'
print('AssistantBranch IntentParser 依赖已清除')
"
ruff check src/tianshu/gateway/feishu/assistant_branch.py 2>&1 | tail -3
```

Expected: `IntentParser 依赖已清除` + ruff 全绿（unused import / unused parameter 等错误均已清理）。

- [ ] **Step 2.3: commit**

```bash
git add src/tianshu/gateway/feishu/assistant_branch.py
git commit -m "refactor(feishu): AssistantBranch 删除 IntentParser 依赖 + 纯文本改走 continue_or_create"
```

---

## Step 3: 修改 FeishuBot.__init__ + reload — 移除 IntentParser 实例化

**目标：** v2 不再注入 IntentParser；同步删除 reload 中的相关逻辑。

**Files:**
- Modify: `src/tianshu/gateway/feishu/__init__.py`

### Sub-tasks

- [ ] **Step 3.1: 删除 IntentParser import**

定位顶部 import 段（约行 21）：
```python
# 删除：
from tianshu.gateway.feishu.intent_parser import IntentParser
```

- [ ] **Step 3.2: 删除 __init__ 中 IntentParser 实例化逻辑（约行 105-115）**

把这段整体删除：
```python
# 删除：
self._intent_parser: IntentParser | None = None
if (
    settings.intent_llm_enabled
    and provider_manager is not None
    and persona_loader is not None
):
    self._intent_parser = IntentParser(
        persona_loader=persona_loader,
        provider_manager=provider_manager,
        persona_id=settings.assistant_persona_id,
    )
```

- [ ] **Step 3.3: 修改 AssistantBranch 实例化**

定位 AssistantBranch 构造（约行 117-126），移除 `intent_parser=self._intent_parser` 参数：
```python
self._assistant_branch = AssistantBranch(
    storage=storage,
    anchor=self._anchor,
    edict_bridge=self._edict_bridge,
    outbound=self._outbound,
    renderer=self._renderer,
    card_builder=self._card_builder,
)
```

- [ ] **Step 3.4: 删除 reload 中 IntentParser 切换逻辑**

定位 `reload` 方法（grep `async def reload`），删除其中所有提到 `_intent_parser` 的段（包括 `IntentParser(...)` 重建、`set_persona` 调用、`assistant_branch._intent_parser =` 等）。

- [ ] **Step 3.5: 验证 + commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu import FeishuBot
import inspect
src = inspect.getsource(FeishuBot)
assert 'IntentParser' not in src, 'FeishuBot 仍引用 IntentParser'
assert '_intent_parser' not in src
print('FeishuBot IntentParser 依赖已清除')
"
ruff check src/tianshu/gateway/feishu/__init__.py 2>&1 | tail -3

git add src/tianshu/gateway/feishu/__init__.py
git commit -m "refactor(feishu): FeishuBot 移除 IntentParser 实例化与 reload 路径"
```

---

## Step 4: 修改 settings.py 与 tongzheng_api.py — intent_llm_enabled 字段处理

**目标：** v2 不再用 intent_llm_enabled，但保留向后兼容（旧 channel_configs 含此字段不报错）。

**Files:**
- Modify: `src/tianshu/gateway/feishu/settings.py`
- Modify: `src/tianshu/gateway/tongzheng_api.py`
- Modify: `src/tianshu/config.py`

### Sub-tasks

- [ ] **Step 4.1: settings.py 标 deprecated**

定位 `FeishuSettings` dataclass（约行 11-25），把 `intent_llm_enabled` 字段保留**但加 deprecated 注释**（向后兼容旧 channel_configs / env）：

```python
@dataclass(frozen=True)
class FeishuSettings:
    # ... 已有字段 ...
    assistant_persona_id: str = "tongzheng"
    intent_llm_enabled: bool = True   # DEPRECATED v2: v2 极简模型不再使用 IntentParser；保留为向后兼容
    disable_assistant_mode: bool = False
```

`from_global_settings` 工厂函数中保留 `getattr(s, "feishu_intent_llm_enabled", True)` 不动（向后兼容）。

- [ ] **Step 4.2: tongzheng_api.py 不变（保留响应字段，避免前端类型错）**

`FeishuChannelConfig` 中 `intent_llm_enabled` 字段保留（v2 不读但不删，避免破坏前端契约）。

- [ ] **Step 4.3: config.py 不变**

`feishu_intent_llm_enabled: bool = True` 字段保留（向后兼容）。

- [ ] **Step 4.4: 验证 + commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
ruff check src/tianshu/gateway/feishu/settings.py 2>&1 | tail -3
.venv/bin/python -c "
from tianshu.gateway.feishu.settings import FeishuSettings
# 字段仍存在（向后兼容）
assert 'intent_llm_enabled' in FeishuSettings.__dataclass_fields__
print('intent_llm_enabled 已标 DEPRECATED 但仍存在（向后兼容）')
"

git add src/tianshu/gateway/feishu/settings.py
git commit -m "refactor(feishu): intent_llm_enabled 字段标 DEPRECATED（v2 不读，保留向后兼容）"
```

---

## Step 5: Storage.list_edicts 支持 exclude_assistant_chat filter

**目标：** SQL 端排除 chat 敕令，避免 `/list` 显示助手对话。

**Files:**
- Modify: `src/tianshu/storage.py`

### Sub-tasks

- [ ] **Step 5.1: 修改 list_edicts 签名 + SQL**

定位 `def list_edicts(...)`（约行 640），替换为：

```python
def list_edicts(
    self,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    exclude_assistant_chat: bool = False,
) -> tuple[list[Edict], int]:
    """列敕令。

    exclude_assistant_chat=True 时过滤掉 metadata.assistant_chat=true 的聊天敕令。
    SQL 用 json_extract(metadata_json, '$.assistant_chat') 实现。
    """
    conditions: list[str] = []
    params: list[str | int] = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if search:
        conditions.append("(title LIKE ? OR goal LIKE ?)")
        params.append(f"%{search}%")
        params.append(f"%{search}%")
    if exclude_assistant_chat:
        conditions.append(
            "(json_extract(metadata_json, '$.assistant_chat') IS NULL "
            "OR json_extract(metadata_json, '$.assistant_chat') != 1)"
        )
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with self._lock:
        rows = self._conn.execute(
            f"SELECT * FROM edicts{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        total = self._conn.execute(
            f"SELECT COUNT(*) FROM edicts{where}",
            params,
        ).fetchone()[0]
    return [self._row_to_edict(r) for r in rows], total
```

⚠️ **SQLite json_extract 行为**：JSON `true` 在 json_extract 中返回 `1`（SQLite 存储为整数），所以条件用 `!= 1`。

- [ ] **Step 5.2: 验证**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.storage import Storage
from tianshu.models.edict import Edict

s = Storage('/tmp/list_filter_test.db')
s.init_db()

# 普通敕令
e1 = Edict(title='普通', goal='做事')
s.save_edict(e1)
# 聊天敕令
e2 = Edict(title='聊天', goal='对话', metadata={'assistant_chat': True})
s.save_edict(e2)

all_edicts, all_total = s.list_edicts()
print(f'all: total={all_total}')
assert all_total == 2

filtered, ftotal = s.list_edicts(exclude_assistant_chat=True)
print(f'filtered: total={ftotal}')
assert ftotal == 1
assert filtered[0].title == '普通'
print('OK')
"
rm -f /tmp/list_filter_test.db
ruff check src/tianshu/storage.py 2>&1 | tail -3
```

Expected: `all: total=2 / filtered: total=1 / OK` + ruff 全绿（pre-existing F841 不算）。

- [ ] **Step 5.3: commit**

```bash
git add src/tianshu/storage.py
git commit -m "feat(storage): list_edicts 加 exclude_assistant_chat filter（SQL json_extract）"
```

---

## Step 6: AssistantBranch._cmd_list 与 _cmd_select / _cmd_status / _cmd_cancel 接入 filter

**目标：** `/list` 默认隐藏聊天敕令；`/select <id>`、`/status`、`/cancel` 通过 ID 前缀查找时也应排除聊天敕令（避免用户误切到聊天敕令）。

**Files:**
- Modify: `src/tianshu/gateway/feishu/assistant_branch.py`

### Sub-tasks

- [ ] **Step 6.1: 修改 _cmd_list（约行 102-108）**

```python
async def _cmd_list(self, msg: "FeishuMessage", ctx: "ModeContext", filter_arg: str) -> None:
    status_filter = self._parse_filter(filter_arg)
    status_value = (
        status_filter.value if hasattr(status_filter, "value") else None
    )
    edicts, _total = self._storage.list_edicts(
        status=status_value, limit=10, offset=0,
        exclude_assistant_chat=True,   # v2: 隐藏聊天敕令
    )
    if not edicts:
        await self._reply(
            msg.chat_id,
            f"{self._renderer.assistant_tag()} 暂无敕令。输入 /new <目标> 颁布第一道",
        )
        return
    card = self._card_builder.build_list_card(
        edicts=edicts, current_anchor=ctx.edict_id,
    )
    await self._outbound.send_card(msg.chat_id, card)
```

⚠️ 检查 `_parse_filter` 当前返回类型（v1.1 implementer 修正可能让它返回 EdictStatus enum），调整 `status_value` 取值方式。

- [ ] **Step 6.2: 修改 _cmd_select（约行 124-145，含 list_edicts 调用）**

把 `self._storage.list_edicts(limit=200, offset=0)` 改为 `self._storage.list_edicts(limit=200, offset=0, exclude_assistant_chat=True)`。

- [ ] **Step 6.3: 修改 _find_by_prefix（约行 252-256）**

```python
def _find_by_prefix(self, prefix: str):
    if len(prefix) < 6:
        return None
    edicts, _total = self._storage.list_edicts(
        limit=200, offset=0, exclude_assistant_chat=True,
    )
    for e in edicts:
        if e.id.startswith(prefix):
            return e
    return None
```

- [ ] **Step 6.4: 验证 + commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
ruff check src/tianshu/gateway/feishu/assistant_branch.py 2>&1 | tail -3
git add src/tianshu/gateway/feishu/assistant_branch.py
git commit -m "feat(feishu): AssistantBranch /list /select /status /cancel 排除聊天敕令"
```

---

## Step 7: EdictBridge.ensure_chat_edict — 首次接入自动建 chat 敕令

**目标：** 飞书首次发任意消息（无 anchor）→ 自动创建 `metadata.assistant_chat=true` 敕令并设 anchor。**取代** v1.1 的"无 anchor 时 silent reply"。

**Files:**
- Modify: `src/tianshu/gateway/feishu/edict_bridge.py`

### Sub-tasks

- [ ] **Step 7.1: 在 EdictBridge 类追加 ensure_chat_edict 方法**

定位 `EdictBridge` 类（grep `class EdictBridge`），在 `create_new` 方法附近追加：

```python
async def ensure_chat_edict(
    self, *, chat_id: str, sender_open_id: str,
) -> str:
    """确保该 chat 有一个聊天敕令（assistant_chat=true）作为 anchor。

    若 anchor 已存在 → 直接返回 anchor edict_id（无论是聊天敕令还是业务敕令）
    若 anchor 不存在 → 创建一个 metadata.assistant_chat=true 敕令并设 anchor
    """
    existing = self._anchor.get(chat_id)
    if existing:
        return existing
    edict = Edict(
        title=f"飞书助手对话 - {chat_id[:12]}",
        goal="持续对话上下文",
        source="channel",
        submitter="emperor",
        metadata={
            "channel": "feishu",
            "chat_id": chat_id,
            "feishu_user": sender_open_id,
            "assistant_chat": True,  # v2 关键标记
        },
    )
    self._storage.save_edict(edict)
    memorial = Memorial(
        edict_id=edict.id, instruction=edict.goal, status=TaskStatus.SUBMITTED,
    )
    self._storage.save_memorial(memorial)
    self._anchor.set(chat_id, edict.id)
    self._event_bus.fire(make_event(
        "edict.submitted",
        edict_id=edict.id, memorial_id=memorial.id,
        producer="feishu_bot",
        payload={"goal": edict.goal, "channel": "feishu", "chat_id": chat_id,
                 "assistant_chat": True},
    ))
    logger.info(
        "[feishu/edict] auto-created chat edict %s for chat=%s",
        edict.id, chat_id,
    )
    return edict.id
```

⚠️ 确认顶部已 import `Edict / Memorial / TaskStatus / make_event`（v1 应已有）。

- [ ] **Step 7.2: 验证**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
import asyncio
from unittest.mock import MagicMock, AsyncMock
from tianshu.gateway.feishu.edict_bridge import EdictBridge
from tianshu.gateway.feishu.session_anchor import SessionAnchor
from tianshu.bus.event_bus import EventBus
from tianshu.storage import Storage

s = Storage('/tmp/ensure_chat_test.db')
s.init_db()
bus = EventBus(storage=s)
anchor = SessionAnchor(s)
executor = MagicMock(); executor.execute_edict = AsyncMock(); executor.running_tasks = set()
b = EdictBridge(storage=s, event_bus=bus, executor=executor, anchor=anchor)

async def run():
    eid = await b.ensure_chat_edict(chat_id='oc_test', sender_open_id='ou_a')
    edict = s.get_edict(eid)
    assert edict.metadata.get('assistant_chat') is True
    assert anchor.get('oc_test') == eid
    # 第二次调用应返回同一个
    eid2 = await b.ensure_chat_edict(chat_id='oc_test', sender_open_id='ou_a')
    assert eid2 == eid
    print('OK eid=%s' % eid)
asyncio.run(run())
"
rm -f /tmp/ensure_chat_test.db
ruff check src/tianshu/gateway/feishu/edict_bridge.py 2>&1 | tail -3
```

Expected: `OK eid=...` + ruff 全绿。

- [ ] **Step 7.3: commit**

```bash
git add src/tianshu/gateway/feishu/edict_bridge.py
git commit -m "feat(feishu): EdictBridge.ensure_chat_edict — 首次接入自动建 assistant_chat=true 敕令"
```

---

## Step 8: ModeRouter 与 FeishuBot._on_message — 调用 ensure_chat_edict

**目标：** 飞书消息处理流水线在最早阶段调用 `ensure_chat_edict`，保证 anchor 永远存在（取代 v1.1"无 anchor 时进助手 silent reply"逻辑）。

**Files:**
- Modify: `src/tianshu/gateway/feishu/mode_router.py`
- Modify: `src/tianshu/gateway/feishu/__init__.py`

### Sub-tasks

- [ ] **Step 8.1: 修改 ModeRouter.dispatch — 调用 ensure_chat_edict 前置**

定位 `class ModeRouter`，修改 `dispatch` 方法：

```python
def __init__(
    self,
    *,
    anchor: "SessionAnchor",
    assistant_branch: "AssistantBranch",
    edict_branch: "EdictBranch",
    edict_bridge: "EdictBridge",   # 新增
) -> None:
    self._anchor = anchor
    self._assistant = assistant_branch
    self._edict = edict_branch
    self._edict_bridge = edict_bridge
```

(b) `dispatch` 前置 ensure_chat_edict（消息一来就保证 anchor）：

```python
async def dispatch(self, msg: "FeishuMessage") -> None:
    """主入口：保证 anchor 存在 → 判断模式 → 转给对应分支。"""
    # v2: 首次接入自动建 chat 敕令（保证 anchor 永远存在）
    await self._edict_bridge.ensure_chat_edict(
        chat_id=msg.chat_id, sender_open_id=msg.sender_open_id,
    )
    ctx = self.resolve_mode(msg.chat_id)
    ctx = ModeContext(
        mode=ctx.mode, chat_id=ctx.chat_id,
        sender_open_id=msg.sender_open_id, edict_id=ctx.edict_id,
    )
    logger.info(
        "[feishu/mode] chat=%s mode=%s text=%.80s",
        msg.chat_id, ctx.mode, msg.text,
    )
    if ctx.mode == "assistant":
        await self._assistant.handle(msg, ctx)
    else:
        await self._edict.handle(msg, ctx)
```

(c) `resolve_mode` 微调：`mode` 判定加 metadata.assistant_chat：

```python
def resolve_mode(self, chat_id: str) -> ModeContext:
    """根据当前 anchor 状态构造 ModeContext。

    v2: anchor 永远存在（ensure_chat_edict 保证）。
    - anchor 指向 metadata.assistant_chat=true 敕令 → assistant
    - anchor 指向其它（业务敕令）→ edict
    """
    edict_id = self._anchor.get(chat_id)
    if not edict_id:
        # 极端边界：理论上 dispatch 已 ensure，这里防御性返回 assistant
        return ModeContext(
            mode="assistant", chat_id=chat_id,
            sender_open_id="", edict_id=None,
        )
    # 读 edict 判断是否聊天敕令
    edict = self._storage_get_edict(edict_id) if hasattr(self, "_storage_get_edict") else None
    is_chat = bool(edict and edict.metadata and edict.metadata.get("assistant_chat"))
    return ModeContext(
        mode="assistant" if is_chat else "edict",
        chat_id=chat_id,
        sender_open_id="", edict_id=edict_id,
    )
```

⚠️ ModeRouter 之前不持有 storage —— 加一个回调或者直接持有 storage：

```python
def __init__(
    self,
    *,
    anchor: "SessionAnchor",
    assistant_branch: "AssistantBranch",
    edict_branch: "EdictBranch",
    edict_bridge: "EdictBridge",
    storage: "Storage",   # 新增
) -> None:
    self._anchor = anchor
    self._assistant = assistant_branch
    self._edict = edict_branch
    self._edict_bridge = edict_bridge
    self._storage = storage

def resolve_mode(self, chat_id: str) -> ModeContext:
    edict_id = self._anchor.get(chat_id)
    if not edict_id:
        return ModeContext(
            mode="assistant", chat_id=chat_id,
            sender_open_id="", edict_id=None,
        )
    edict = self._storage.get_edict(edict_id)
    is_chat = bool(edict and edict.metadata and edict.metadata.get("assistant_chat"))
    return ModeContext(
        mode="assistant" if is_chat else "edict",
        chat_id=chat_id,
        sender_open_id="", edict_id=edict_id,
    )
```

TYPE_CHECKING 块加 `from tianshu.storage import Storage`。

- [ ] **Step 8.2: 修改 FeishuBot ModeRouter 实例化**

定位 FeishuBot.__init__ 中 `self._mode_router = ModeRouter(...)`（约行 135-139），加新参数：

```python
self._mode_router = ModeRouter(
    anchor=self._anchor,
    assistant_branch=self._assistant_branch,
    edict_branch=self._edict_branch,
    edict_bridge=self._edict_bridge,    # 新增
    storage=storage,                     # 新增
)
```

- [ ] **Step 8.3: 验证 + commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
ruff check src/tianshu/gateway/feishu/mode_router.py src/tianshu/gateway/feishu/__init__.py 2>&1 | tail -3
.venv/bin/python -c "
from tianshu.gateway.feishu.mode_router import ModeRouter
import inspect
sig = inspect.signature(ModeRouter.__init__)
assert 'edict_bridge' in sig.parameters
assert 'storage' in sig.parameters
print('ModeRouter 已注入 edict_bridge + storage')
"

git add src/tianshu/gateway/feishu/mode_router.py src/tianshu/gateway/feishu/__init__.py
git commit -m "feat(feishu): ModeRouter 前置 ensure_chat_edict + 基于 metadata.assistant_chat 判定 mode"
```

---

## Step 9: AssistantBranch._cmd_clear + EdictBranch._cmd_exit

**目标：**
- `/clear`：归档当前 chat 敕令 + 新建 + 切 anchor
- `/exit`：业务敕令模式下切回 chat 敕令而非清 anchor（v2 行为）

**Files:**
- Modify: `src/tianshu/gateway/feishu/assistant_branch.py`
- Modify: `src/tianshu/gateway/feishu/edict_branch.py`
- Modify: `src/tianshu/gateway/feishu/persona_renderer.py`

### Sub-tasks

- [ ] **Step 9.1: AssistantBranch 加 /clear 命令**

定位 AssistantBranch.handle 的 elif 链（约行 70-90），追加分支：

```python
elif cmd == "/clear":
    await self._cmd_clear(msg, ctx)
```

并新增 `_cmd_clear` 方法（在类内）：

```python
async def _cmd_clear(self, msg: "FeishuMessage", ctx: "ModeContext") -> None:
    """归档当前聊天敕令 + 新建 + 切 anchor。

    仅在 anchor 指向聊天敕令时可用。
    """
    if not ctx.edict_id:
        await self._reply(
            msg.chat_id,
            f"{self._renderer.assistant_tag()} 当前无活跃聊天会话",
        )
        return
    edict = self._storage.get_edict(ctx.edict_id)
    if not edict or not (edict.metadata and edict.metadata.get("assistant_chat")):
        await self._reply(
            msg.chat_id,
            "/clear 仅在聊天会话中可用。当前是业务敕令，请用 /exit 退出后再 /clear。",
        )
        return
    # 归档当前聊天敕令
    self._storage.update_edict_status(edict.id, EdictStatus.COMPLETED.value)
    # 新建一个新的聊天敕令（清空 anchor 让 ensure_chat_edict 自然新建）
    self._storage.delete_feishu_anchor(msg.chat_id)
    new_eid = await self._edict_bridge.ensure_chat_edict(
        chat_id=msg.chat_id, sender_open_id=msg.sender_open_id,
    )
    await self._reply(
        msg.chat_id,
        f"{self._renderer.assistant_tag()} 已归档上轮对话 #{edict.id[:8]}，开启新会话 #{new_eid[:8]}",
    )
```

- [ ] **Step 9.2: persona_renderer 加 /clear 帮助文案**

修改 `help_assistant` 方法（约行 80-90）：

```python
def help_assistant(self) -> str:
    return (
        f"{self.assistant_tag()} 可用命令：\n"
        "- `/new <目标>` 新建敕令\n"
        "- `/list [filter]` 查看敕令列表（filter: open/completed/all）\n"
        "- `/select <id>` 切换到指定敕令\n"
        "- `/budget` 成本概览\n"
        "- `/menu` 主菜单\n"
        "- `/clear` 归档当前对话 + 开新会话\n"   # 新增
        "- `/help` 显示帮助"
    )
```

- [ ] **Step 9.3: EdictBranch._cmd_exit — v2 切回 chat 敕令**

定位 `EdictBranch._cmd_exit`（grep `_cmd_exit`），替换：

```python
async def _cmd_exit(self, msg: "FeishuMessage", ctx: "ModeContext") -> None:
    """v2: 退出业务敕令模式 → 切到 chat 敕令（自动 ensure）。"""
    self._storage.delete_feishu_anchor(msg.chat_id)
    new_eid = await self._edict_bridge.ensure_chat_edict(
        chat_id=msg.chat_id, sender_open_id=msg.sender_open_id,
    )
    await self._reply(
        msg.chat_id,
        f"{self._renderer.edict_exit_reply()}（已切回助手 #{new_eid[:8]}）",
    )
```

⚠️ EdictBranch.__init__ 当前可能没持有 edict_bridge，已在 v1.1 实施时含 edict_bridge 参数（grep 确认）。如已在则可用；否则需要加。

- [ ] **Step 9.4: 验证 + commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.assistant_branch import AssistantBranch
src = __import__('inspect').getsource(AssistantBranch.handle)
assert '/clear' in src, '/clear 已加路由'
assert '_cmd_clear' in dir(AssistantBranch), '_cmd_clear 方法已加'
print('OK')
"
ruff check src/tianshu/gateway/feishu/assistant_branch.py src/tianshu/gateway/feishu/edict_branch.py src/tianshu/gateway/feishu/persona_renderer.py 2>&1 | tail -3

git add src/tianshu/gateway/feishu/assistant_branch.py src/tianshu/gateway/feishu/edict_branch.py src/tianshu/gateway/feishu/persona_renderer.py
git commit -m "feat(feishu): /clear 归档对话 + /exit 切回 chat 敕令（v2 行为）"
```

---

## Step 10: 通政司前端 — 隐藏 LLM 增强 checkbox

**目标：** v2 不再使用 IntentParser，前端隐藏对应 checkbox 避免误导用户。

**Files:**
- Modify: `web/src/pages/TongzhengPage.tsx`

### Sub-tasks

- [ ] **Step 10.1: 删除 LLM 增强 Form.Item**

定位 TongzhengPage.tsx 中"启用 LLM 意图增强"的 Form.Item（grep `intent_llm_enabled`），整段删除（包括 Switch）。

⚠️ 保留 `initialValues` 中 `intent_llm_enabled: true` 字段（向后兼容 PUT 请求 schema）。

- [ ] **Step 10.2: 验证 TypeScript 编译**

```bash
cd /Users/chenjiamin/tiangong/tianshu/web
pnpm tsc --noEmit 2>&1 | grep TongzhengPage | head -5
```

Expected: 无新增错误（PersonaDetailPage pre-existing 错误忽略）。

- [ ] **Step 10.3: commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add web/src/pages/TongzhengPage.tsx
git commit -m "refactor(tongzheng/web): 隐藏 LLM 意图增强 checkbox（v2 不再使用）"
```

---

## Step 11: 端到端冒烟测试 — v2 行为验证

**目标：** 验证 v2 修订后的核心路径（首次接入自动建 chat 敕令、纯文本走 executor、/clear、/exit 切回 chat 敕令）。

### Sub-tasks

- [ ] **Step 11.1: 端到端 webhook 模拟**

```bash
cd /Users/chenjiamin/tiangong/tianshu
pkill -f "uvicorn tianshu.app" 2>/dev/null || true
rm -f /tmp/feishu_v2_test.db ~/.tianshu/feishu_app_lock.test_v2
sleep 1

TIANSHU_FEISHU_APP_ID=test_v2 \
TIANSHU_FEISHU_APP_SECRET=secret \
TIANSHU_FEISHU_CONNECTION_MODE=webhook \
TIANSHU_DB_PATH=/tmp/feishu_v2_test.db \
TIANSHU_LLM_API_KEY=fake \
TIANSHU_LLM_API_BASE=http://localhost:9999 \
.venv/bin/python -m uvicorn tianshu.app:create_app --factory --port 8765 > /tmp/feishu_v2.log 2>&1 &
sleep 5

# 1. 首次发"你好" → 应自动创建 chat 敕令并 anchor
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v2_1"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v2"}},
    "message": {"chat_id": "oc_v2", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"你好\"}"}}}'
sleep 2

# 验证 anchor + assistant_chat
echo "--- 首次发消息后 ---"
sqlite3 /tmp/feishu_v2_test.db "SELECT chat_id, current_edict_id FROM feishu_session_anchor"
sqlite3 /tmp/feishu_v2_test.db "SELECT id, title, json_extract(metadata_json, '\$.assistant_chat') as is_chat FROM edicts WHERE source='channel'"

# 2. /list → 不应包含 chat 敕令
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v2_2"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v2"}},
    "message": {"chat_id": "oc_v2", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"/list\"}"}}}'
sleep 2

# 3. /new 业务敕令
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v2_3"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v2"}},
    "message": {"chat_id": "oc_v2", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"/new 写代码\"}"}}}'
sleep 2

# 4. /exit → 应切回 chat 敕令（不是清 anchor）
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v2_4"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v2"}},
    "message": {"chat_id": "oc_v2", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"/exit\"}"}}}'
sleep 2

echo "--- /exit 后 anchor ---"
sqlite3 /tmp/feishu_v2_test.db "SELECT chat_id, current_edict_id FROM feishu_session_anchor"
sqlite3 /tmp/feishu_v2_test.db "SELECT id, json_extract(metadata_json, '\$.assistant_chat') as is_chat FROM edicts WHERE id = (SELECT current_edict_id FROM feishu_session_anchor WHERE chat_id='oc_v2')"

# 5. /clear
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v2_5"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v2"}},
    "message": {"chat_id": "oc_v2", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"/clear\"}"}}}'
sleep 2

echo "--- /clear 后 ---"
sqlite3 /tmp/feishu_v2_test.db "SELECT id, status FROM edicts WHERE source='channel' ORDER BY created_at"

pkill -f "uvicorn tianshu.app" 2>/dev/null || true
```

Expected：
- 第 1 步：anchor 表 oc_v2 → 某 edict_id；该 edict 的 is_chat=1
- 第 2 步：日志显示 /list 命令（卡片下发因 fake creds 失败但路由 OK）
- 第 3 步：业务敕令创建，anchor 切到新 edict
- 第 4 步：/exit 后 anchor 不空，仍指向第 1 步那个 chat 敕令
- 第 5 步：原 chat 敕令 status=completed，新建一个 chat 敕令 status=open

- [ ] **Step 11.2: 不 commit**（验证用，无代码改动）

如果发现行为不符合预期，回到对应 Step 修复并重新 commit。

---

## Step 12: 测试补齐（用户偏好"测试最后补"）

**目标：** 调整 v1.1 的测试以匹配 v2 行为；删除已废弃测试；新增 v2 行为测试。

**Files:**
- Modify: `tests/gateway/feishu/test_assistant_branch.py`
- Modify: `tests/gateway/feishu/test_edict_branch.py`
- Modify: `tests/gateway/feishu/test_card_builder.py`
- Modify: `tests/gateway/feishu/test_mode_router.py`
- Create: `tests/gateway/feishu/test_clear_command.py`（独立测试 /clear）

### Sub-tasks

- [ ] **Step 12.1: 修改 test_assistant_branch.py**

(a) 删除 IntentParser 相关 mock 测试：
```bash
grep -n "intent_parser\|test_natural_language" tests/gateway/feishu/test_assistant_branch.py
```
找到所有相关测试函数，整段删除。

(b) AssistantBranch fixture 移除 `intent_parser` 参数。

(c) 新增 `test_natural_language_calls_continue_or_create`：

```python
@pytest.mark.asyncio
async def test_natural_language_calls_continue_or_create(branch):
    """v2: 纯文本应调 continue_or_create 而非 silent reply。"""
    b, _, _, outbound, _ = branch
    b._edict_bridge.continue_or_create = AsyncMock(return_value="ed_chat_x")
    await b.handle(_msg("你是谁?"), _ctx())
    b._edict_bridge.continue_or_create.assert_awaited_once_with(
        chat_id="oc_x", sender_open_id="ou_a", text="你是谁?",
    )
```

(d) 新增 _cmd_clear 测试：

```python
@pytest.mark.asyncio
async def test_clear_archives_chat_edict_and_creates_new(branch):
    """v2: /clear 归档当前 chat 敕令 + ensure 新一个。"""
    b, storage, _, outbound, _ = branch
    e_chat = MagicMock()
    e_chat.id = "ed_chat_old"
    e_chat.metadata = {"assistant_chat": True}
    storage.get_edict.return_value = e_chat
    b._edict_bridge.ensure_chat_edict = AsyncMock(return_value="ed_chat_new")
    
    ctx = ModeContext(mode="assistant", chat_id="oc_x", sender_open_id="ou_a", edict_id="ed_chat_old")
    await b.handle(_msg("/clear"), ctx)
    
    storage.update_edict_status.assert_called_with("ed_chat_old", "completed")
    storage.delete_feishu_anchor.assert_called_with("oc_x")
    b._edict_bridge.ensure_chat_edict.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_rejected_in_business_edict(branch):
    """v2: /clear 在业务敕令上下文应拒绝。"""
    b, storage, _, outbound, _ = branch
    e_biz = MagicMock()
    e_biz.metadata = {}  # 不含 assistant_chat
    storage.get_edict.return_value = e_biz
    
    ctx = ModeContext(mode="edict", chat_id="oc_x", sender_open_id="ou_a", edict_id="ed_biz")
    await b.handle(_msg("/clear"), ctx)
    
    msg = outbound.send_text.await_args.args[1]
    assert "/exit" in msg or "业务敕令" in msg
    storage.update_edict_status.assert_not_called()
```

- [ ] **Step 12.2: 修改 test_edict_branch.py — _cmd_exit 行为变化**

定位 `test_exit_clears_anchor`，改为 `test_exit_switches_to_chat_edict`：

```python
@pytest.mark.asyncio
async def test_exit_switches_to_chat_edict(branch):
    """v2: /exit 切回 chat 敕令（而非清 anchor）。"""
    b, storage, _, outbound, _ = branch
    b._edict_bridge.ensure_chat_edict = AsyncMock(return_value="ed_chat_xyz")
    
    await b.handle(_msg("/exit"), _ctx())
    
    storage.delete_feishu_anchor.assert_called_with("oc_x")
    b._edict_bridge.ensure_chat_edict.assert_awaited_once()
    assert "ed_chat_x" in outbound.send_text.await_args.args[1]
```

- [ ] **Step 12.3: 修改 test_mode_router.py — 加 ensure_chat_edict + storage 依赖**

修改 fixtures（每个测试函数）加 `edict_bridge=MagicMock()` + `storage=MagicMock()`：

```python
@pytest.mark.asyncio
async def test_dispatch_calls_ensure_chat_edict_first():
    """v2: dispatch 前置 ensure_chat_edict。"""
    anchor = MagicMock(); anchor.get.return_value = None
    assistant = MagicMock(); assistant.handle = AsyncMock()
    edict = MagicMock(); edict.handle = AsyncMock()
    bridge = MagicMock(); bridge.ensure_chat_edict = AsyncMock(return_value="ed_chat_x")
    storage = MagicMock(); storage.get_edict.return_value = None
    
    router = ModeRouter(
        anchor=anchor, assistant_branch=assistant, edict_branch=edict,
        edict_bridge=bridge, storage=storage,
    )
    await router.dispatch(_make_msg(text="你好"))
    bridge.ensure_chat_edict.assert_awaited_once()
```

(b) 修改其它测试同步加 `edict_bridge` + `storage` 参数。

- [ ] **Step 12.4: 新增 test_storage 中 list_edicts filter 测试**

修改 `tests/test_storage.py`，加：

```python
def test_list_edicts_exclude_assistant_chat(storage):
    """v2: list_edicts(exclude_assistant_chat=True) 排除 chat 敕令。"""
    from tianshu.models.edict import Edict
    
    e1 = Edict(title="业务", goal="干活")
    e2 = Edict(title="聊天", goal="对话", metadata={"assistant_chat": True})
    storage.save_edict(e1)
    storage.save_edict(e2)
    
    all_edicts, all_total = storage.list_edicts()
    assert all_total == 2
    
    filtered, ftotal = storage.list_edicts(exclude_assistant_chat=True)
    assert ftotal == 1
    assert filtered[0].title == "业务"
```

- [ ] **Step 12.5: 跑全集测试**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -m pytest tests/gateway/feishu/ tests/test_storage.py tests/test_gateway.py -q 2>&1 | tail -5
ruff check src/tianshu/gateway/feishu/ tests/gateway/feishu/ 2>&1 | tail -3
```

Expected: 全部通过 + ruff 全绿。如有失败按错误信息修。

- [ ] **Step 12.6: 覆盖率**

```bash
.venv/bin/python -m pytest tests/gateway/feishu/ --cov=src/tianshu/gateway/feishu --cov-report=term-missing 2>&1 | tail -25
```

Expected: 整体 ≥ 80%。

- [ ] **Step 12.7: commit**

```bash
git add tests/
git commit -m "test(feishu): v2 极简模型测试更新（删 IntentParser、/clear、/exit 切回 chat 敕令、list filter）"
```

---

## Step 13: 文档更新

**Files:**
- Modify: `docs/ops/feishu-assistant-mode.md`

### Sub-tasks

- [ ] **Step 13.1: 整体改写文档反映 v2 极简模型**

替换文件全部内容：

```markdown
# 飞书助手模式（v2 极简模型）

## 概述

飞书助手与敕令模式**底层统一为同一种敕令**，仅以 `metadata.assistant_chat=true` 标记区分：

- **聊天敕令**（💼 助手）：用于持续对话，工具/技能/LLM 等行为完全等同于普通敕令
- **业务敕令**（📋 敕令 #xxx）：用户显式 `/new` 创建的任务

助手能否"做事"由绑定的 cabinet persona 决定（通政司配置）。选「兵部尚书」做助手 → 兵部尚书的 tools_allowed 全部可用（含 shell_exec / web_fetch 等）。选「户部尚书」 → 用其工具集。

## 模式切换

| 操作 | 当前 anchor | 新 anchor | UI |
|------|-----------|-----------|-----|
| 飞书首次接入 | （无）| 自动建 chat 敕令 | 💼 助手 |
| `/new <goal>` | chat 敕令 | 新建业务敕令 | 📋 敕令 |
| `/select <id>` | * | 指定业务敕令 | 📋 敕令 |
| `/exit` | 业务敕令 | 切回 chat 敕令 | 💼 助手 |
| `/clear` | chat 敕令 | 归档 + 新建 chat 敕令 | 💼 助手（新对话）|

## 命令清单

### 助手模式（chat 敕令上下文）
- 纯文本 = **continue_or_create** → executor 用 persona LLM 自然回应（含工具调用）
- `/new <目标>` 新建业务敕令
- `/list [filter]` 查业务敕令列表（自动隐藏 chat 敕令）
- `/select <id>` 切到业务敕令
- `/budget` 成本概览
- `/menu` 主菜单
- `/clear` 归档当前对话 + 新建 chat 敕令
- `/help` 帮助

### 敕令模式（业务敕令上下文）
- 纯文本 = 续接当前业务敕令（v1 现有行为）
- `/status` 查当前敕令状态
- `/cancel` 取消当前敕令
- `/exit` 切回 chat 敕令（v2: 不再清 anchor）
- `/new <目标>` 自动 /exit + /new
- `/list /budget /menu /help` 查询类（不动 anchor）

## 助手 Persona 配置

通政司页面 → 飞书助手分卡 → 选 cabinet persona 兼任助手 → 保存。

**v1.1 的「LLM 意图增强」开关 v2 已废弃** —— v2 不再有"自然语言 → 命令"的转换层；所有纯文本直接走 executor，由 persona LLM 决定回应方式（含工具调用）。

## 与 v1.1 的关键差异

| 场景 | v1.1 | v2 |
|------|------|------|
| 飞书首次发"你好" | silent reply | 自动建 chat 敕令 + LLM 回应 |
| "你是谁?" | silent reply | LLM 自然回应 |
| "显示我的列表" | IntentParser → /list | LLM 调 list_edicts 工具回应 |
| "每天爬这个网页" | silent reply | LLM 触发 cron + 长任务 plan |
| `/exit` | 删 anchor → 助手模式 | 切回 chat 敕令 |
| `/clear`（新）| - | 归档 + 新建 chat 敕令 |

## 紧急逃生

如 v2 有严重问题，临时回退到 v1 行为：

\`\`\`bash
TIANSHU_FEISHU_DISABLE_ASSISTANT_MODE=1
\`\`\`

## 故障排查

| 问题 | 排查 |
|------|------|
| 助手不响应纯文本 | 看 server 日志是否有 [feishu/edict] follow_up；检查 persona 的 LLM 配置可达 |
| `/list` 不显示我的聊天 | v2 设计如此 —— 聊天敕令默认隐藏；如需查看用 SQL 直接查 metadata.assistant_chat=true 的敕令 |
| `/clear` 报"业务敕令请用 /exit" | 当前 anchor 是业务敕令而非 chat 敕令；先 /exit 再 /clear |
| 自然语言不被识别为命令 | v2 设计：所有纯文本由 LLM 自主决定回应（不再做命令意图映射）|

## 实现原理

```
飞书消息 → ModeRouter.dispatch
  ├── ensure_chat_edict（保证 anchor 存在）
  ├── resolve_mode（基于 anchor 敕令的 metadata.assistant_chat）
  └── 命令路由 → AssistantBranch / EdictBranch
       └── 纯文本 → continue_or_create → executor.execute_edict
                                        → persona LLM + 工具
```
```

- [ ] **Step 13.2: commit**

```bash
git add docs/ops/feishu-assistant-mode.md
git commit -m "docs(feishu): v2 极简模型用户指南（聊天敕令统一）"
```

---

## 完成检查清单

- [ ] 13 个 Step 全部完成并提交
- [ ] `pytest tests/gateway/feishu/ --cov=src/tianshu/gateway/feishu` 覆盖率 ≥ 80%
- [ ] `ruff check src/tianshu/gateway/feishu/ tests/gateway/feishu/` 无 lint 错
- [ ] 端到端冒烟（Step 11）全部通过：
  - [ ] 首次接入自动建 chat 敕令 + anchor
  - [ ] /list 隐藏 chat 敕令
  - [ ] /new 进业务敕令模式
  - [ ] /exit 切回 chat 敕令（不是清 anchor）
  - [ ] /clear 归档 + 新建
- [ ] 现有 v1.1 测试通过（部分已修改）
- [ ] 文档：`docs/ops/feishu-assistant-mode.md` 改写为 v2 版本

---

## 风险与回退

| 风险 | 缓解 |
|------|-----|
| LLM 调用失败导致助手无响应 | persona LLM 配置检查；executor 已有 try/except |
| 删除 IntentParser 后某些 v1.1 测试失败 | Step 12 同步删除 test_intent_parser + 改 test_assistant_branch fixture |
| /list filter SQL 在某些 SQLite 版本不支持 json_extract | json_extract 是 SQLite 3.38+ 特性；tianshu 目前用版本检查 |
| 紧急逃生 | `TIANSHU_FEISHU_DISABLE_ASSISTANT_MODE=1` 退回 v1 行为 |
| ModeRouter dispatch 加 ensure_chat_edict 后增加每条消息开销 | ensure_chat_edict 仅在无 anchor 时建敕令，已有时直接返回；性能影响可忽略 |

---

## 与 spec / plan 文档的对应

| Spec § | Plan Step |
|--------|-----------|
| §3 模型 | Step 1-9 整体改造 |
| §4 决策 | Step 1 (删 IntentParser) + Step 5 (filter) + Step 9 (/clear) |
| §5 实施差异 | 13 Step 全部 |
| §6 行为差异 | Step 7 (ensure) + Step 9 (/exit /clear) |
| §8 测试 | Step 12 |
| §9 实施顺序 | 本 Plan 13 Step（包含 spec §9 的 10 步 + 端到端验证 + 文档）|
