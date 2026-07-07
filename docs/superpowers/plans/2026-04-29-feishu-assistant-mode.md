# 飞书机器人 v1.1 双模式架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 v1 飞书机器人基础上引入双模式架构 —— 助手模式（CLI 命令操作各功能）+ 敕令模式（纯文本续接），支持自然语言意图解析（LLM fallback）和绑定 cabinet persona 的人格化回信。

**Architecture:** Dispatcher → ModeRouter（基于 anchor 判定）→ AssistantBranch / EdictBranch 双分支。新增 5 个模块：ModeRouter / AssistantBranch / IntentParser / PersonaRenderer / CardBuilder。复用 v1 的 SessionAnchor / Storage / EventBus / FeishuOutbound / ApprovalCardHandler 基础设施。

**Tech Stack:** Python 3.13 / FastAPI / SQLite / lark-oapi 1.5.5 / pytest / pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-29-feishu-assistant-mode-design.md`

**用户偏好（来自 memory）：** 功能优先，测试最后补 —— Step 1-12 实现可手工验证的功能；Step 13 统一补齐测试到 80%+。

**用户特殊约束：** 整体开发完再提交（每 Task 仍本地 commit 便于 git show 审 diff，最终 push 由用户决定）。

**真实接口对照（spec 抽象 → tianshu 实际）：**
- `AgentPersona` 字段：`id / name / department / soul_path / role_path / llm_config_name / tools_allowed / ...`（**无** `emoji / title / system_prompt`）
- `app.state.persona_loader: PersonaLoader` 提供 `get(persona_id) -> AgentPersona | None`
- `storage.list_edicts(status=..., limit=..., offset=...) -> list[Edict]` 已存在
- `CostManager.get_budget(scope: str) -> BudgetStatus | None` 已存在
- v1 已有：`FeishuBot` `Dispatcher` `EdictBridge` `SessionAnchor` `FeishuOutbound` `ApprovalCardHandler`

---

## File Structure

```
src/tianshu/gateway/feishu/
├── __init__.py                 修改 +120 行 ：注入 persona_loader / renderer，重构 _on_message 走 ModeRouter
├── mode_router.py              新建 ~80 行  ：状态机判定（基于 anchor）+ 命令分发到对应分支
├── assistant_branch.py         新建 ~280 行 ：助手模式 9 个命令实现（/new /list /select /budget /menu /help /status /cancel /exit）
├── edict_branch.py             新建 ~150 行 ：敕令模式命令实现（/exit /new /list /budget /menu /status /cancel + 纯文本续接）
├── intent_parser.py            新建 ~120 行 ：LLM 意图解析（仅在助手模式纯文本未匹配命令时调用）
├── persona_renderer.py         新建 ~110 行 ：模板化渲染回信文案，department→emoji 映射表
├── card_builder.py             新建 ~180 行 ：/list /menu /budget 卡片构造
├── card_action_dispatcher.py   新建 ~80 行  ：通用按钮 value 协议分发器（取代 ApprovalCardHandler.handle_button_click 中只处理 approval 的逻辑）
├── settings.py                 修改 +20 行  ：新增 assistant_persona_id / intent_llm_enabled / disable_assistant_mode 字段
├── outbound.py                 不动
├── approval_card.py            修改 +5 行   ：handle_button_click 只处理 approval value，其它转给 CardActionDispatcher
└── connection.py               不动

src/tianshu/storage.py          修改 +10 行  ：新增 delete_feishu_anchor 方法
src/tianshu/config.py           修改 +3 行   ：新增 feishu_disable_assistant_mode env 字段（紧急逃生开关）
src/tianshu/gateway/tongzheng_api.py  修改 +30 行 ：channel config schema 加 assistant_persona_id / intent_llm_enabled，新增 GET /personas
src/tianshu/app.py              修改 +5 行   ：FeishuBot 注入 persona_loader

web/src/api/tongzheng.ts        修改 +15 行  ：扩展 FeishuChannelConfig 类型 + 新增 listPersonas
web/src/pages/TongzhengPage.tsx 修改 +80 行  ：助手分卡（persona 下拉 + LLM 增强 checkbox）

tests/gateway/feishu/
├── test_mode_router.py         新建 ~120 行
├── test_assistant_branch.py    新建 ~250 行
├── test_edict_branch.py        新建 ~120 行
├── test_intent_parser.py       新建 ~120 行
├── test_persona_renderer.py    新建 ~80 行
├── test_card_builder.py        新建 ~100 行
└── test_e2e_dual_mode.py       新建 ~150 行

docs/ops/feishu-assistant-mode.md   新建 ~100 行  用户使用指南
```

---

## Step 1: storage 新增 delete_feishu_anchor + channel_configs 字段语义

**目标：** 提供 `/exit` 所需的数据库操作；明确 channel_configs 兼容字段加载策略（无新表）。

**Files:**
- Modify: `src/tianshu/storage.py`

### Sub-tasks

- [ ] **Step 1.1: 在 Storage 类中追加 delete_feishu_anchor**

定位 `set_feishu_anchor` 方法（约 storage.py 行 2649 附近），紧邻追加：

```python
def delete_feishu_anchor(self, chat_id: str) -> None:
    """`/exit` 用：清除该 chat 的 anchor，回到助手模式。"""
    self._conn.execute(
        "DELETE FROM feishu_session_anchor WHERE chat_id = ?", (chat_id,),
    )
    self._conn.commit()
```

- [ ] **Step 1.2: 验证**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.storage import Storage
s = Storage('/tmp/anchor_test.db')
s.init_db()
s.set_feishu_anchor('oc_x', 'ed_1')
assert s.get_feishu_anchor('oc_x') == 'ed_1'
s.delete_feishu_anchor('oc_x')
assert s.get_feishu_anchor('oc_x') is None
print('OK')
"
rm -f /tmp/anchor_test.db
```
Expected: `OK`

- [ ] **Step 1.3: commit**

```bash
git add src/tianshu/storage.py
git commit -m "feat(feishu): storage 新增 delete_feishu_anchor 用于 /exit 退出敕令模式"
```

---

## Step 2: PersonaRenderer + 默认人格 fallback

**目标：** 用绑定 persona 的人格元素（name + department→emoji 映射）渲染回信文案。零 LLM 调用。

**Files:**
- Create: `src/tianshu/gateway/feishu/persona_renderer.py`

### Sub-tasks

- [ ] **Step 2.1: 创建 persona_renderer.py**

```python
# 文件：src/tianshu/gateway/feishu/persona_renderer.py
"""根据绑定 persona 渲染飞书回信文案。零 LLM 调用，纯模板。

AgentPersona 实际字段：id / name / department / soul_path / role_path /
                        llm_config_name / tools_allowed / ... （无 emoji / title 字段）
本模块用 department → emoji 映射表和 name 作为称呼。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tianshu.persona.loader import AgentPersona

logger = logging.getLogger(__name__)


# Department 到 emoji 的映射（与百官阁视觉一致）
_DEPT_EMOJI: dict[str, str] = {
    "tongzheng": "📜",   # 通政司：对外通信
    "binbu": "⚔️",       # 兵部：奉敕调用工具
    "hubu": "💰",        # 户部：成本与预算
    "libu": "🎓",        # 礼部：技能/培训
    "ducha": "⚖️",       # 都察院：审计
    "cabinet": "🏛️",     # 内阁：决策
    "neishi": "📖",      # 内侍/侍读
}

DEFAULT_EMOJI = "🏛️"
DEFAULT_NAME = "侍读"


@dataclass(frozen=True)
class RenderedPersona:
    """简化的 persona 视图，仅含飞书回信渲染需要的字段。"""
    name: str
    emoji: str
    department: str


class PersonaRenderer:
    """根据绑定 persona 渲染回信文案。"""

    def __init__(self, persona: "AgentPersona | None") -> None:
        self._persona = self._resolve(persona)

    @staticmethod
    def _resolve(persona: "AgentPersona | None") -> RenderedPersona:
        if persona is None:
            logger.warning(
                "[feishu/persona] no persona bound, falling back to default '%s'",
                DEFAULT_NAME,
            )
            return RenderedPersona(name=DEFAULT_NAME, emoji=DEFAULT_EMOJI, department="default")
        emoji = _DEPT_EMOJI.get(persona.department, DEFAULT_EMOJI)
        return RenderedPersona(
            name=persona.name or DEFAULT_NAME,
            emoji=emoji,
            department=persona.department or "default",
        )

    @property
    def name(self) -> str:
        return self._persona.name

    @property
    def emoji(self) -> str:
        return self._persona.emoji

    # --- 模式标记 ---

    @staticmethod
    def assistant_tag() -> str:
        return "💼 助手"

    @staticmethod
    def edict_tag(edict_id: str) -> str:
        return f"📋 敕令 #{edict_id[:8]}"

    # --- 回信文案 ---

    def welcome(self) -> str:
        return f"{self.emoji} {self.name} 在此候命。输入 /menu 打开菜单，或 /help 查看命令。"

    def help_assistant(self) -> str:
        return (
            f"{self.assistant_tag()} 可用命令：\n"
            "- `/new <目标>` 新建敕令\n"
            "- `/list [filter]` 查看敕令列表（filter: open/completed/all）\n"
            "- `/select <id>` 切换到指定敕令\n"
            "- `/budget` 成本概览\n"
            "- `/menu` 主菜单\n"
            "- `/help` 显示帮助"
        )

    def help_edict(self, edict_id: str) -> str:
        return (
            f"{self.edict_tag(edict_id)} 可用命令：\n"
            "- 纯文本 = 续接当前敕令\n"
            "- `/status` 查看状态\n"
            "- `/cancel` 取消敕令\n"
            "- `/exit` 退出回到助手模式\n"
            "- `/new <目标>` 自动退出 + 新建\n"
            "- `/list /budget /menu` 查询（不切换）"
        )

    def edict_created_reply(self, edict_id: str, title: str) -> str:
        return f"{self.assistant_tag()} → ✅ 新敕令 #{edict_id[:8]}「{title}」已颁，进入敕令模式"

    def edict_received_reply(self, edict_id: str) -> str:
        return f"{self.edict_tag(edict_id)} 已收到"

    def edict_selected_reply(self, edict_id: str, title: str) -> str:
        return f"{self.edict_tag(edict_id)} 已切换（标题：{title}）"

    def edict_exit_reply(self) -> str:
        return f"{self.assistant_tag()} 已退出敕令模式"

    def edict_cancel_reply(self, edict_id: str) -> str:
        return f"{self.edict_tag(edict_id)} 已取消"

    def unknown_command_reply(self, mode_tag: str, cmd: str) -> str:
        return f"{mode_tag} 未识此令「{cmd}」，输入 /help 查看可用命令"

    def assistant_silent_reply(self) -> str:
        return f"{self.assistant_tag()} {self.name} 待命中。请用 /help 查看命令，或 /menu 打开菜单。"

    def llm_intent_hint(self, intent: str) -> str:
        return f"💡 我理解你想：{intent}"


__all__ = ["PersonaRenderer", "RenderedPersona", "DEFAULT_NAME", "DEFAULT_EMOJI"]
```

- [ ] **Step 2.2: 验证 import + 渲染**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.persona_renderer import PersonaRenderer
r = PersonaRenderer(None)
print(r.welcome())
print(r.help_assistant())
print(r.assistant_tag())
print(r.edict_tag('ed_abcdef12345'))
"
ruff check src/tianshu/gateway/feishu/persona_renderer.py 2>&1 | tail -3
```
Expected：四行渲染输出 + ruff 全绿。

- [ ] **Step 2.3: commit**

```bash
git add src/tianshu/gateway/feishu/persona_renderer.py
git commit -m "feat(feishu): PersonaRenderer 模板化渲染 + department emoji 映射"
```

---

## Step 3: ModeRouter 状态机

**目标：** 基于 anchor 状态判定模式，分发到 AssistantBranch / EdictBranch。

**Files:**
- Create: `src/tianshu/gateway/feishu/mode_router.py`

### Sub-tasks

- [ ] **Step 3.1: 创建 mode_router.py**

```python
# 文件：src/tianshu/gateway/feishu/mode_router.py
"""ModeRouter：基于 SessionAnchor 状态判定模式，分发命令到对应分支。

状态机：
- anchor 不存在 / current_edict_id is None → 助手模式 → AssistantBranch
- anchor.current_edict_id 非空 → 敕令模式 → EdictBranch
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tianshu.gateway.feishu.assistant_branch import AssistantBranch
    from tianshu.gateway.feishu.dispatcher import FeishuMessage
    from tianshu.gateway.feishu.edict_branch import EdictBranch
    from tianshu.gateway.feishu.session_anchor import SessionAnchor

logger = logging.getLogger(__name__)

Mode = Literal["assistant", "edict"]


@dataclass(frozen=True)
class ModeContext:
    """每条消息处理时的模式上下文。"""
    mode: Mode
    chat_id: str
    sender_open_id: str
    edict_id: str | None  # 敕令模式时为绑定的 edict_id


class ModeRouter:
    """读 anchor 决定走哪个分支。"""

    def __init__(
        self,
        *,
        anchor: "SessionAnchor",
        assistant_branch: "AssistantBranch",
        edict_branch: "EdictBranch",
    ) -> None:
        self._anchor = anchor
        self._assistant = assistant_branch
        self._edict = edict_branch

    def resolve_mode(self, chat_id: str) -> ModeContext:
        """根据当前 anchor 状态构造 ModeContext。"""
        edict_id = self._anchor.get(chat_id)
        if edict_id:
            return ModeContext(
                mode="edict", chat_id=chat_id,
                sender_open_id="", edict_id=edict_id,
            )
        return ModeContext(
            mode="assistant", chat_id=chat_id,
            sender_open_id="", edict_id=None,
        )

    async def dispatch(self, msg: "FeishuMessage") -> None:
        """主入口：消息进来 → 判断模式 → 转给对应分支。"""
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


__all__ = ["ModeRouter", "ModeContext", "Mode"]
```

- [ ] **Step 3.2: 验证 import**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.mode_router import ModeRouter, ModeContext
print('imports OK')
"
ruff check src/tianshu/gateway/feishu/mode_router.py 2>&1 | tail -3
```

- [ ] **Step 3.3: commit**

```bash
git add src/tianshu/gateway/feishu/mode_router.py
git commit -m "feat(feishu): ModeRouter 状态机（基于 anchor 分发到 assistant/edict 分支）"
```

---

## Step 4: AssistantBranch 助手模式命令路由

**目标：** 实现助手模式下 9 个命令的路由 + 业务逻辑。`/list /budget /menu` 暂占位（依赖 Step 6/7 的 CardBuilder）。

**Files:**
- Create: `src/tianshu/gateway/feishu/assistant_branch.py`

### Sub-tasks

- [ ] **Step 4.1: 创建 assistant_branch.py**

```python
# 文件：src/tianshu/gateway/feishu/assistant_branch.py
"""助手模式（anchor=NULL）命令路由。

支持命令：/new /list /select /budget /menu /help /status /cancel
不支持的纯文本：先 IntentParser 解析（若启用），失败则回 silent_reply。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from tianshu.models.common import EdictStatus

if TYPE_CHECKING:
    from tianshu.executor.executor import Executor
    from tianshu.gateway.feishu.card_builder import CardBuilder
    from tianshu.gateway.feishu.dispatcher import FeishuMessage
    from tianshu.gateway.feishu.edict_bridge import EdictBridge
    from tianshu.gateway.feishu.intent_parser import IntentParser
    from tianshu.gateway.feishu.mode_router import ModeContext
    from tianshu.gateway.feishu.outbound import FeishuOutbound
    from tianshu.gateway.feishu.persona_renderer import PersonaRenderer
    from tianshu.gateway.feishu.session_anchor import SessionAnchor
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class AssistantBranch:
    """助手模式命令分发器。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        anchor: "SessionAnchor",
        edict_bridge: "EdictBridge",
        outbound: "FeishuOutbound",
        renderer: "PersonaRenderer",
        card_builder: "CardBuilder",
        intent_parser: "IntentParser | None",
    ) -> None:
        self._storage = storage
        self._anchor = anchor
        self._edict_bridge = edict_bridge
        self._outbound = outbound
        self._renderer = renderer
        self._card_builder = card_builder
        self._intent_parser = intent_parser  # None = LLM fallback 禁用

    def set_renderer(self, renderer: "PersonaRenderer") -> None:
        """支持 reload 时切换 persona。"""
        self._renderer = renderer

    async def handle(self, msg: "FeishuMessage", ctx: "ModeContext") -> None:
        """主入口：解析命令 → 调对应实现。"""
        text = msg.text.strip()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""

        if cmd == "/new":
            goal = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_new(msg, ctx, goal)
        elif cmd == "/list":
            filter_arg = parts[1].strip().lower() if len(parts) > 1 else "open"
            await self._cmd_list(msg, ctx, filter_arg)
        elif cmd == "/select":
            target = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_select(msg, ctx, target)
        elif cmd == "/budget":
            await self._cmd_budget(msg, ctx)
        elif cmd == "/menu":
            await self._cmd_menu(msg, ctx)
        elif cmd == "/help":
            await self._reply(msg.chat_id, self._renderer.help_assistant())
        elif cmd == "/status":
            target = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_status(msg, ctx, target)
        elif cmd == "/cancel":
            target = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_cancel(msg, ctx, target)
        elif cmd.startswith("/"):
            await self._reply(
                msg.chat_id,
                self._renderer.unknown_command_reply(self._renderer.assistant_tag(), cmd),
            )
        else:
            await self._handle_natural_language(msg, ctx, text)

    # --- 命令实现 ---

    async def _cmd_new(self, msg, ctx, goal: str) -> None:
        if not goal:
            await self._reply(msg.chat_id, "用法：/new <目标描述>")
            return
        edict_id = await self._edict_bridge.create_new(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
        )
        title = goal[:20] + ("…" if len(goal) > 20 else "")
        await self._reply(msg.chat_id, self._renderer.edict_created_reply(edict_id, title))

    async def _cmd_list(self, msg, ctx, filter_arg: str) -> None:
        status_filter = self._parse_filter(filter_arg)
        edicts = self._storage.list_edicts(
            status=status_filter, limit=10, offset=0,
        )
        if not edicts:
            await self._reply(msg.chat_id, f"{self._renderer.assistant_tag()} 暂无敕令。输入 /new <目标> 颁布第一道")
            return
        card = self._card_builder.build_list_card(
            edicts=edicts, current_anchor=ctx.edict_id,
        )
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_select(self, msg, ctx, target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "用法：/select <敕令 ID 前缀（≥6 字符）>")
            return
        if len(target) < 6:
            await self._reply(msg.chat_id, "ID 前缀至少 6 字符以避免歧义")
            return
        matches = [e for e in self._storage.list_edicts(limit=200, offset=0) if e.id.startswith(target)]
        if not matches:
            await self._reply(msg.chat_id, f"未找到敕令 #{target}，输入 /list 查看")
            return
        if len(matches) > 1:
            ids_preview = ", ".join(f"#{e.id[:12]}" for e in matches[:5])
            await self._reply(msg.chat_id, f"短 ID '{target}' 有多个匹配：{ids_preview}，请用更长前缀")
            return
        edict = matches[0]
        self._anchor.set(msg.chat_id, edict.id)
        await self._reply(msg.chat_id, self._renderer.edict_selected_reply(edict.id, edict.title or "(无标题)"))

    async def _cmd_budget(self, msg, ctx) -> None:
        card = await self._card_builder.build_budget_card()
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_menu(self, msg, ctx) -> None:
        card = self._card_builder.build_menu_card()
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_status(self, msg, ctx, target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "助手模式下 /status 需要指定敕令 ID。用法：/status <id>")
            return
        edict = self._find_by_prefix(target)
        if not edict:
            await self._reply(msg.chat_id, f"未找到敕令 #{target}")
            return
        await self._reply(
            msg.chat_id,
            f"📋 #{edict.id[:8]} 标题：{edict.title or '(无)'}\n状态：{edict.status}",
        )

    async def _cmd_cancel(self, msg, ctx, target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "助手模式下 /cancel 需要指定敕令 ID。用法：/cancel <id>")
            return
        edict = self._find_by_prefix(target)
        if not edict:
            await self._reply(msg.chat_id, f"未找到敕令 #{target}")
            return
        if edict.status in (EdictStatus.COMPLETED, EdictStatus.CANCELLED):
            await self._reply(msg.chat_id, f"敕令 #{edict.id[:8]} 已 {edict.status}，无需取消")
            return
        self._storage.update_edict_status(edict.id, EdictStatus.CANCELLED.value)
        await self._reply(msg.chat_id, self._renderer.edict_cancel_reply(edict.id))

    # --- 纯文本（自然语言）---

    async def _handle_natural_language(self, msg, ctx, text: str) -> None:
        if self._intent_parser is None:
            await self._reply(msg.chat_id, self._renderer.assistant_silent_reply())
            return
        intent_result = await self._intent_parser.parse(text)
        intent = intent_result.get("intent", "unknown")
        args = intent_result.get("args", {}) or {}
        if intent == "unknown":
            await self._reply(msg.chat_id, self._renderer.assistant_silent_reply())
            return
        # 把意图映射回命令调用
        synthesized = self._synthesize_command(intent, args)
        if synthesized is None:
            await self._reply(msg.chat_id, self._renderer.assistant_silent_reply())
            return
        # 提示用户我们理解的意图，然后执行
        await self._reply(msg.chat_id, self._renderer.llm_intent_hint(synthesized))
        # 重写 msg.text 后递归调 handle
        from dataclasses import replace
        new_msg = replace(msg, text=synthesized)
        await self.handle(new_msg, ctx)

    @staticmethod
    def _synthesize_command(intent: str, args: dict) -> str | None:
        if intent == "list":
            f = args.get("filter") or "open"
            return f"/list {f}"
        if intent == "new":
            goal = args.get("goal") or ""
            if not goal:
                return None
            return f"/new {goal}"
        if intent == "status":
            tid = args.get("target") or args.get("edict_id") or ""
            return f"/status {tid}".strip()
        if intent == "cancel":
            tid = args.get("target") or args.get("edict_id") or ""
            return f"/cancel {tid}".strip()
        if intent == "budget":
            return "/budget"
        if intent == "help":
            return "/help"
        return None

    # --- 工具方法 ---

    @staticmethod
    def _parse_filter(s: str) -> EdictStatus | None:
        s = s.lower()
        if s in ("open", "active", ""):
            return EdictStatus.OPEN
        if s == "completed":
            return EdictStatus.COMPLETED
        if s == "cancelled":
            return EdictStatus.CANCELLED
        if s == "all":
            return None
        return EdictStatus.OPEN

    def _find_by_prefix(self, prefix: str):
        if len(prefix) < 6:
            return None
        for e in self._storage.list_edicts(limit=200, offset=0):
            if e.id.startswith(prefix):
                return e
        return None

    async def _reply(self, chat_id: str, text: str) -> None:
        await self._outbound.send_text(chat_id, text)


__all__ = ["AssistantBranch"]
```

- [ ] **Step 4.2: 验证（仅 import + 类型）**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.assistant_branch import AssistantBranch
print('AssistantBranch imports OK')
"
ruff check src/tianshu/gateway/feishu/assistant_branch.py 2>&1 | tail -3
```

- [ ] **Step 4.3: commit**

```bash
git add src/tianshu/gateway/feishu/assistant_branch.py
git commit -m "feat(feishu): AssistantBranch — 助手模式 9 命令路由（卡片命令暂占位）"
```

---

## Step 5: EdictBranch 敕令模式命令路由

**目标：** 实现敕令模式下命令路由。`/exit /new` 触发 anchor 切换；`/list /budget /menu` 复用 AssistantBranch 但不动 anchor；纯文本走 v1 续接。

**Files:**
- Create: `src/tianshu/gateway/feishu/edict_branch.py`

### Sub-tasks

- [ ] **Step 5.1: 创建 edict_branch.py**

```python
# 文件：src/tianshu/gateway/feishu/edict_branch.py
"""敕令模式（anchor=eid）命令路由。

支持命令：
- /exit                   退出敕令模式 → 助手模式
- /new <goal>             自动 /exit + /new
- /status [id]            查状态（默认当前 anchor）
- /cancel [id]            取消（默认当前 anchor）
- /list /budget /menu     查询类，不动 anchor
- /help                   敕令模式帮助
- 纯文本                   续接当前敕令（v1 现有行为）
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.edict_bridge import EdictBusyError
from tianshu.models.common import EdictStatus

if TYPE_CHECKING:
    from tianshu.gateway.feishu.assistant_branch import AssistantBranch
    from tianshu.gateway.feishu.dispatcher import FeishuMessage
    from tianshu.gateway.feishu.edict_bridge import EdictBridge
    from tianshu.gateway.feishu.mode_router import ModeContext
    from tianshu.gateway.feishu.outbound import FeishuOutbound
    from tianshu.gateway.feishu.persona_renderer import PersonaRenderer
    from tianshu.gateway.feishu.session_anchor import SessionAnchor
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class EdictBranch:
    """敕令模式命令分发器。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        anchor: "SessionAnchor",
        edict_bridge: "EdictBridge",
        outbound: "FeishuOutbound",
        renderer: "PersonaRenderer",
        assistant_branch: "AssistantBranch",
    ) -> None:
        self._storage = storage
        self._anchor = anchor
        self._edict_bridge = edict_bridge
        self._outbound = outbound
        self._renderer = renderer
        self._assistant = assistant_branch  # 用于查询类命令复用

    def set_renderer(self, renderer: "PersonaRenderer") -> None:
        self._renderer = renderer

    async def handle(self, msg: "FeishuMessage", ctx: "ModeContext") -> None:
        text = msg.text.strip()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        edict_id = ctx.edict_id or ""

        if cmd == "/exit":
            await self._cmd_exit(msg, ctx)
        elif cmd == "/new":
            goal = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_new_with_exit(msg, ctx, goal)
        elif cmd == "/status":
            target = parts[1].strip() if len(parts) > 1 else edict_id
            await self._cmd_status(msg, target)
        elif cmd == "/cancel":
            target = parts[1].strip() if len(parts) > 1 else edict_id
            await self._cmd_cancel(msg, target)
        elif cmd in ("/list", "/budget", "/menu"):
            # 委托给 AssistantBranch 的对应实现，但不动 anchor
            await self._assistant.handle(msg, ctx)
        elif cmd == "/help":
            await self._reply(msg.chat_id, self._renderer.help_edict(edict_id))
        elif cmd.startswith("/"):
            await self._reply(
                msg.chat_id,
                self._renderer.unknown_command_reply(
                    self._renderer.edict_tag(edict_id), cmd,
                ),
            )
        else:
            await self._continue_edict(msg, ctx, text)

    # --- 命令实现 ---

    async def _cmd_exit(self, msg, ctx) -> None:
        self._storage.delete_feishu_anchor(msg.chat_id)
        await self._reply(msg.chat_id, self._renderer.edict_exit_reply())

    async def _cmd_new_with_exit(self, msg, ctx, goal: str) -> None:
        if not goal:
            await self._reply(msg.chat_id, "用法：/new <目标描述>")
            return
        # 先退出当前敕令模式
        self._storage.delete_feishu_anchor(msg.chat_id)
        # 再新建
        edict_id = await self._edict_bridge.create_new(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
        )
        title = goal[:20] + ("…" if len(goal) > 20 else "")
        await self._reply(
            msg.chat_id,
            f"{self._renderer.edict_tag(ctx.edict_id or '')} → {self._renderer.edict_created_reply(edict_id, title)}",
        )

    async def _cmd_status(self, msg, target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "用法：/status [id]")
            return
        edict = self._storage.get_edict(target)
        if not edict:
            await self._reply(msg.chat_id, f"敕令 #{target[:8]} 不存在")
            return
        await self._reply(
            msg.chat_id,
            f"{self._renderer.edict_tag(edict.id)} 标题：{edict.title or '(无)'}\n状态：{edict.status}",
        )

    async def _cmd_cancel(self, msg, target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "用法：/cancel [id]")
            return
        edict = self._storage.get_edict(target)
        if not edict:
            await self._reply(msg.chat_id, f"敕令 #{target[:8]} 不存在")
            return
        if edict.status in (EdictStatus.COMPLETED, EdictStatus.CANCELLED):
            await self._reply(msg.chat_id, f"敕令 #{edict.id[:8]} 已 {edict.status}，无需取消")
            return
        self._storage.update_edict_status(edict.id, EdictStatus.CANCELLED.value)
        # 如果取消的是当前 anchor 敕令，清 anchor
        if edict.id == self._anchor.get(msg.chat_id):
            self._storage.delete_feishu_anchor(msg.chat_id)
            await self._reply(
                msg.chat_id,
                f"{self._renderer.edict_cancel_reply(edict.id)}（已自动退出敕令模式）",
            )
        else:
            await self._reply(msg.chat_id, self._renderer.edict_cancel_reply(edict.id))

    async def _continue_edict(self, msg, ctx, text: str) -> None:
        """v1 续接行为：依赖 EdictBridge.continue_or_create。"""
        try:
            edict_id = await self._edict_bridge.continue_or_create(
                chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, text=text,
            )
        except EdictBusyError as exc:
            await self._reply(msg.chat_id, str(exc))
            return
        await self._reply(msg.chat_id, self._renderer.edict_received_reply(edict_id))

    async def _reply(self, chat_id: str, text: str) -> None:
        await self._outbound.send_text(chat_id, text)


__all__ = ["EdictBranch"]
```

- [ ] **Step 5.2: 验证**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.edict_branch import EdictBranch
print('EdictBranch imports OK')
"
ruff check src/tianshu/gateway/feishu/edict_branch.py 2>&1 | tail -3
```

- [ ] **Step 5.3: commit**

```bash
git add src/tianshu/gateway/feishu/edict_branch.py
git commit -m "feat(feishu): EdictBranch — 敕令模式命令路由（含 /exit /new 切换 + 查询类委托）"
```

---

## Step 6: CardBuilder（/list + /menu）

**目标：** 构造 `/list` 简化卡片 + `/menu` 主菜单卡片。`/budget` 卡片在 Step 7 加。

**Files:**
- Create: `src/tianshu/gateway/feishu/card_builder.py`

### Sub-tasks

- [ ] **Step 6.1: 创建 card_builder.py（含 /budget 占位）**

```python
# 文件：src/tianshu/gateway/feishu/card_builder.py
"""卡片构造：/list /menu /budget。

按钮 value 协议（统一）：
{
  "command": "select" | "list" | "budget" | "help" | "new" | "cancel",
  "edict_id"?: str,
  "goal"?: str,
  "filter"?: str,
}
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.cost.manager import CostManager
    from tianshu.models.edict import Edict
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class CardBuilder:
    """构造各类卡片 payload（dict）。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        cost_manager: "CostManager | None" = None,
    ) -> None:
        self._storage = storage
        self._cost_manager = cost_manager

    # --- /list 卡片 ---

    def build_list_card(
        self,
        edicts: list["Edict"],
        current_anchor: str | None = None,
    ) -> dict:
        """每条敕令一行 markdown + 一个主按钮"切换到此敕令"。"""
        elements: list[dict] = []
        for i, e in enumerate(edicts):
            star = "★ " if e.id == current_anchor else ""
            title_short = (e.title or "(无标题)")[:30]
            elements.append({
                "tag": "markdown",
                "content": f"{star}**#{e.id[:8]}** · {e.status} · {title_short}",
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "切换到此敕令"},
                        "type": "primary" if e.id == current_anchor else "default",
                        "value": {"command": "select", "edict_id": e.id},
                    }
                ],
            })
            if i < len(edicts) - 1:
                elements.append({"tag": "hr"})

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {
                    "tag": "plain_text",
                    "content": f"📋 最近敕令（{len(edicts)} 条）",
                },
            },
            "elements": elements,
        }

    # --- /menu 卡片 ---

    def build_menu_card(self) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"tag": "plain_text", "content": "🏛️ 主菜单"},
            },
            "elements": [
                {"tag": "markdown", "content": "_请选择操作 ↓_"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "📋 查看列表"},
                            "value": {"command": "list", "filter": "open"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "💰 成本概览"},
                            "value": {"command": "budget"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❓ 帮助"},
                            "value": {"command": "help"},
                        },
                    ],
                },
            ],
        }

    # --- /budget 卡片（Step 7 实现）---

    async def build_budget_card(self) -> dict:
        """成本概览卡片（Step 7 完整实现）。"""
        if self._cost_manager is None:
            return self._budget_unavailable_card()
        try:
            return await self._build_budget_card_real()
        except Exception:
            logger.exception("[feishu/card] budget card build failed")
            return self._budget_unavailable_card()

    async def _build_budget_card_real(self) -> dict:
        # Step 7 填实现，先占位返回简化卡片
        budget = self._cost_manager.get_budget("global") if self._cost_manager else None
        if budget is None:
            return self._budget_unavailable_card()
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "💰 预算概览"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**当前预算**：¥{budget.budget_cny:.2f}\n"
                        f"**已花费**：¥{budget.spent_cny:.2f}\n"
                        f"**剩余**：¥{budget.budget_cny - budget.spent_cny:.2f}"
                    ),
                }
            ],
        }

    @staticmethod
    def _budget_unavailable_card() -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "grey",
                "title": {"tag": "plain_text", "content": "💰 成本概览"},
            },
            "elements": [
                {"tag": "markdown", "content": "_暂时无法获取成本数据，请稍后重试或在 web 端查看。_"},
            ],
        }


__all__ = ["CardBuilder"]
```

- [ ] **Step 6.2: 验证（list/menu 卡片 schema）**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.card_builder import CardBuilder
from unittest.mock import MagicMock

cb = CardBuilder(storage=MagicMock(), cost_manager=None)
menu = cb.build_menu_card()
assert menu['header']['template'] == 'purple'
assert len(menu['elements'][1]['actions']) == 3
print('menu card OK')

# 假 edicts
class _E:
    def __init__(self, id, title, status):
        self.id, self.title, self.status = id, title, status
edicts = [_E('ed_aaaa1111', '写代码', 'open'), _E('ed_bbbb2222', '总结', 'open')]
lst = cb.build_list_card(edicts, current_anchor='ed_aaaa1111')
assert '★' in lst['elements'][0]['content']
assert lst['elements'][1]['actions'][0]['type'] == 'primary'
print('list card OK')
"
ruff check src/tianshu/gateway/feishu/card_builder.py 2>&1 | tail -3
```

- [ ] **Step 6.3: commit**

```bash
git add src/tianshu/gateway/feishu/card_builder.py
git commit -m "feat(feishu): CardBuilder /list /menu 卡片 + /budget 占位"
```

---

## Step 7: /budget 真实数据接入

**目标：** 让 CardBuilder.build_budget_card 调真实 cost API，列出近 7 天 + Top 5 高消费敕令。

**Files:**
- Modify: `src/tianshu/gateway/feishu/card_builder.py`

### Sub-tasks

- [ ] **Step 7.1: 查现有 cost API**

```bash
grep -nE "def get_cost_summary|def list_recent_costs|def get_records" /Users/chenjiamin/tiangong/tianshu/src/tianshu/cost/manager.py /Users/chenjiamin/tiangong/tianshu/src/tianshu/cost/tracker.py 2>/dev/null | head -10
grep -nE "def get_cost_records|cost_ledger|def get_cost_summary" /Users/chenjiamin/tiangong/tianshu/src/tianshu/storage.py | head -10
```

记录可用方法。本 Step 假设至少存在以下两类查询能力（如不存在则按 Step 7.4 fallback 简化）：
- `cost_manager.get_budget(scope: str) -> BudgetStatus | None`（已知存在）
- 通过 storage 查 `cost_ledger` 表近 7 天的累计 + 按 edict_id 分组

- [ ] **Step 7.2: 增强 build_budget_card_real**

替换 `_build_budget_card_real` 实现：

```python
async def _build_budget_card_real(self) -> dict:
    """近 7 天总消费 + 当前预算 + Top 5 高消费敕令。"""
    if self._cost_manager is None:
        return self._budget_unavailable_card()
    budget = self._cost_manager.get_budget("global")

    # 近 7 天总消费 + Top 5 敕令（直接查 cost_ledger 表）
    recent_total = 0.0
    top_edicts: list[tuple[str, str, float]] = []
    try:
        rows = self._storage._conn.execute(
            "SELECT edict_id, SUM(cost_cny) as total FROM cost_ledger "
            "WHERE created_at >= datetime('now', '-7 days') AND edict_id IS NOT NULL "
            "GROUP BY edict_id ORDER BY total DESC LIMIT 5"
        ).fetchall()
        for row in rows:
            edict_id, total = row[0], float(row[1] or 0.0)
            edict = self._storage.get_edict(edict_id)
            title = (edict.title if edict else "(已删)")[:20]
            top_edicts.append((edict_id, title, total))
            recent_total += total
        # 补充：未进 top5 但仍在统计期内的累计
        full_total_row = self._storage._conn.execute(
            "SELECT SUM(cost_cny) FROM cost_ledger "
            "WHERE created_at >= datetime('now', '-7 days')"
        ).fetchone()
        if full_total_row and full_total_row[0]:
            recent_total = float(full_total_row[0])
    except Exception:
        logger.exception("[feishu/card] cost ledger query failed")

    lines = [f"**近 7 天消费**：¥{recent_total:.2f}"]
    if budget is not None:
        lines.append(f"**当前预算**：¥{budget.budget_cny:.2f}")
        lines.append(f"**剩余**：¥{(budget.budget_cny - budget.spent_cny):.2f}")

    elements: list[dict] = [
        {"tag": "markdown", "content": "\n".join(lines)},
    ]
    if top_edicts:
        elements.append({"tag": "hr"})
        top_lines = ["**Top 5 敕令成本（近 7 天）**："]
        for eid, title, cost in top_edicts:
            top_lines.append(f"- #{eid[:8]} ¥{cost:.2f}（{title}）")
        elements.append({"tag": "markdown", "content": "\n".join(top_lines)})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "💰 成本概览（近 7 天）"},
        },
        "elements": elements,
    }
```

- [ ] **Step 7.3: 验证**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
import asyncio
from tianshu.gateway.feishu.card_builder import CardBuilder
from unittest.mock import MagicMock, AsyncMock

storage = MagicMock()
storage._conn.execute.return_value.fetchall.return_value = []
storage._conn.execute.return_value.fetchone.return_value = (0.0,)
cost = MagicMock()
cost.get_budget.return_value = None
cb = CardBuilder(storage=storage, cost_manager=cost)
card = asyncio.run(cb.build_budget_card())
assert card['header']['title']['content'].startswith('💰')
print('budget card no-data OK')
"
ruff check src/tianshu/gateway/feishu/card_builder.py 2>&1 | tail -3
```

- [ ] **Step 7.4: commit**

```bash
git add src/tianshu/gateway/feishu/card_builder.py
git commit -m "feat(feishu): /budget 卡片接入 cost_ledger 查近 7 天 + Top 5 敕令"
```

---

## Step 8: IntentParser LLM 意图解析

**目标：** 助手模式下纯文本未匹配命令时，调用绑定 persona 的 LLM 解析意图。

**Files:**
- Create: `src/tianshu/gateway/feishu/intent_parser.py`

### Sub-tasks

- [ ] **Step 8.1: 查 LLM 调用接口**

```bash
grep -nE "class.*Provider|def complete|def chat|async def generate" /Users/chenjiamin/tiangong/tianshu/src/tianshu/providers/manager.py 2>/dev/null | head -10
grep -nE "class.*Provider|class LLMConfig|def complete" /Users/chenjiamin/tiangong/tianshu/src/tianshu/providers/*.py 2>/dev/null | head -10
```

记录 ProviderManager 的实际调用入口。本 Step 假设至少有 `provider_manager.acquire(llm_config_name)` 能拿到 client，client 有 `complete(messages, max_tokens, temperature)` 方法。如不一致按实际改下方代码。

- [ ] **Step 8.2: 创建 intent_parser.py**

```python
# 文件：src/tianshu/gateway/feishu/intent_parser.py
"""LLM 意图解析。仅在助手模式 + 命令未命中时调用。"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.persona.loader import PersonaLoader
    from tianshu.providers.manager import ProviderManager

logger = logging.getLogger(__name__)

INTENTS = ("new", "list", "status", "cancel", "budget", "help", "unknown")

_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


class IntentParser:
    """轻量 LLM 意图分类器。返回 {"intent": ..., "args": {...}}。"""

    def __init__(
        self,
        *,
        persona_loader: "PersonaLoader",
        provider_manager: "ProviderManager",
        persona_id: str,
    ) -> None:
        self._loader = persona_loader
        self._providers = provider_manager
        self._persona_id = persona_id

    def set_persona(self, persona_id: str) -> None:
        self._persona_id = persona_id

    async def parse(self, text: str) -> dict:
        persona = self._loader.get(self._persona_id)
        if persona is None:
            logger.warning("[feishu/intent] persona %s not found", self._persona_id)
            return {"intent": "unknown", "args": {}}
        if not persona.llm_config_name:
            logger.warning(
                "[feishu/intent] persona %s has no llm_config_name", persona.name,
            )
            return {"intent": "unknown", "args": {}}

        try:
            client = self._providers.acquire(persona.llm_config_name)
        except Exception:
            logger.exception("[feishu/intent] acquire provider failed")
            return {"intent": "unknown", "args": {}}

        prompt = self._build_prompt(persona)
        try:
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ]
            resp_text = await client.complete(
                messages=messages, max_tokens=200, temperature=0.0,
            )
        except Exception:
            logger.exception("[feishu/intent] LLM call failed")
            return {"intent": "unknown", "args": {}}

        return self._parse_json(resp_text)

    @staticmethod
    def _build_prompt(persona) -> str:
        return (
            f"你是用户的飞书助手「{persona.name}」。判断用户消息想做什么。\n"
            f"只输出一个 JSON：{{\"intent\": \"<one of {list(INTENTS)}>\", \"args\": {{...}}}}\n\n"
            "intent 含义：\n"
            "- new: 用户想新建一个敕令（args.goal 为目标描述）\n"
            "- list: 用户想看敕令列表（args.filter ∈ open/completed/all）\n"
            "- status: 用户想看某敕令状态（args.target 为 id 前缀或 'latest'）\n"
            "- cancel: 用户想取消某敕令（args.target 为 id 前缀或 'latest'）\n"
            "- budget: 用户想看成本/预算\n"
            "- help: 用户想看帮助\n"
            "- unknown: 不属于以上任何一种\n\n"
            "示例：\n"
            "\"显示我的列表\" → {\"intent\": \"list\", \"args\": {}}\n"
            "\"取消最近那个\" → {\"intent\": \"cancel\", \"args\": {\"target\": \"latest\"}}\n"
            "\"新建一个敕令做摘要\" → {\"intent\": \"new\", \"args\": {\"goal\": \"做摘要\"}}\n"
            "\"在干啥\" → {\"intent\": \"unknown\", \"args\": {}}\n"
            "\"花了多少钱\" → {\"intent\": \"budget\", \"args\": {}}"
        )

    @staticmethod
    def _parse_json(resp_text: str) -> dict:
        if not resp_text:
            return {"intent": "unknown", "args": {}}
        # 容错截取第一个 {...} 块
        match = _JSON_BLOCK_RE.search(resp_text)
        if not match:
            return {"intent": "unknown", "args": {}}
        try:
            obj = json.loads(match.group(0))
        except Exception:
            logger.warning("[feishu/intent] invalid JSON: %.200s", resp_text)
            return {"intent": "unknown", "args": {}}
        intent = obj.get("intent", "unknown")
        if intent not in INTENTS:
            logger.warning("[feishu/intent] invalid intent: %s", intent)
            return {"intent": "unknown", "args": {}}
        args = obj.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        return {"intent": intent, "args": args}


__all__ = ["IntentParser", "INTENTS"]
```

- [ ] **Step 8.3: 验证（仅 import + JSON 解析）**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.intent_parser import IntentParser, INTENTS
# 只测 _parse_json
assert IntentParser._parse_json('{\"intent\": \"list\", \"args\": {}}') == {'intent': 'list', 'args': {}}
assert IntentParser._parse_json('garbage') == {'intent': 'unknown', 'args': {}}
assert IntentParser._parse_json('文字 {\"intent\": \"new\", \"args\": {\"goal\": \"x\"}} 后缀')['intent'] == 'new'
assert IntentParser._parse_json('{\"intent\": \"hack\", \"args\": {}}') == {'intent': 'unknown', 'args': {}}
print('parse_json OK')
"
ruff check src/tianshu/gateway/feishu/intent_parser.py 2>&1 | tail -3
```

⚠️ 如果 ProviderManager 接口与 plan 假设不一致（无 `acquire` 或 `complete` 方法），调整 `parse()` 内部调用即可，**不要**修改 `_parse_json` 与 `_build_prompt`（这两个不依赖 provider 接口）。

- [ ] **Step 8.4: commit**

```bash
git add src/tianshu/gateway/feishu/intent_parser.py
git commit -m "feat(feishu): IntentParser LLM 意图解析（容错 JSON + 白名单 intent）"
```

---

## Step 9: CardActionDispatcher 通用按钮分发

**目标：** 卡片按钮 value 协议统一处理。把 ApprovalCardHandler 中只识别审批 value 的逻辑拆出，新建通用 dispatcher 处理 `command` 字段。

**Files:**
- Create: `src/tianshu/gateway/feishu/card_action_dispatcher.py`
- Modify: `src/tianshu/gateway/feishu/approval_card.py`（仅识别审批专属 value）

### Sub-tasks

- [ ] **Step 9.1: 创建 card_action_dispatcher.py**

```python
# 文件：src/tianshu/gateway/feishu/card_action_dispatcher.py
"""卡片按钮通用分发器。

按钮 value 协议（v1.1）：
{
  "command": "select" | "list" | "budget" | "help" | "new" | "cancel",
  "edict_id"?: str,
  "goal"?: str,
  "filter"?: str,
}

转换：把按钮点击合成为等价的文本命令（如 /select <id>），重发给 ModeRouter。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.gateway.feishu.dispatcher import FeishuCardAction, FeishuMessage
    from tianshu.gateway.feishu.mode_router import ModeRouter

logger = logging.getLogger(__name__)


class CardActionDispatcher:
    def __init__(self, *, mode_router: "ModeRouter") -> None:
        self._mode_router = mode_router

    async def handle(self, action: "FeishuCardAction") -> None:
        """把卡片按钮 value 转成文本命令再走 ModeRouter。"""
        value = action.value or {}
        command = value.get("command")
        if not command:
            logger.warning("[feishu/card-dispatch] missing command in value=%s", value)
            return

        synthesized = self._synthesize(command, value)
        if synthesized is None:
            logger.warning("[feishu/card-dispatch] unknown command=%s", command)
            return

        # 构造一个伪 FeishuMessage 喂给 ModeRouter
        from tianshu.gateway.feishu.dispatcher import FeishuMessage
        fake_msg = FeishuMessage(
            event_id=action.event_id,
            chat_id=action.chat_id,
            chat_type="p2p",  # 卡片按钮一般在 chat 内点击；group 也走同流程
            sender_open_id=action.sender_open_id,
            text=synthesized,
            raw={"_from_card_button": True, "value": value},
        )
        await self._mode_router.dispatch(fake_msg)

    @staticmethod
    def _synthesize(command: str, value: dict) -> str | None:
        if command == "select":
            eid = value.get("edict_id") or ""
            return f"/select {eid}".strip() if eid else None
        if command == "list":
            f = value.get("filter") or "open"
            return f"/list {f}"
        if command == "budget":
            return "/budget"
        if command == "help":
            return "/help"
        if command == "new":
            goal = value.get("goal") or ""
            return f"/new {goal}".strip() if goal else None
        if command == "cancel":
            eid = value.get("edict_id") or ""
            return f"/cancel {eid}".strip() if eid else "/cancel"
        return None


__all__ = ["CardActionDispatcher"]
```

- [ ] **Step 9.2: 修改 approval_card.py — handle_button_click 加一层判断**

定位 `ApprovalCardHandler.handle_button_click`（`approval_card.py` 第 ~135 行）。在方法开头追加判断：如果 value 含 `command` 字段（v1.1 通用协议），返回 None 让上层分发到 CardActionDispatcher；只有原 v1 协议（含 `memorial_id` + `action`）才继续处理。

```python
async def handle_button_click(self, action: FeishuCardAction) -> None:
    """入站按钮点击 → submit_tool_decision。

    v1.1：仅处理审批专属 value（含 memorial_id + action）；
          含 'command' 字段的按钮由 CardActionDispatcher 处理。
    """
    value = action.value or {}
    if "command" in value:
        # 这是 v1.1 通用协议按钮，本 handler 不处理
        return
    memorial_id = value.get("memorial_id")
    act = value.get("action")
    scope = value.get("scope")
    if not (memorial_id and act in ("approve", "reject")):
        logger.warning("[feishu/card] malformed value=%s", value)
        return
    # ... 原有 try/except submit_tool_decision 逻辑保持不变
```

- [ ] **Step 9.3: 验证**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.gateway.feishu.card_action_dispatcher import CardActionDispatcher
assert CardActionDispatcher._synthesize('select', {'edict_id': 'ed_xxx'}) == '/select ed_xxx'
assert CardActionDispatcher._synthesize('list', {}) == '/list open'
assert CardActionDispatcher._synthesize('budget', {}) == '/budget'
assert CardActionDispatcher._synthesize('cancel', {}) == '/cancel'
assert CardActionDispatcher._synthesize('cancel', {'edict_id': 'ed_y'}) == '/cancel ed_y'
assert CardActionDispatcher._synthesize('foo', {}) is None
print('synthesize OK')
"
ruff check src/tianshu/gateway/feishu/card_action_dispatcher.py src/tianshu/gateway/feishu/approval_card.py 2>&1 | tail -3
```

- [ ] **Step 9.4: commit**

```bash
git add src/tianshu/gateway/feishu/card_action_dispatcher.py src/tianshu/gateway/feishu/approval_card.py
git commit -m "feat(feishu): CardActionDispatcher 按钮通用协议 + ApprovalCardHandler 仅识别审批 value"
```

---

## Step 10: FeishuBot 整合（注入 5 个新模块 + 重构 _on_message）

**目标：** 把所有新模块注入 FeishuBot，重构 `_on_message` / `_on_card` 走 ModeRouter / CardActionDispatcher。settings 加新字段。

**Files:**
- Modify: `src/tianshu/gateway/feishu/__init__.py`
- Modify: `src/tianshu/gateway/feishu/settings.py`
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/app.py`

### Sub-tasks

- [ ] **Step 10.1: settings.py 加新字段**

```python
# src/tianshu/gateway/feishu/settings.py
# 在 FeishuSettings dataclass 末尾追加字段：
assistant_persona_id: str = "tongzheng"   # 默认通政司
intent_llm_enabled: bool = True
disable_assistant_mode: bool = False       # 紧急逃生开关：true 时退回 v1 自动新建行为
```

并在 `from_global_settings(s)` 工厂里追加：

```python
return FeishuSettings(
    # ... 原有字段 ...
    assistant_persona_id=getattr(s, "feishu_assistant_persona_id", "tongzheng"),
    intent_llm_enabled=getattr(s, "feishu_intent_llm_enabled", True),
    disable_assistant_mode=getattr(s, "feishu_disable_assistant_mode", False),
)
```

- [ ] **Step 10.2: config.py 加 env 字段**

```python
# src/tianshu/config.py 在 feishu_dedup_cache_size 后追加：
feishu_assistant_persona_id: str = "tongzheng"
feishu_intent_llm_enabled: bool = True
feishu_disable_assistant_mode: bool = False
```

- [ ] **Step 10.3: tongzheng_api.py 加 channel_configs 字段**

修改 `src/tianshu/gateway/tongzheng_api.py`，`FeishuChannelConfig` Pydantic 模型加：

```python
class FeishuChannelConfig(BaseModel):
    # ... 原有字段 ...
    assistant_persona_id: str = "tongzheng"
    intent_llm_enabled: bool = True
```

`get_feishu_channel` / `put_feishu_channel` 函数中的 settings 从 DB / env 还原时也带上这两字段。

新增辅助接口：

```python
@tongzheng_router.get("/personas")
async def list_personas(request: Request) -> ApiResponse:
    """供前端下拉框使用：列所有可用 cabinet personas。"""
    loader = request.app.state.persona_loader
    personas = []
    for p in loader.list():
        personas.append({
            "id": p.id, "name": p.name, "department": p.department,
        })
    return ApiResponse(success=True, data={"personas": personas})
```

⚠️ 验证 `PersonaLoader` 是否有 `list()` 方法：

```bash
grep -nE "def list" /Users/chenjiamin/tiangong/tianshu/src/tianshu/persona/loader.py | head -5
```

如无则用以下兼容实现：

```python
# 通过迭代加载好的 personas 字典
loader = request.app.state.persona_loader
personas = [{"id": pid, "name": p.name, "department": p.department}
            for pid, p in getattr(loader, "_personas", {}).items()]
```

- [ ] **Step 10.4: 重构 FeishuBot.__init__ 注入新模块**

修改 `src/tianshu/gateway/feishu/__init__.py`：

```python
# 顶部追加 imports
from tianshu.gateway.feishu.assistant_branch import AssistantBranch
from tianshu.gateway.feishu.card_action_dispatcher import CardActionDispatcher
from tianshu.gateway.feishu.card_builder import CardBuilder
from tianshu.gateway.feishu.edict_branch import EdictBranch
from tianshu.gateway.feishu.intent_parser import IntentParser
from tianshu.gateway.feishu.mode_router import ModeRouter
from tianshu.gateway.feishu.persona_renderer import PersonaRenderer

# TYPE_CHECKING 块追加：
if TYPE_CHECKING:
    from tianshu.cost.manager import CostManager
    from tianshu.persona.loader import PersonaLoader
    from tianshu.providers.manager import ProviderManager

# __init__ 加新参数：
def __init__(
    self,
    *,
    storage: "Storage",
    event_bus: "EventBus",
    approval_manager: "ApprovalManager",
    executor: "Executor",
    notifier: "Notifier",
    settings: FeishuSettings,
    persona_loader: "PersonaLoader",
    provider_manager: "ProviderManager | None" = None,
    cost_manager: "CostManager | None" = None,
) -> None:
    # ... 原有赋值 ...
    self._persona_loader = persona_loader
    self._provider_manager = provider_manager
    self._cost_manager = cost_manager

    # 构造 PersonaRenderer
    persona = persona_loader.get(settings.assistant_persona_id)
    self._renderer = PersonaRenderer(persona)

    # 构造 CardBuilder
    self._card_builder = CardBuilder(storage=storage, cost_manager=cost_manager)

    # 构造 IntentParser（仅当 LLM 启用 + provider 可用）
    self._intent_parser: IntentParser | None = None
    if settings.intent_llm_enabled and provider_manager is not None:
        self._intent_parser = IntentParser(
            persona_loader=persona_loader,
            provider_manager=provider_manager,
            persona_id=settings.assistant_persona_id,
        )

    # 构造分支
    self._assistant_branch = AssistantBranch(
        storage=storage, anchor=self._anchor,
        edict_bridge=self._edict_bridge, outbound=self._outbound,
        renderer=self._renderer, card_builder=self._card_builder,
        intent_parser=self._intent_parser,
    )
    self._edict_branch = EdictBranch(
        storage=storage, anchor=self._anchor,
        edict_bridge=self._edict_bridge, outbound=self._outbound,
        renderer=self._renderer, assistant_branch=self._assistant_branch,
    )
    self._mode_router = ModeRouter(
        anchor=self._anchor,
        assistant_branch=self._assistant_branch,
        edict_branch=self._edict_branch,
    )
    self._card_action_dispatcher = CardActionDispatcher(mode_router=self._mode_router)
```

- [ ] **Step 10.5: 重构 _on_message / _on_card 走新流水线**

替换 `_on_message` / `_on_card`：

```python
async def _on_message(self, msg: FeishuMessage) -> None:
    logger.info(
        "[feishu/inbound] chat=%s sender=%s text=%.80s",
        msg.chat_id, msg.sender_open_id, msg.text,
    )
    # 紧急逃生：disable_assistant_mode=True 时退回 v1 行为
    if self._settings.disable_assistant_mode:
        await self._on_message_v1_legacy(msg)
        return
    await self._mode_router.dispatch(msg)

async def _on_card(self, action: FeishuCardAction) -> None:
    logger.info("[feishu/card] chat=%s value=%s", action.chat_id, action.value)
    value = action.value or {}
    # 兼容 v1 审批按钮（含 memorial_id + action）
    if "memorial_id" in value and "action" in value:
        await self._approval_card.handle_button_click(action)
        return
    # v1.1 通用协议按钮
    await self._card_action_dispatcher.handle(action)

async def _on_message_v1_legacy(self, msg: FeishuMessage) -> None:
    """紧急逃生路径：当 disable_assistant_mode=True 时使用。

    复制 v1 的 _on_message 命令处理逻辑，保留 /new /list 等不动作切换的行为。
    """
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
        await self._reply(msg.chat_id, f"✅ 新敕令 #{edict_id[:8]} 已创建（v1 模式）")
        return
    # 默认：续接或自动新建（v1 X1 行为）
    try:
        edict_id = await self._edict_bridge.continue_or_create(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, text=text,
        )
    except EdictBusyError as exc:
        await self._reply(msg.chat_id, str(exc))
        return
    await self._reply(msg.chat_id, f"✅ 已收到（敕令 #{edict_id[:8]}）")
```

⚠️ 保留旧的 `EdictStatus` import 与 `_reply` 方法（v1 legacy fallback 用）。

- [ ] **Step 10.6: 修改 reload — 切换 persona renderer**

在 `FeishuBot.reload(new_settings)` 末尾追加：

```python
# 切换 persona renderer
new_persona = self._persona_loader.get(new_settings.assistant_persona_id)
new_renderer = PersonaRenderer(new_persona)
self._renderer = new_renderer
self._assistant_branch.set_renderer(new_renderer)
self._edict_branch.set_renderer(new_renderer)

# 切换 IntentParser
if new_settings.intent_llm_enabled and self._provider_manager is not None:
    if self._intent_parser is None:
        from tianshu.gateway.feishu.intent_parser import IntentParser
        self._intent_parser = IntentParser(
            persona_loader=self._persona_loader,
            provider_manager=self._provider_manager,
            persona_id=new_settings.assistant_persona_id,
        )
    else:
        self._intent_parser.set_persona(new_settings.assistant_persona_id)
    self._assistant_branch._intent_parser = self._intent_parser  # type: ignore[attr-defined]
else:
    self._intent_parser = None
    self._assistant_branch._intent_parser = None  # type: ignore[attr-defined]
```

- [ ] **Step 10.7: app.py 注入 persona_loader / provider_manager / cost_manager 到 FeishuBot**

定位 `FeishuBot(...)` 实例化处，追加 kwargs：

```python
feishu_bot = FeishuBot(
    storage=storage,
    event_bus=event_bus,
    approval_manager=approval_manager,
    executor=executor,
    notifier=notifier,
    settings=feishu_settings,
    persona_loader=persona_loader,                 # 新增
    provider_manager=provider_manager,             # 新增
    cost_manager=cost_manager,                     # 新增（如果存在）
)
```

⚠️ 检查 `provider_manager / cost_manager` 实例化位置在 FeishuBot 之前。如不在，调整顺序（不要把 FeishuBot 放在它们之前）。grep:

```bash
grep -nE "provider_manager =|cost_manager =" /Users/chenjiamin/tiangong/tianshu/src/tianshu/app.py | head -5
```

- [ ] **Step 10.8: 端到端冒烟测试**

```bash
cd /Users/chenjiamin/tiangong/tianshu
pkill -f "uvicorn tianshu.app" 2>/dev/null || true
rm -f /tmp/feishu_v11_test.db ~/.tianshu/feishu_app_lock.test_v11
sleep 1

TIANSHU_FEISHU_APP_ID=test_v11 \
TIANSHU_FEISHU_APP_SECRET=secret \
TIANSHU_FEISHU_CONNECTION_MODE=webhook \
TIANSHU_DB_PATH=/tmp/feishu_v11_test.db \
TIANSHU_LLM_API_KEY=fake \
TIANSHU_LLM_API_BASE=http://localhost:9999 \
.venv/bin/python -m uvicorn tianshu.app:create_app --factory --port 8765 > /tmp/feishu_v11.log 2>&1 &
sleep 5

# 1. 第一条消息（无 anchor）→ 应进入助手模式（默认 silent reply）
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v11_1"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v11"}},
    "message": {"chat_id": "oc_v11", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"你好\"}"}}}'
sleep 2

# 2. /menu 命令 → 应回菜单卡片
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v11_2"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v11"}},
    "message": {"chat_id": "oc_v11", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"/menu\"}"}}}'
sleep 2

# 3. /new 命令 → 应进入敕令模式
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v11_3"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v11"}},
    "message": {"chat_id": "oc_v11", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"/new 写代码\"}"}}}'
sleep 2

# 4. /exit 命令 → 应回助手模式
curl -s -X POST http://localhost:8765/feishu/webhook -H 'Content-Type: application/json' -d '{
  "header": {"event_type": "im.message.receive_v1", "event_id": "ev_v11_4"},
  "event": {"sender": {"sender_id": {"open_id": "ou_v11"}},
    "message": {"chat_id": "oc_v11", "chat_type": "p2p", "message_type": "text",
      "content": "{\"text\": \"/exit\"}"}}}'
sleep 2

echo "--- relevant logs ---"
grep -E "feishu/(mode|inbound|outbound:|edict|persona)" /tmp/feishu_v11.log | head -30

echo "--- DB anchor ---"
sqlite3 /tmp/feishu_v11_test.db "SELECT chat_id, current_edict_id FROM feishu_session_anchor"

pkill -f "uvicorn tianshu.app" 2>/dev/null || true
```

Expected：
- 日志含 `[feishu/mode] chat=oc_v11 mode=assistant text=你好` → silent reply
- /menu 后 `[feishu/mode] mode=assistant text=/menu`
- /new 后 `mode=assistant text=/new 写代码` + `[feishu/edict] created edict=...`
- /exit 后日志含模式切换；DB anchor 表 oc_v11 行被删除

- [ ] **Step 10.9: 确认现有 v1 测试不受影响**

```bash
.venv/bin/python -m pytest tests/test_gateway.py tests/test_storage.py tests/gateway/feishu/ -q 2>&1 | tail -5
ruff check src/tianshu/gateway/feishu/ src/tianshu/app.py src/tianshu/config.py 2>&1 | tail -3
```

⚠️ v1 的 `tests/gateway/feishu/test_feishu_bot.py` 与 `test_e2e_webhook.py` 等测试**可能因为 FeishuBot 构造函数改了签名而失败**。如果失败，更新这些测试的 fixture 加 `persona_loader=MagicMock()` 等 kwarg。

- [ ] **Step 10.10: commit**

```bash
git add src/tianshu/gateway/feishu/__init__.py src/tianshu/gateway/feishu/settings.py src/tianshu/config.py src/tianshu/app.py src/tianshu/gateway/tongzheng_api.py
git commit -m "feat(feishu): FeishuBot 整合双模式（ModeRouter + 5 新模块） + 紧急逃生开关"
```

---

## Step 11: 升级通告卡片（一次性 + 幂等）

**目标：** v1.1 上线后第一次 reload，对所有现有 anchor 的 chat 发一次升级通告，幂等（重启不重发）。

**Files:**
- Modify: `src/tianshu/storage.py`（加幂等表方法）
- Modify: `src/tianshu/gateway/feishu/__init__.py`（启动时触发）

### Sub-tasks

- [ ] **Step 11.1: storage 加 has/mark 幂等记录**

复用 `feishu_pending_cards` 表，用特殊 `kind='upgrade_notice_v1_1'`，approval_id 设为 chat_id：

```python
# storage.py 追加：

def has_sent_upgrade_notice(self, chat_id: str, version_tag: str) -> bool:
    row = self._conn.execute(
        "SELECT 1 FROM feishu_pending_cards "
        "WHERE approval_id = ? AND kind = ?",
        (chat_id, f"upgrade_notice_{version_tag}"),
    ).fetchone()
    return row is not None

def mark_upgrade_notice_sent(self, chat_id: str, version_tag: str) -> None:
    from datetime import datetime, UTC
    self._conn.execute(
        "INSERT OR IGNORE INTO feishu_pending_cards "
        "(approval_id, chat_id, message_id, kind, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (chat_id, chat_id, "", f"upgrade_notice_{version_tag}",
         datetime.now(UTC).isoformat()),
    )
    self._conn.commit()

def list_active_anchor_chats(self) -> list[str]:
    rows = self._conn.execute(
        "SELECT chat_id FROM feishu_session_anchor WHERE current_edict_id IS NOT NULL"
    ).fetchall()
    return [row[0] for row in rows]
```

- [ ] **Step 11.2: FeishuBot 启动时发升级通告**

在 `FeishuBot.start()` 末尾（所有订阅就绪后）追加：

```python
async def _send_upgrade_notice_once(self) -> None:
    """v1.1 升级通告：对所有现有 anchor 的 chat 发一次。幂等（重复启动不重发）。"""
    version_tag = "v1_1"
    chats = self._storage.list_active_anchor_chats()
    for chat_id in chats:
        if self._storage.has_sent_upgrade_notice(chat_id, version_tag):
            continue
        try:
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "indigo",
                    "title": {"tag": "plain_text", "content": "🆙 飞书助手升级 v1.1"},
                },
                "elements": [
                    {"tag": "markdown", "content":
                        "**新功能**：\n"
                        "- 助手模式（无敕令时输入 `/menu` `/list` `/budget`）\n"
                        "- 自然语言识别（如 \"显示我的列表\"）\n\n"
                        "**现有敕令绑定保持不变**\n"
                        "输入 `/help` 查看完整命令列表"},
                ],
            }
            await self._outbound.send_card(chat_id, card)
            self._storage.mark_upgrade_notice_sent(chat_id, version_tag)
        except Exception:
            logger.exception("[feishu] upgrade notice send failed for chat=%s", chat_id)

# start() 末尾追加调用：
await self._send_upgrade_notice_once()
```

- [ ] **Step 11.3: 验证（用 mock 跑一次）**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -c "
from tianshu.storage import Storage
s = Storage('/tmp/notice_test.db')
s.init_db()
s.set_feishu_anchor('oc_a', 'ed_1')
s.set_feishu_anchor('oc_b', 'ed_2')
print('chats:', s.list_active_anchor_chats())
assert s.has_sent_upgrade_notice('oc_a', 'v1_1') is False
s.mark_upgrade_notice_sent('oc_a', 'v1_1')
assert s.has_sent_upgrade_notice('oc_a', 'v1_1') is True
print('OK')
"
rm -f /tmp/notice_test.db
ruff check src/tianshu/storage.py src/tianshu/gateway/feishu/__init__.py 2>&1 | tail -3
```

- [ ] **Step 11.4: commit**

```bash
git add src/tianshu/storage.py src/tianshu/gateway/feishu/__init__.py
git commit -m "feat(feishu): v1.1 升级通告卡片（幂等 + 仅一次）"
```

---

## Step 12: 通政司 Web 前端扩展（助手分卡）

**目标：** 在 TongzhengPage.tsx 加助手 persona 下拉 + LLM 增强 checkbox + 保存逻辑。

**Files:**
- Modify: `web/src/api/tongzheng.ts`
- Modify: `web/src/pages/TongzhengPage.tsx`

### Sub-tasks

- [ ] **Step 12.1: api/tongzheng.ts 扩展类型 + 加 listPersonas**

```typescript
// web/src/api/tongzheng.ts 追加字段到 FeishuChannelConfig 接口：
assistant_persona_id: string;
intent_llm_enabled: boolean;

// 同步加到 FeishuChannelView：
assistant_persona_id: string;
intent_llm_enabled: boolean;

// 文件末尾追加：
export interface PersonaSummary {
  id: string;
  name: string;
  department: string;
}

export async function listPersonas(): Promise<PersonaSummary[]> {
  const { data } = await apiClient.get("/tongzheng/personas");
  return data.data?.personas ?? [];
}
```

- [ ] **Step 12.2: TongzhengPage.tsx 加助手分卡**

在文件中现有「飞书机器人」Card 之后、PageContainer 闭合之前，追加：

```tsx
// 顶部 imports 追加：
import { listPersonas, type PersonaSummary } from "../api/tongzheng";

// 组件内追加 query：
const { data: personas } = useQuery({
  queryKey: ["tongzheng", "personas"],
  queryFn: listPersonas,
});

// 在「飞书机器人」Card 闭合 </Card> 后追加助手分卡：
<Card
  title={
    <Space>
      <span>🤖 飞书助手</span>
    </Space>
  }
  style={{ marginTop: 16 }}
>
  <Alert
    type="info"
    showIcon
    message="助手是飞书侧的命令路由 + 自然语言意图层"
    description={
      <ul style={{ margin: 0, paddingLeft: 20 }}>
        <li>选一个 cabinet persona 兼任飞书助手，借用其名字 + emoji 作为回信人格</li>
        <li>启用 LLM 意图增强后，纯文本（如"显示列表"）可解析为命令；每条非命令消息会调一次 persona 的 LLM</li>
      </ul>
    }
    style={{ marginBottom: 16 }}
  />

  <Form form={form} layout="vertical">
    <Form.Item
      label="助手 Persona"
      name="assistant_persona_id"
      extra="助手用此 persona 的人格渲染回信；不影响该 persona 原本的敕令任务"
    >
      <Select
        placeholder="选择一个 persona"
        options={(personas ?? []).map((p) => ({
          value: p.id,
          label: `${p.name}（${p.department}）`,
        }))}
      />
    </Form.Item>

    <Form.Item
      label="启用 LLM 意图增强"
      name="intent_llm_enabled"
      valuePropName="checked"
      extra="开启后，自然语言（如"显示我的列表"）会过 persona 的 LLM 解析为命令"
    >
      <Switch />
    </Form.Item>
  </Form>
</Card>
```

⚠️ `Switch` 已经在 v1 import 列表中（如未则加）；`Alert` 同。

- [ ] **Step 12.3: 验证 TypeScript 编译**

```bash
cd /Users/chenjiamin/tiangong/tianshu/web
pnpm install --frozen-lockfile 2>&1 | tail -3
pnpm tsc --noEmit 2>&1 | tail -10
```

⚠️ 如果有 pre-existing 错误（如 `PersonaDetailPage.tsx`），关注**新引入的错误**。如果只是新增字段类型未加到 form 默认值，按 TS 错误信息补全 `initialValues`。

- [ ] **Step 12.4: commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add web/src/api/tongzheng.ts web/src/pages/TongzhengPage.tsx
git commit -m "feat(tongzheng): web 助手分卡（persona 下拉 + LLM 增强 checkbox）"
```

---

## Step 13: 测试补齐到 80%+

**目标：** 6 个新模块各加单元/集成测试；e2e 测试覆盖双模式切换核心路径。

### Task 13.1: test_persona_renderer.py

**Files:** Create `tests/gateway/feishu/test_persona_renderer.py`

- [ ] **Step 1: 写测试**

```python
# tests/gateway/feishu/test_persona_renderer.py
"""PersonaRenderer 单元测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from tianshu.gateway.feishu.persona_renderer import (
    DEFAULT_EMOJI, DEFAULT_NAME, PersonaRenderer,
)


def test_renderer_with_persona():
    p = MagicMock()
    p.name = "通政司"
    p.department = "tongzheng"
    r = PersonaRenderer(p)
    assert r.name == "通政司"
    assert r.emoji == "📜"
    assert "通政司" in r.welcome()
    assert "💼" in r.assistant_tag()


def test_renderer_with_unknown_department():
    p = MagicMock()
    p.name = "测试"
    p.department = "unknown_dept"
    r = PersonaRenderer(p)
    assert r.emoji == DEFAULT_EMOJI


def test_renderer_with_none_persona():
    r = PersonaRenderer(None)
    assert r.name == DEFAULT_NAME
    assert r.emoji == DEFAULT_EMOJI


def test_edict_tag_truncates_id():
    r = PersonaRenderer(None)
    assert r.edict_tag("ed_abcdef1234567890") == "📋 敕令 #ed_abcde"


def test_help_modes_differ():
    r = PersonaRenderer(None)
    a = r.help_assistant()
    e = r.help_edict("ed_xxx")
    assert "/new" in a and "/list" in a
    assert "/exit" in e and "续接" in e


def test_unknown_command_reply():
    r = PersonaRenderer(None)
    msg = r.unknown_command_reply("💼 助手", "/foo")
    assert "/foo" in msg and "/help" in msg
```

- [ ] **Step 2: 跑测试**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -m pytest tests/gateway/feishu/test_persona_renderer.py -v 2>&1 | tail -10
```

- [ ] **Step 3: commit**

```bash
git add tests/gateway/feishu/test_persona_renderer.py
git commit -m "test(feishu): PersonaRenderer 单元（含默认 fallback）"
```

### Task 13.2: test_mode_router.py

**Files:** Create `tests/gateway/feishu/test_mode_router.py`

- [ ] **Step 1: 写测试**

```python
# tests/gateway/feishu/test_mode_router.py
"""ModeRouter 单元测试：状态机判定 + 分发。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.dispatcher import FeishuMessage
from tianshu.gateway.feishu.mode_router import ModeContext, ModeRouter


def _make_msg(chat_id="oc_x", sender="ou_a", text="hi") -> FeishuMessage:
    return FeishuMessage(
        event_id="e", chat_id=chat_id, chat_type="p2p",
        sender_open_id=sender, text=text, raw={},
    )


def test_resolve_assistant_mode_when_no_anchor():
    anchor = MagicMock()
    anchor.get.return_value = None
    router = ModeRouter(
        anchor=anchor, assistant_branch=MagicMock(), edict_branch=MagicMock(),
    )
    ctx = router.resolve_mode("oc_x")
    assert ctx.mode == "assistant"
    assert ctx.edict_id is None


def test_resolve_edict_mode_when_anchored():
    anchor = MagicMock()
    anchor.get.return_value = "ed_abc"
    router = ModeRouter(
        anchor=anchor, assistant_branch=MagicMock(), edict_branch=MagicMock(),
    )
    ctx = router.resolve_mode("oc_x")
    assert ctx.mode == "edict"
    assert ctx.edict_id == "ed_abc"


@pytest.mark.asyncio
async def test_dispatch_to_assistant_when_no_anchor():
    anchor = MagicMock(); anchor.get.return_value = None
    assistant = MagicMock(); assistant.handle = AsyncMock()
    edict = MagicMock(); edict.handle = AsyncMock()
    router = ModeRouter(anchor=anchor, assistant_branch=assistant, edict_branch=edict)
    await router.dispatch(_make_msg(text="/menu"))
    assistant.handle.assert_awaited_once()
    edict.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_to_edict_when_anchored():
    anchor = MagicMock(); anchor.get.return_value = "ed_abc"
    assistant = MagicMock(); assistant.handle = AsyncMock()
    edict = MagicMock(); edict.handle = AsyncMock()
    router = ModeRouter(anchor=anchor, assistant_branch=assistant, edict_branch=edict)
    await router.dispatch(_make_msg(text="hi"))
    edict.handle.assert_awaited_once()
    assistant.handle.assert_not_awaited()
    # 验证 ctx.edict_id 透传
    ctx_arg = edict.handle.await_args.args[1]
    assert isinstance(ctx_arg, ModeContext)
    assert ctx_arg.edict_id == "ed_abc"
```

- [ ] **Step 2: 跑 + commit**

```bash
.venv/bin/python -m pytest tests/gateway/feishu/test_mode_router.py -v 2>&1 | tail -10
git add tests/gateway/feishu/test_mode_router.py
git commit -m "test(feishu): ModeRouter 状态机 + 分发"
```

### Task 13.3: test_intent_parser.py

**Files:** Create `tests/gateway/feishu/test_intent_parser.py`

- [ ] **Step 1: 写测试**

```python
# tests/gateway/feishu/test_intent_parser.py
"""IntentParser 单元测试：JSON 容错 + 白名单 intent。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.intent_parser import INTENTS, IntentParser


def test_parse_json_valid():
    r = IntentParser._parse_json('{"intent": "list", "args": {}}')
    assert r == {"intent": "list", "args": {}}


def test_parse_json_with_preamble():
    r = IntentParser._parse_json('解析结果 {"intent": "new", "args": {"goal": "x"}} 完成')
    assert r["intent"] == "new"
    assert r["args"]["goal"] == "x"


def test_parse_json_garbage():
    assert IntentParser._parse_json("garbage") == {"intent": "unknown", "args": {}}


def test_parse_json_intent_not_in_whitelist():
    r = IntentParser._parse_json('{"intent": "drop_table", "args": {}}')
    assert r == {"intent": "unknown", "args": {}}


def test_parse_json_args_not_dict():
    r = IntentParser._parse_json('{"intent": "list", "args": "not-a-dict"}')
    assert r == {"intent": "list", "args": {}}


def test_parse_json_empty():
    assert IntentParser._parse_json("") == {"intent": "unknown", "args": {}}


def test_intents_constant_complete():
    expected = {"new", "list", "status", "cancel", "budget", "help", "unknown"}
    assert set(INTENTS) == expected


@pytest.mark.asyncio
async def test_parse_when_persona_missing_returns_unknown():
    loader = MagicMock(); loader.get.return_value = None
    parser = IntentParser(
        persona_loader=loader, provider_manager=MagicMock(), persona_id="missing",
    )
    r = await parser.parse("显示列表")
    assert r == {"intent": "unknown", "args": {}}


@pytest.mark.asyncio
async def test_parse_when_no_llm_config_returns_unknown():
    p = MagicMock(); p.llm_config_name = None
    loader = MagicMock(); loader.get.return_value = p
    parser = IntentParser(
        persona_loader=loader, provider_manager=MagicMock(), persona_id="x",
    )
    r = await parser.parse("hi")
    assert r == {"intent": "unknown", "args": {}}


@pytest.mark.asyncio
async def test_parse_llm_failure_returns_unknown():
    p = MagicMock(); p.llm_config_name = "haiku"; p.name = "通政司"
    loader = MagicMock(); loader.get.return_value = p
    client = MagicMock(); client.complete = AsyncMock(side_effect=RuntimeError("net err"))
    pm = MagicMock(); pm.acquire.return_value = client
    parser = IntentParser(persona_loader=loader, provider_manager=pm, persona_id="x")
    r = await parser.parse("hi")
    assert r == {"intent": "unknown", "args": {}}


@pytest.mark.asyncio
async def test_parse_llm_returns_valid_intent():
    p = MagicMock(); p.llm_config_name = "haiku"; p.name = "通政司"
    loader = MagicMock(); loader.get.return_value = p
    client = MagicMock()
    client.complete = AsyncMock(return_value='{"intent": "list", "args": {}}')
    pm = MagicMock(); pm.acquire.return_value = client
    parser = IntentParser(persona_loader=loader, provider_manager=pm, persona_id="x")
    r = await parser.parse("看看我的列表")
    assert r == {"intent": "list", "args": {}}
```

- [ ] **Step 2: 跑 + commit**

```bash
.venv/bin/python -m pytest tests/gateway/feishu/test_intent_parser.py -v 2>&1 | tail -10
git add tests/gateway/feishu/test_intent_parser.py
git commit -m "test(feishu): IntentParser JSON 容错 + LLM mock"
```

### Task 13.4: test_card_builder.py

**Files:** Create `tests/gateway/feishu/test_card_builder.py`

- [ ] **Step 1: 写测试**

```python
# tests/gateway/feishu/test_card_builder.py
"""CardBuilder 单元测试。"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from tianshu.gateway.feishu.card_builder import CardBuilder


class _E:
    def __init__(self, id, title, status):
        self.id, self.title, self.status = id, title, status


def test_menu_card():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    card = cb.build_menu_card()
    assert card["header"]["template"] == "purple"
    actions = card["elements"][1]["actions"]
    assert {a["value"]["command"] for a in actions} == {"list", "budget", "help"}


def test_list_card_marks_anchor_with_star():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_a", "写代码", "open"), _E("ed_b", "总结", "open")]
    card = cb.build_list_card(edicts, current_anchor="ed_a")
    assert "★" in card["elements"][0]["content"]
    assert card["elements"][1]["actions"][0]["type"] == "primary"
    # 第二条无星
    assert "★" not in card["elements"][3]["content"]


def test_list_card_truncates_long_title():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    long_title = "x" * 100
    edicts = [_E("ed_a", long_title, "open")]
    card = cb.build_list_card(edicts)
    assert len(card["elements"][0]["content"]) < 100


@pytest.mark.asyncio
async def test_budget_card_no_cost_manager():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    card = await cb.build_budget_card()
    assert card["header"]["template"] == "grey"
    assert "暂时无法获取" in card["elements"][0]["content"]


@pytest.mark.asyncio
async def test_budget_card_with_data():
    storage = MagicMock()
    storage._conn.execute.return_value.fetchall.return_value = [
        ("ed_a", 3.21), ("ed_b", 2.10),
    ]
    storage._conn.execute.return_value.fetchone.return_value = (5.31,)
    storage.get_edict.return_value = MagicMock(title="测试敕令")
    cm = MagicMock()
    budget = MagicMock(); budget.budget_cny = 100.0; budget.spent_cny = 5.31
    cm.get_budget.return_value = budget
    cb = CardBuilder(storage=storage, cost_manager=cm)
    card = await cb.build_budget_card()
    assert card["header"]["template"] == "orange"
    md_content = card["elements"][0]["content"]
    assert "近 7 天消费" in md_content
```

- [ ] **Step 2: 跑 + commit**

```bash
.venv/bin/python -m pytest tests/gateway/feishu/test_card_builder.py -v 2>&1 | tail -10
git add tests/gateway/feishu/test_card_builder.py
git commit -m "test(feishu): CardBuilder /list /menu /budget 卡片 schema"
```

### Task 13.5: test_assistant_branch.py

**Files:** Create `tests/gateway/feishu/test_assistant_branch.py`

- [ ] **Step 1: 写测试**

```python
# tests/gateway/feishu/test_assistant_branch.py
"""AssistantBranch 单元测试：助手模式命令路由。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.assistant_branch import AssistantBranch
from tianshu.gateway.feishu.dispatcher import FeishuMessage
from tianshu.gateway.feishu.mode_router import ModeContext


def _msg(text="hi", chat="oc_x", sender="ou_a") -> FeishuMessage:
    return FeishuMessage(
        event_id="e", chat_id=chat, chat_type="p2p",
        sender_open_id=sender, text=text, raw={},
    )


def _ctx(chat="oc_x") -> ModeContext:
    return ModeContext(mode="assistant", chat_id=chat, sender_open_id="ou_a", edict_id=None)


def _renderer():
    r = MagicMock()
    r.assistant_tag.return_value = "💼 助手"
    r.assistant_silent_reply.return_value = "💼 待命中"
    r.help_assistant.return_value = "help"
    r.unknown_command_reply.side_effect = lambda tag, cmd: f"{tag} 未识 {cmd}"
    r.edict_created_reply.side_effect = lambda eid, t: f"✅ 新敕令 #{eid[:8]}"
    r.edict_selected_reply.side_effect = lambda eid, t: f"📋 已切换 #{eid[:8]}"
    r.edict_cancel_reply.side_effect = lambda eid: f"📋 已取消 #{eid[:8]}"
    r.llm_intent_hint.side_effect = lambda i: f"💡 我理解你想：{i}"
    return r


@pytest.fixture
def branch():
    storage = MagicMock()
    anchor = MagicMock()
    bridge = MagicMock(); bridge.create_new = AsyncMock(return_value="ed_new1234")
    outbound = MagicMock(); outbound.send_text = AsyncMock(); outbound.send_card = AsyncMock()
    cb = MagicMock(); cb.build_menu_card.return_value = {"header": {}}
    cb.build_list_card.return_value = {"header": {}}
    cb.build_budget_card = AsyncMock(return_value={"header": {}})
    return AssistantBranch(
        storage=storage, anchor=anchor, edict_bridge=bridge,
        outbound=outbound, renderer=_renderer(), card_builder=cb,
        intent_parser=None,
    ), storage, anchor, outbound, cb


@pytest.mark.asyncio
async def test_help_command(branch):
    b, _, _, outbound, _ = branch
    await b.handle(_msg("/help"), _ctx())
    outbound.send_text.assert_awaited()
    assert "help" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_new_command_creates_edict(branch):
    b, _, anchor, outbound, _ = branch
    await b.handle(_msg("/new 写代码"), _ctx())
    b._edict_bridge.create_new.assert_awaited_once()
    assert "ed_new1234" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_new_without_goal_shows_usage(branch):
    b, _, _, outbound, _ = branch
    await b.handle(_msg("/new"), _ctx())
    assert "用法" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_menu_command_sends_card(branch):
    b, _, _, outbound, cb = branch
    await b.handle(_msg("/menu"), _ctx())
    outbound.send_card.assert_awaited()
    cb.build_menu_card.assert_called_once()


@pytest.mark.asyncio
async def test_list_command_empty(branch):
    b, storage, _, outbound, _ = branch
    storage.list_edicts.return_value = []
    await b.handle(_msg("/list"), _ctx())
    assert "暂无敕令" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_list_command_sends_card(branch):
    b, storage, _, outbound, cb = branch
    storage.list_edicts.return_value = [MagicMock(id="ed_a", title="x", status="open")]
    await b.handle(_msg("/list"), _ctx())
    outbound.send_card.assert_awaited()
    cb.build_list_card.assert_called_once()


@pytest.mark.asyncio
async def test_select_short_id_rejected(branch):
    b, _, _, outbound, _ = branch
    await b.handle(_msg("/select abc"), _ctx())
    assert "至少 6" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_select_no_match(branch):
    b, storage, _, outbound, _ = branch
    storage.list_edicts.return_value = []
    await b.handle(_msg("/select abcdef12"), _ctx())
    assert "未找到" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_select_unique_match(branch):
    b, storage, anchor, outbound, _ = branch
    e = MagicMock(); e.id = "ed_abcdef1234"; e.title = "测试"
    storage.list_edicts.return_value = [e]
    await b.handle(_msg("/select ed_abcdef"), _ctx())
    anchor.set.assert_called_with("oc_x", "ed_abcdef1234")


@pytest.mark.asyncio
async def test_unknown_slash_command(branch):
    b, _, _, outbound, _ = branch
    await b.handle(_msg("/foo"), _ctx())
    assert "未识" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_natural_language_no_intent_parser_silent(branch):
    b, _, _, outbound, _ = branch
    await b.handle(_msg("你好"), _ctx())
    assert "待命" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_natural_language_with_intent_parser_list():
    storage = MagicMock(); storage.list_edicts.return_value = []
    anchor = MagicMock()
    bridge = MagicMock(); bridge.create_new = AsyncMock()
    outbound = MagicMock(); outbound.send_text = AsyncMock(); outbound.send_card = AsyncMock()
    cb = MagicMock()
    cb.build_list_card.return_value = {"header": {}}
    cb.build_menu_card.return_value = {"header": {}}
    cb.build_budget_card = AsyncMock()
    parser = MagicMock()
    parser.parse = AsyncMock(return_value={"intent": "list", "args": {}})
    b = AssistantBranch(
        storage=storage, anchor=anchor, edict_bridge=bridge, outbound=outbound,
        renderer=_renderer(), card_builder=cb, intent_parser=parser,
    )
    await b.handle(_msg("看看列表"), _ctx())
    parser.parse.assert_awaited_once()
    # 第一次 send_text 是意图提示
    assert "我理解你想" in outbound.send_text.await_args_list[0].args[1]
```

- [ ] **Step 2: 跑 + commit**

```bash
.venv/bin/python -m pytest tests/gateway/feishu/test_assistant_branch.py -v 2>&1 | tail -15
git add tests/gateway/feishu/test_assistant_branch.py
git commit -m "test(feishu): AssistantBranch 9 命令 + 自然语言路径"
```

### Task 13.6: test_edict_branch.py

**Files:** Create `tests/gateway/feishu/test_edict_branch.py`

- [ ] **Step 1: 写测试**

```python
# tests/gateway/feishu/test_edict_branch.py
"""EdictBranch 单元测试：敕令模式命令 + /exit + 续接。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.dispatcher import FeishuMessage
from tianshu.gateway.feishu.edict_branch import EdictBranch
from tianshu.gateway.feishu.mode_router import ModeContext
from tianshu.models.common import EdictStatus


def _msg(text="hi", chat="oc_x") -> FeishuMessage:
    return FeishuMessage(
        event_id="e", chat_id=chat, chat_type="p2p",
        sender_open_id="ou_a", text=text, raw={},
    )


def _ctx(eid="ed_anchor1") -> ModeContext:
    return ModeContext(mode="edict", chat_id="oc_x", sender_open_id="ou_a", edict_id=eid)


def _renderer():
    r = MagicMock()
    r.edict_tag.side_effect = lambda eid: f"📋 #{eid[:8]}"
    r.edict_exit_reply.return_value = "💼 已退出"
    r.edict_received_reply.side_effect = lambda eid: f"📋 已收到 #{eid[:8]}"
    r.edict_cancel_reply.side_effect = lambda eid: f"📋 已取消 #{eid[:8]}"
    r.help_edict.return_value = "help-edict"
    r.unknown_command_reply.side_effect = lambda tag, cmd: f"{tag} 未识 {cmd}"
    r.edict_created_reply.side_effect = lambda eid, t: f"✅ #{eid[:8]}"
    return r


@pytest.fixture
def branch():
    storage = MagicMock()
    anchor = MagicMock()
    bridge = MagicMock()
    bridge.create_new = AsyncMock(return_value="ed_new5678")
    bridge.continue_or_create = AsyncMock(return_value="ed_anchor1")
    outbound = MagicMock(); outbound.send_text = AsyncMock()
    assistant = MagicMock(); assistant.handle = AsyncMock()
    return EdictBranch(
        storage=storage, anchor=anchor, edict_bridge=bridge,
        outbound=outbound, renderer=_renderer(),
        assistant_branch=assistant,
    ), storage, anchor, outbound, assistant


@pytest.mark.asyncio
async def test_exit_clears_anchor(branch):
    b, storage, _, outbound, _ = branch
    await b.handle(_msg("/exit"), _ctx())
    storage.delete_feishu_anchor.assert_called_with("oc_x")
    assert "已退出" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_new_in_edict_mode_exits_then_creates(branch):
    b, storage, _, outbound, _ = branch
    await b.handle(_msg("/new 新目标"), _ctx())
    storage.delete_feishu_anchor.assert_called_with("oc_x")
    b._edict_bridge.create_new.assert_awaited_once()
    assert "ed_new567" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_status_default_uses_anchor(branch):
    b, storage, _, outbound, _ = branch
    e = MagicMock(); e.id = "ed_anchor1"; e.title = "x"; e.status = "open"
    storage.get_edict.return_value = e
    await b.handle(_msg("/status"), _ctx())
    storage.get_edict.assert_called_with("ed_anchor1")
    assert "ed_ancho" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_cancel_default_uses_anchor_and_clears(branch):
    b, storage, anchor, outbound, _ = branch
    e = MagicMock(); e.id = "ed_anchor1"; e.status = EdictStatus.OPEN
    storage.get_edict.return_value = e
    anchor.get.return_value = "ed_anchor1"
    await b.handle(_msg("/cancel"), _ctx())
    storage.update_edict_status.assert_called_with("ed_anchor1", EdictStatus.CANCELLED.value)
    storage.delete_feishu_anchor.assert_called_with("oc_x")
    assert "自动退出" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_query_commands_delegate_to_assistant(branch):
    b, _, _, _, assistant = branch
    await b.handle(_msg("/list"), _ctx())
    assistant.handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_text_continues(branch):
    b, _, _, outbound, _ = branch
    await b.handle(_msg("补一句"), _ctx())
    b._edict_bridge.continue_or_create.assert_awaited_once()
    assert "已收到" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_help_in_edict_mode(branch):
    b, _, _, outbound, _ = branch
    await b.handle(_msg("/help"), _ctx())
    assert "help-edict" in outbound.send_text.await_args.args[1]
```

- [ ] **Step 2: 跑 + commit**

```bash
.venv/bin/python -m pytest tests/gateway/feishu/test_edict_branch.py -v 2>&1 | tail -10
git add tests/gateway/feishu/test_edict_branch.py
git commit -m "test(feishu): EdictBranch /exit /new /status /cancel + 续接"
```

### Task 13.7: test_e2e_dual_mode.py

**Files:** Create `tests/gateway/feishu/test_e2e_dual_mode.py`

- [ ] **Step 1: 写测试**

```python
# tests/gateway/feishu/test_e2e_dual_mode.py
"""端到端：webhook → 双模式切换核心路径。"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_dual_mode(monkeypatch):
    monkeypatch.setenv("TIANSHU_FEISHU_APP_ID", "test_v11")
    monkeypatch.setenv("TIANSHU_FEISHU_APP_SECRET", "secret")
    monkeypatch.setenv("TIANSHU_FEISHU_CONNECTION_MODE", "webhook")
    monkeypatch.setenv("TIANSHU_FEISHU_TEXT_BATCH_DELAY", "0.0")
    monkeypatch.setenv("TIANSHU_FEISHU_INTENT_LLM_ENABLED", "false")  # 关 LLM 简化测试
    monkeypatch.setenv("TIANSHU_LLM_API_KEY", "fake")
    monkeypatch.setenv("TIANSHU_LLM_API_BASE", "http://localhost:9999")
    from tianshu.app import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client, app


def _msg(text: str, *, event_id: str, chat="oc_e2e_dual", sender="ou_e2e") -> dict:
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


def test_first_message_in_assistant_mode(app_dual_mode):
    client, app = app_dual_mode
    r = client.post("/feishu/webhook", json=_msg("你好", event_id="ev_a1"))
    assert r.status_code == 200
    time.sleep(1.0)
    # anchor 应不存在（助手模式）
    storage = app.state.storage
    assert storage.get_feishu_anchor("oc_e2e_dual") is None
    # 不应自动创建敕令
    assert storage.list_edicts() == [] or all(
        e.metadata.get("chat_id") != "oc_e2e_dual" for e in storage.list_edicts()
    )


def test_new_command_enters_edict_mode(app_dual_mode):
    client, app = app_dual_mode
    r = client.post("/feishu/webhook", json=_msg("/new 写代码", event_id="ev_a2"))
    assert r.status_code == 200
    time.sleep(1.5)
    # anchor 应被设置
    storage = app.state.storage
    eid = storage.get_feishu_anchor("oc_e2e_dual")
    assert eid is not None
    edict = storage.get_edict(eid)
    assert edict is not None
    assert edict.metadata.get("chat_id") == "oc_e2e_dual"


def test_exit_clears_anchor(app_dual_mode):
    client, app = app_dual_mode
    client.post("/feishu/webhook", json=_msg("/new 任务", event_id="ev_b1"))
    time.sleep(1.5)
    storage = app.state.storage
    assert storage.get_feishu_anchor("oc_e2e_dual") is not None
    client.post("/feishu/webhook", json=_msg("/exit", event_id="ev_b2"))
    time.sleep(1.0)
    assert storage.get_feishu_anchor("oc_e2e_dual") is None


def test_continue_in_edict_mode(app_dual_mode):
    client, app = app_dual_mode
    client.post("/feishu/webhook", json=_msg("/new 主任务", event_id="ev_c1"))
    time.sleep(1.5)
    storage = app.state.storage
    initial_eid = storage.get_feishu_anchor("oc_e2e_dual")

    # 在敕令模式下发纯文本 → 不应新建，仅续接（memorial 数 +1，但 active 限制可能阻断）
    client.post("/feishu/webhook", json=_msg("补充说明", event_id="ev_c2"))
    time.sleep(1.0)
    # anchor 仍是同一 edict
    assert storage.get_feishu_anchor("oc_e2e_dual") == initial_eid


def test_help_command_works_in_both_modes(app_dual_mode):
    client, _ = app_dual_mode
    r1 = client.post("/feishu/webhook", json=_msg("/help", event_id="ev_d1"))
    assert r1.status_code == 200
    # /new 进敕令模式
    client.post("/feishu/webhook", json=_msg("/new x", event_id="ev_d2"))
    time.sleep(1.5)
    # 敕令模式 /help
    r2 = client.post("/feishu/webhook", json=_msg("/help", event_id="ev_d3"))
    assert r2.status_code == 200
```

- [ ] **Step 2: 跑 + commit**

```bash
.venv/bin/python -m pytest tests/gateway/feishu/test_e2e_dual_mode.py -v 2>&1 | tail -15
git add tests/gateway/feishu/test_e2e_dual_mode.py
git commit -m "test(feishu): e2e 双模式切换核心路径"
```

### Task 13.8: 覆盖率核对

- [ ] **Step 1: 跑全集 + 覆盖率**

```bash
cd /Users/chenjiamin/tiangong/tianshu
.venv/bin/python -m pytest tests/gateway/feishu/ --cov=src/tianshu/gateway/feishu --cov-report=term-missing 2>&1 | tail -25
```

Expected: `src/tianshu/gateway/feishu/` 总覆盖率 ≥ 80%。如某文件 < 80%，回到对应 task 补测试。

- [ ] **Step 2: 现有测试不受影响**

```bash
.venv/bin/python -m pytest tests/ -q -x 2>&1 | tail -10
```

⚠️ 如有遗留测试失败（pre-existing），用 git stash 法验证非本次引入。

- [ ] **Step 3: 文档**

写 `docs/ops/feishu-assistant-mode.md`：

```markdown
# 飞书助手模式（v1.1）

## 概述

v1.1 引入双模式架构：
- **助手模式**（无 anchor）：通过命令操作（/new /list /select /budget /menu /help /status /cancel）
- **敕令模式**（有 anchor）：纯文本即续接当前敕令

## 模式切换

| 操作 | 触发模式切换 |
|------|------------|
| 飞书首次接入 | 助手模式（默认）|
| `/new <goal>` | 助手 → 敕令 |
| `/select <id>` | 助手 → 敕令 |
| `/exit` | 敕令 → 助手 |

## 命令清单

### 助手模式
- `/new <goal>` 新建敕令并进入敕令模式
- `/list [open|completed|all]` 列敕令
- `/select <id>` 切到指定敕令（id 前缀 ≥6 字符）
- `/budget` 成本概览
- `/menu` 主菜单卡片
- `/help` 帮助
- `/status <id>` 查敕令状态（需指定 id）
- `/cancel <id>` 取消敕令（需指定 id）

### 敕令模式
- 纯文本 = 续接当前敕令
- `/status` 查当前敕令状态
- `/cancel` 取消当前敕令
- `/exit` 退出敕令模式
- `/new <goal>` 自动 /exit + /new
- `/list /budget /menu /help` 查询类（不动 anchor）

## 助手 Persona 配置

通政司页面 → 飞书助手分卡 → 选 cabinet persona 兼任助手 → 保存。

LLM 意图增强：开启后纯文本（如"显示我的列表"）会通过 persona 的 LLM 解析为命令。

## 紧急逃生

如双模式有严重问题，临时回退到 v1 行为：

```bash
TIANSHU_FEISHU_DISABLE_ASSISTANT_MODE=1
```

## 故障排查

| 问题 | 排查 |
|------|------|
| 飞书发"你好"无响应 | 默认助手模式不自动新建。请用 `/new` 或 `/list` |
| `/select` 报"短 ID 多个匹配" | 用更长前缀（至少 6 字符；可从 `/list` 卡片复制完整 ID） |
| 自然语言不被识别 | 确认通政司「启用 LLM 意图增强」已开 + 助手 persona 有 llm_config_name |
| `/budget` 显示"暂时无法获取" | cost_manager 未正确接入或 cost_ledger 表查询失败 |
```

- [ ] **Step 4: commit 文档**

```bash
git add docs/ops/feishu-assistant-mode.md
git commit -m "docs(feishu): v1.1 助手模式 + 敕令模式用户指南"
```

---

## 完成检查清单

- [ ] 13 个 Step 全部完成并提交
- [ ] `pytest tests/gateway/feishu/ --cov=src/tianshu/gateway/feishu` 覆盖率 ≥ 80%
- [ ] `ruff check src/tianshu/gateway/feishu/ tests/gateway/feishu/` 无 lint 错
- [ ] 端到端验证：
  - 飞书首次发消息 → 助手模式 silent reply
  - `/menu` → 卡片下发
  - `/new` → 进敕令模式
  - 纯文本 → 续接
  - `/exit` → 回助手模式
  - `/select <id>` → 切换 anchor
  - `/budget` → 卡片显示成本
- [ ] 现有 v1 测试全部通过
- [ ] 文档：`docs/ops/feishu-assistant-mode.md` 完整可用
- [ ] 紧急逃生开关 `TIANSHU_FEISHU_DISABLE_ASSISTANT_MODE` 已实现并测试

---

## 风险与回退

如某 Step 阻塞或上线后发现严重问题：

```bash
# 临时回退到 v1 行为（保留代码 + 关闭新模式）
TIANSHU_FEISHU_DISABLE_ASSISTANT_MODE=1

# 完全回退（删除 v1.1 commits）
git revert <Step 1-13 commits>
```

---

## 与 Spec 的对应

| Spec § | Plan Step / Task |
|--------|------------------|
| §3 整体架构 | Step 3-9 各模块 |
| §4 状态机 | Step 3 ModeRouter |
| §5 数据模型 | Step 1 (delete_feishu_anchor) + Step 10.1-10.3 (settings) |
| §6 命令清单 | Step 4 (Assistant) + Step 5 (Edict) |
| §7 卡片 schema | Step 6-7 CardBuilder + Step 9 CardActionDispatcher |
| §8 LLM Fallback | Step 8 IntentParser |
| §9 PersonaRenderer | Step 2 |
| §10 通政司 web 扩展 | Step 10.3 + Step 12 |
| §11 行为差异 + 迁移 | Step 11 升级通告 + Step 10.5 紧急逃生 |
| §12 错误处理 | 各 Step 实现细节（/budget 失败兜底 / select 多匹配 / LLM 失败等）|
| §13 测试策略 | Step 13 (8 个 sub-task) |

---

## 与 v2 改进项目（不在本 Plan 范围）

- `_settings` 私有属性 reload 时直接修改的 hack → 给各模块加 `update_settings()` 公开方法
- v2 LLM 意图理解可加入更多 args（如 时间范围 / 排序）
- v2 命令树扩展：`/personas` `/audit` `/recent` 等部门视图
- v2 卡片树扩展：`/list` 卡片每行加多按钮（取消 / 查看详情）
- v2 多助手切换（不同 persona 提供不同 UX）
