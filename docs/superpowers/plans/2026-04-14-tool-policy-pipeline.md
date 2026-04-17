# Tool Policy Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 tianshu 现有 `HookRegistry` / `ApprovalManager` / `Auditor` 基础上，补齐事前 Tool Policy Pipeline（tier runtime、PolicyEngine、Session Rules、Policy Profile、Web UI），一次性解决 Spec `2026-04-14-tool-policy-pipeline-design.md` 中列出的缺口 A/B/C/D 与扩展 E。

**Architecture:** `PolicyHook` 作为 `BEFORE_TOOL_CALL` 的一个 priority=50 handler 注入 `HookRegistry`，内部分三层：Tier 快路径（T0 直接放行）→ `SessionRuleStore` 缓存查询 → `PolicyEngine`（5 条内建 frozen-dataclass 规则）。审批走现有 `ApprovalManager`，但其 `approval_required_tools` 入口判断下沉为一条 rule；Decree 扩展 `grant_scope` 字段，批准动作顺带写入 `SessionRuleStore`。任务级 `PolicyProfile` 在 `Executor.execute_edict` 启动前展开为 `scope="edict"` 的 session rules。所有决策通过 `storage.append_event` 写 `policy.*` 事件，前端新增 4 个展示点消费。

**Tech Stack:** Python 3.11+（frozen dataclasses, Protocol, asyncio）、pydantic v2（现有 Edict/Decree 模型扩展）、SQLite（现有 Storage 层）、FastAPI（现有 `gateway/api.py`）、React + TypeScript + Ant Design（现有 `web/` 前端）。

**Reference**：实现过程中涉及的语义与边界全部来自 `docs/superpowers/specs/2026-04-14-tool-policy-pipeline-design.md`。遇到歧义时以 spec 为准。

**Test Policy（重要）：**按项目 MEMORY 偏好 "功能优先，测试最后补"，本计划**不写单元/集成测试**。每个任务形态是 `写代码 → 手动 smoke test → commit`。手动验证场景对应 Spec Section 8 的 12 条清单，对应关系已在任务中标注。技术债清单见 Spec Section 8。

---

## 文件结构（实施前映射）

| 文件 | 动作 | 责任 |
|------|------|------|
| `src/tianshu/tools/types.py` | 改 | 新增 `ToolTier` IntEnum |
| `src/tianshu/tools/registry.py` | 改 | 新增 `get_definition` 访问器，execute() 头部 T0 快路径跳过 `_hooks` |
| `src/tianshu/tools/builtins.py` | 改 | 用 `ToolTier` 常量重写 tier 字段，`shell_exec` 从 T2 提到 T3 |
| `src/tianshu/tools/edit_file.py` | 改 | 用 `ToolTier.T1_WORKSPACE` |
| `src/tianshu/tools/list_dir.py` / `grep.py` / `find_files.py` | 改 | 用 `ToolTier.T0_READONLY` |
| `src/tianshu/tools/memory_tools.py` | 改 | 读工具 T0，写工具 T1 |
| `src/tianshu/tools/skill_tools.py` | 改 | `list_skills` T0，`invoke` T1（默认） |
| `src/tianshu/tools/policy.py` | 新建 | `ToolCallRecord` / `PolicyContext` / `PolicyDecision` / `PolicyRule` Protocol / `PolicyEngine` |
| `src/tianshu/tools/policy_rules/__init__.py` | 新建 | 导出所有内建规则 |
| `src/tianshu/tools/policy_rules/tier_escalation.py` | 新建 | `TierEscalationRule` |
| `src/tianshu/tools/policy_rules/workspace_boundary.py` | 新建 | `WorkspaceBoundaryRule` |
| `src/tianshu/tools/policy_rules/bash_safety.py` | 新建 | `BashSafetyRule`（含黑名单常量） |
| `src/tianshu/tools/policy_rules/approval_required_list.py` | 新建 | `ApprovalRequiredListRule` |
| `src/tianshu/tools/policy_rules/default_tier.py` | 新建 | `DefaultTierRule` |
| `src/tianshu/tools/policy_store.py` | 新建 | `SessionRule` dataclass + `SessionRuleStore` Protocol + `InMemorySessionRuleStore` + `SqliteSessionRuleStore` + `arg_fingerprint` 函数族 |
| `src/tianshu/tools/policy_profile.py` | 新建 | `PolicyProfile` frozen dataclass + `BUILTIN_TEMPLATES` 常量 + `expand_to_session_rules` 函数 |
| `src/tianshu/executor/policy_hook.py` | 新建 | `PolicyHook.on_before_tool_call` — `BEFORE_TOOL_CALL` handler |
| `src/tianshu/executor/agent.py` | 改 | BEFORE/AFTER_TOOL_CALL hook context 新增 `edict=edict, memorial=memorial`；T0 tier 快路径跳过 hook chain |
| `src/tianshu/executor/executor.py` | 改 | `execute_edict` 在 SESSION_START 前展开 `edict.runtime.policy_profile`，传 `memorial` 给 `agent.execute` |
| `src/tianshu/executor/approvals.py` | 改 | `on_before_tool_call` 移除 `approval_required_tools` 入口判断（保留 UI 交互层）；`_handle_approve` 根据 `decree.grant_scope` 写 session rule |
| `src/tianshu/models/decree.py` | 改 | 新增 `grant_scope` + `grant_reason` 字段 |
| `src/tianshu/models/edict.py` | 改 | `EdictRuntime` 新增 `policy_profile` 字段 |
| `src/tianshu/storage.py` | 改 | 新增 `session_rules` 表 DDL + CRUD 方法 |
| `src/tianshu/app.py` | 改 | 构造 `SessionRuleStore` / `PolicyEngine` / `PolicyHook`，注册到 `HookRegistry`，注入 `ApprovalManager` |
| `src/tianshu/gateway/api.py` | 改 | 新增 `# --- Policy endpoints ---` 段：`/policy_events`、`/session_rules`、`/policy/stats`、`/policy/templates` |
| `web/src/api/policy.ts` | 新建 | 前端 API 客户端 |
| `web/src/components/policy/PolicyTimeline.tsx` | 新建 | Edict 详情页面板 |
| `web/src/components/policy/PolicyProfilePanel.tsx` | 新建 | 创建 Edict 表单折叠面板 |
| `web/src/pages/SessionRulesPage.tsx` | 新建 | Session Rules 管理页 |
| `web/src/pages/EdictDetailPage.tsx` | 改 | 嵌入 `PolicyTimeline` |
| `web/src/pages/EdictCreatePage.tsx` | 改 | 嵌入 `PolicyProfilePanel` |
| `web/src/pages/AuditDashboardPage.tsx` | 改 | 新增 "Policy Decisions" Tab |
| `web/src/App.tsx` | 改 | 注册 `/session-rules` 路由 |
| `web/src/components/layout/*` | 改 | 菜单项加入 "Session Rules" |

---

## 实施顺序（强依赖链）

1. **Step 1 (Gap B, 前置: memorial 线程穿透)**: Tier runtime + agent.py 给 hook 透传 edict/memorial。这是 Step 2 的前置条件 — 没有 memorial 在 hook context 里，`ApprovalManager` 和 `PolicyHook` 都拿不到审批锚点。
2. **Step 2 (Gap A)**: PolicyEngine + 5 内建规则 + `PolicyHook` 注入 HookRegistry。`ApprovalManager` 入口判断收敛到规则 4 (`ApprovalRequiredListRule`)。
3. **Step 3 (Gap C)**: `SessionRuleStore` + `session_rules` 表 + `Decree.grant_scope` 扩展 + `ApprovalManager._handle_approve` 写 rule + `PolicyEngine` 集成查询。
4. **Step 4 (Ext E)**: `PolicyProfile` 数据类 + `EdictRuntime.policy_profile` 字段 + `Executor` 展开逻辑 + 3 硬编码模板 + 规则读 profile。
5. **Step 5 (Gap D)**: 后端 API 路由 + 前端 4 个展示点。

每个 Step 结束都写 commit，Step 内部每个 Task 写一次独立 commit（多任务时按子任务粒度 commit）。

---

## Step 1: Tier Runtime 生效 + Hook Context 修正（Gap B）

### Task 1.1: 新增 `ToolTier` IntEnum

**Files:**
- Modify: `src/tianshu/tools/types.py`

- [ ] **Step 1: 在 `types.py` 顶部追加 `ToolTier` 定义**

在 `from __future__ import annotations` 下方新增 `from enum import IntEnum`，文件末尾追加：

```python
class ToolTier(IntEnum):
    """工具权限 tier，数值越大越危险。

    与 PolicyEngine 协作：T0 直接快路径放行，T1+ 进入 hook chain
    由 PolicyEngine 决策。spec: Section 2。
    """

    T0_READONLY = 0          # 只读 / 无副作用
    T1_WORKSPACE = 1         # workspace 内写
    T2_WRITE = 2             # 外部写 / 可逆副作用
    T3_DANGEROUS = 3         # 危险 / 不可逆
```

- [ ] **Step 2: 手动 smoke — import 校验**

Run: `uv run python -c "from tianshu.tools.types import ToolTier; print(ToolTier.T0_READONLY.value, ToolTier.T3_DANGEROUS)"`
Expected: `0 ToolTier.T3_DANGEROUS`

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/types.py
git commit -m "feat(tools): add ToolTier IntEnum for runtime permission tiers"
```

---

### Task 1.2: ToolRegistry 支持 tier 访问 + T0 快路径

**Files:**
- Modify: `src/tianshu/tools/registry.py`

- [ ] **Step 1: 在 `ToolRegistry` 上增加 `get_definition` 访问器**

在 `list_definitions` 方法后（约 58 行后），追加：

```python
    def get_definition(self, name: str) -> ToolDefinition | None:
        """返回 name 对应的 ToolDefinition，未注册时返回 None。"""
        entry = self._tools.get(name)
        return entry[0] if entry else None
```

- [ ] **Step 2: 在 `execute` 方法中加 T0 快路径**

找到 `execute` 方法中 `jsonschema.validate(...)` 之后、`# Before hooks` 之前的位置，插入：

```python
        # Spec Section 2: T0_READONLY 工具走快路径 — 跳过 _hooks 链
        # 仍然 validate schema + 日志，但不经过 ToolHook 的 before/after 回调。
        from tianshu.tools.types import ToolTier

        if defn.tier == ToolTier.T0_READONLY:
            logger.debug("[TOOL] fast-path T0: name=%s", name)
            try:
                return await func(**args)
            except Exception as e:
                logger.exception("Tool '%s' raised in fast path", name)
                return error_result(f"Error executing {name}: {e}")
```

> 注意：ToolTier 是 IntEnum，`defn.tier == ToolTier.T0_READONLY` 对 int 字段也能正确比较。

- [ ] **Step 3: 在 `execute` 方法顶部（`if name not in self._tools` 之前）加"缺 tier 降级为 T3"防御**

找到 `defn, func = self._tools[name]` 下一行，插入：

```python
        # Spec Section 2: 未声明 tier 的工具 runtime 视为 T3_DANGEROUS
        from tianshu.tools.types import ToolTier

        if defn.tier is None or defn.tier not in ToolTier.__members__.values() and defn.tier not in (0, 1, 2, 3):
            logger.error(
                "[TOOL] %s has invalid tier=%r, downgrading to T3_DANGEROUS",
                name, defn.tier,
            )
            # 动态覆盖这一次调用的 tier（不改 registry 里的定义，避免副作用）
            defn = defn.model_copy(update={"tier": ToolTier.T3_DANGEROUS.value})
```

> 说明：`ToolDefinition` 是 pydantic BaseModel，用 `model_copy(update=...)` 生成新副本。仅影响本次 `execute`，不污染注册表。

- [ ] **Step 4: 手动 smoke — T0 快路径**

1. 启动服务 `uv run uvicorn tianshu.app:app --reload`
2. 提交一个 edict：`POST /edicts goal="列出当前目录"`，触发 `list_dir` 工具
3. 查日志，确认有 `[TOOL] fast-path T0: name=list_dir`
4. 查 events 表 `SELECT * FROM events WHERE type LIKE 'hook.%' AND ...`，确认 `list_dir` 没有产生 `hook.before_tool_call` 事件（因为快路径跳过了 HookRegistry）。

> 对应 Spec Section 8 手动验证清单第 1 条。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/tools/registry.py
git commit -m "feat(tools): add T0 fast path and tier validation in ToolRegistry"
```

---

### Task 1.3: 修正内建工具 tier 声明

**Files:**
- Modify: `src/tianshu/tools/builtins.py`
- Modify: `src/tianshu/tools/edit_file.py`
- Modify: `src/tianshu/tools/list_dir.py`
- Modify: `src/tianshu/tools/grep.py`
- Modify: `src/tianshu/tools/find_files.py`
- Modify: `src/tianshu/tools/memory_tools.py`
- Modify: `src/tianshu/tools/skill_tools.py`

- [ ] **Step 1: `builtins.py` — shell_exec 提到 T3**

将 `builtins.py` 顶部 import 行追加 `from tianshu.tools.types import ToolTier`（如果尚未导入 types）。

找到 `shell_exec` 的 `ToolDefinition(...)` 块中 `tier=2,` 的行，改为：

```python
            tier=ToolTier.T3_DANGEROUS.value,
```

找到 `read_file` 的 `tier=0,` 行，改为：

```python
            tier=ToolTier.T0_READONLY.value,
```

找到 `write_file` 的 `tier=1,` 行，改为：

```python
            tier=ToolTier.T1_WORKSPACE.value,
```

- [ ] **Step 2: 其他工具用常量替换数字**

- `edit_file.py`：`tier=1,` → `tier=ToolTier.T1_WORKSPACE.value,`（加 import）
- `list_dir.py`：`tier=0,` → `tier=ToolTier.T0_READONLY.value,`
- `grep.py`：`tier=0,` → `tier=ToolTier.T0_READONLY.value,`
- `find_files.py`：`tier=0,` → `tier=ToolTier.T0_READONLY.value,`

每个文件都需要新增 `from tianshu.tools.types import ToolTier` 导入。

- [ ] **Step 3: `memory_tools.py` 和 `skill_tools.py` — 逐个 tool 分类**

打开 `memory_tools.py`，对每个 `registry.register(...)` 调用的 `ToolDefinition`：
- 纯读操作（`memory_search`、`memory_get` 等）→ `tier=ToolTier.T0_READONLY.value`
- 写操作（`memory_set`、`memory_delete` 等）→ `tier=ToolTier.T1_WORKSPACE.value`

打开 `skill_tools.py`：
- `list_skills` → `tier=ToolTier.T0_READONLY.value`
- `invoke_skill`（或 `skill_invoke`）→ `tier=ToolTier.T1_WORKSPACE.value`（初版默认，SKILL.md 解析留给 Phase 2）

两个文件都加 `from tianshu.tools.types import ToolTier` 导入。

> 注：如果工具名称不同，按实际代码调整。关键原则：读 = T0，写 = T1，执行外部命令 = T3。

- [ ] **Step 4: 手动 smoke — tier 生效**

1. `uv run python -c "from tianshu.app import *; from tianshu.tools.registry import ToolRegistry; from tianshu.tools.builtins import register_builtins; r = ToolRegistry(); register_builtins(r, '/tmp'); [print(d.name, d.tier) for d in r.list_definitions()]"`
2. 确认 `shell_exec 3`, `read_file 0`, `list_dir 0`, `grep 0`, `find_files 0`, `edit_file 1`, `write_file 1`。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/tools/builtins.py src/tianshu/tools/edit_file.py src/tianshu/tools/list_dir.py src/tianshu/tools/grep.py src/tianshu/tools/find_files.py src/tianshu/tools/memory_tools.py src/tianshu/tools/skill_tools.py
git commit -m "feat(tools): promote shell_exec to T3 and migrate all builtins to ToolTier enum"
```

---

### Task 1.4: 修复 BEFORE_TOOL_CALL hook 缺失 edict/memorial context 的 bug

**Problem:** 当前 `executor/agent.py:350` 调用 `self._hooks.run(HookType.BEFORE_TOOL_CALL, tool_name=..., tool_args=..., iteration=...)`，**未传** `edict` 和 `memorial`，导致 `ApprovalManager.on_before_tool_call` 在 `if not tool_name or not edict: return None` 就早退，从未真正触发过。必须先修。

**Files:**
- Modify: `src/tianshu/executor/executor.py`
- Modify: `src/tianshu/executor/agent.py`

- [ ] **Step 1: Executor 传 memorial 给 agent.execute**

打开 `executor/executor.py`，找到 `execute_edict` 方法中约 229 行的 `self._agent.execute(edict, ..., persona=persona,)` 调用，在参数列表新增 `memorial=memorial,`：

```python
            result = await asyncio.wait_for(
                self._agent.execute(
                    edict,
                    memorial=memorial,          # NEW
                    on_event=on_event,
                    history=history,
                    user_content=user_content,
                    persona=persona,
                ),
                timeout=timeout,
            )
```

- [ ] **Step 2: Agent.execute 签名增加 `memorial` 参数**

打开 `executor/agent.py`，找到 `async def execute(` 方法签名（约 74 行），在 `edict: Edict,` 后插入：

```python
    async def execute(
        self,
        edict: Edict,
        memorial: "Memorial | None" = None,      # NEW
        on_event: Callable[[dict], None] | None = None,
        history: list[dict] | None = None,
        user_content: str | None = None,
        tool_filter: list[str] | None = None,
        persona: object | None = None,
        stream_callback: object | None = None,
        cancellation_token: object | None = None,
    ) -> AgentResult:
```

如果文件顶部没有 `Memorial` 导入，在 `from tianshu.models.edict import Edict` 附近添加：

```python
from tianshu.models.memorial import Memorial
```

然后把签名里的字符串注解改成 `memorial: Memorial | None = None`。

- [ ] **Step 3: 把 edict + memorial 塞进 BEFORE/AFTER_TOOL_CALL hook context，并加 T0 快路径短路**

找到约 349 行的 `HookType.BEFORE_TOOL_CALL` 调用，整体替换为：

```python
                for tc in response.tool_calls:
                    # Tier 快路径：T0_READONLY 直接绕过 HookRegistry
                    # 复用 ToolRegistry.get_definition，保持单一数据源。
                    from tianshu.tools.types import ToolTier

                    tool_defn = self._tools.get_definition(tc["name"])
                    tool_tier = tool_defn.tier if tool_defn else ToolTier.T3_DANGEROUS.value
                    is_fast_path = tool_tier == ToolTier.T0_READONLY.value

                    if self._hooks and not is_fast_path:
                        hook_result = await self._hooks.run(
                            HookType.BEFORE_TOOL_CALL,
                            tool_name=tc["name"],
                            tool_args=tc["args"],
                            iteration=state.iteration,
                            edict=edict,
                            memorial=memorial,
                        )
                        if hook_result.block:
                            new_messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": f"Tool blocked: {hook_result.reason}",
                            })
                            _emit({
                                "type": "tool.blocked",
                                "tool": tc["name"],
                                "iteration": state.iteration,
                                "reason": hook_result.reason,
                            })
                            continue
```

- [ ] **Step 4: AFTER_TOOL_CALL 同样透传 edict/memorial 并短路 T0**

找到约 393 行：

```python
                    if self._hooks and not is_fast_path:
                        await self._hooks.run(
                            HookType.AFTER_TOOL_CALL,
                            tool_name=tc["name"],
                            tool_args=tc["args"],
                            tool_result=tool_result,
                            iteration=state.iteration,
                            edict=edict,
                            memorial=memorial,
                        )
```

> 设计决定（Spec Section 2）：T0 在 registry.execute() 有自己的快路径；这里 agent 层再 short-circuit 一次，避免 T0 工具产生无意义的 `hook.before_tool_call` 事件噪声。两处 short-circuit 形成 defense in depth。

- [ ] **Step 5: 手动 smoke — ApprovalManager 真正触发**

1. 提交一个带 `runtime.approval_required_tools=["shell_exec"]` 的 edict，goal = "跑 echo hello"
2. 观察前端或 events 表，预期出现 `tool.approval_required` 事件（以前是没出现的 — 证明 bug 已修）
3. 手动 POST `/decrees` 批准，工具执行完成

> 对应 Spec Section 8 清单第 4 条（部分）。

- [ ] **Step 6: Commit**

```bash
git add src/tianshu/executor/executor.py src/tianshu/executor/agent.py
git commit -m "fix(executor): thread edict+memorial into BEFORE/AFTER_TOOL_CALL hooks"
```

---

## Step 2: PolicyEngine + 5 内建规则 + PolicyHook（Gap A）

### Task 2.1: 新建 `tools/policy.py` — 数据模型与 Engine

**Files:**
- Create: `src/tianshu/tools/policy.py`

- [ ] **Step 1: 写文件**

```python
"""Policy Engine — 事前权限决策核心。

Spec: docs/superpowers/specs/2026-04-14-tool-policy-pipeline-design.md (Section 3)

设计原则：
- 所有数据模型都是 frozen dataclass（immutable → 测试与并发友好）
- PolicyRule 是 Protocol → 便于 fake rule 注入
- fail-open 单条规则 + fail-secure 整体引擎
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from tianshu.tools.types import ToolTier

if TYPE_CHECKING:
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial

logger = logging.getLogger(__name__)

POLICY_RULE_TIMEOUT = 1.0      # 单条规则 1s
POLICY_ENGINE_TIMEOUT = 3.0    # 引擎整体 3s


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的历史记录（未来组合策略用，初版仅预留）。"""

    tool_name: str
    args_summary: dict[str, Any]
    verdict: str
    iteration: int
    timestamp: datetime


@dataclass(frozen=True)
class PolicyContext:
    """一次工具调用的完整决策上下文。"""

    tool_name: str
    tool_tier: ToolTier
    args: dict[str, Any]
    edict: "Edict"
    memorial: "Memorial | None"
    workspace_root: Path
    iteration: int
    recent_calls: tuple[ToolCallRecord, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    """策略决策结果 — verdict + rule_id + reason 是决策可追溯的最小单元。"""

    verdict: Literal["allow", "deny", "require_approval"]
    rule_id: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class PolicyRule(Protocol):
    rule_id: str
    priority: int  # 高优先级先跑

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        """返回 None 表示弃权（让下一条规则决定）。"""
        ...


class PolicyEngine:
    """按 priority 执行 rules，遇 deny/require_approval 短路。

    全局可观测 + fail-secure 引擎：
    - 单规则抛异常 / 超时 → 弃权 + WARNING
    - 引擎整体超时 / 未知异常 → deny + ERROR（对应 policy_deny_due_to_engine_error_total 指标）
    - 所有规则弃权 → allow（默认）
    """

    def __init__(self, rules: list[PolicyRule]) -> None:
        # priority 高的先跑 → 降序排序
        self._rules: tuple[PolicyRule, ...] = tuple(
            sorted(rules, key=lambda r: r.priority, reverse=True)
        )

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        try:
            return await asyncio.wait_for(
                self._evaluate_inner(ctx),
                timeout=POLICY_ENGINE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(
                "PolicyEngine timeout (>%.1fs) for tool=%s — fail-secure deny",
                POLICY_ENGINE_TIMEOUT, ctx.tool_name,
            )
            return PolicyDecision(
                verdict="deny",
                rule_id="engine_timeout",
                reason=f"PolicyEngine exceeded {POLICY_ENGINE_TIMEOUT}s timeout",
            )
        except Exception as e:
            logger.exception(
                "PolicyEngine unexpected error for tool=%s — fail-secure deny",
                ctx.tool_name,
            )
            return PolicyDecision(
                verdict="deny",
                rule_id="engine_error",
                reason=f"PolicyEngine failed: {type(e).__name__}: {e}",
            )

    async def _evaluate_inner(self, ctx: PolicyContext) -> PolicyDecision:
        last_allow: PolicyDecision | None = None
        for rule in self._rules:
            rule_start = time.monotonic()
            try:
                decision = await asyncio.wait_for(
                    rule.evaluate(ctx),
                    timeout=POLICY_RULE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Rule %s timed out (>%.1fs) — abstain",
                    rule.rule_id, POLICY_RULE_TIMEOUT,
                )
                continue
            except Exception:
                logger.exception(
                    "Rule %s raised — abstain", rule.rule_id,
                )
                continue

            elapsed = time.monotonic() - rule_start
            logger.debug(
                "[POLICY] rule=%s verdict=%s elapsed=%.3fs",
                rule.rule_id,
                decision.verdict if decision else "abstain",
                elapsed,
            )

            if decision is None:
                continue  # 弃权
            if decision.verdict in ("deny", "require_approval"):
                return decision  # 短路
            if decision.verdict == "allow":
                last_allow = decision  # 不短路，允许后续规则覆盖

        # 所有规则弃权 or 仅有 allow → 用最后的 allow 或默认
        if last_allow is not None:
            return last_allow
        return PolicyDecision(
            verdict="allow",
            rule_id="default",
            reason="all rules abstained",
        )
```

- [ ] **Step 2: 手动 smoke — import**

Run: `uv run python -c "from tianshu.tools.policy import PolicyEngine, PolicyContext, PolicyDecision, ToolCallRecord; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/policy.py
git commit -m "feat(tools): add PolicyEngine with rule pipeline and fail-secure timeouts"
```

---

### Task 2.2: 5 条内建规则 —— `tools/policy_rules/`

**Files:**
- Create: `src/tianshu/tools/policy_rules/__init__.py`
- Create: `src/tianshu/tools/policy_rules/tier_escalation.py`
- Create: `src/tianshu/tools/policy_rules/workspace_boundary.py`
- Create: `src/tianshu/tools/policy_rules/bash_safety.py`
- Create: `src/tianshu/tools/policy_rules/approval_required_list.py`
- Create: `src/tianshu/tools/policy_rules/default_tier.py`

- [ ] **Step 1: `__init__.py`**

```python
"""Built-in policy rules. Spec Section 3."""

from tianshu.tools.policy_rules.approval_required_list import ApprovalRequiredListRule
from tianshu.tools.policy_rules.bash_safety import BashSafetyRule
from tianshu.tools.policy_rules.default_tier import DefaultTierRule
from tianshu.tools.policy_rules.tier_escalation import TierEscalationRule
from tianshu.tools.policy_rules.workspace_boundary import WorkspaceBoundaryRule

__all__ = [
    "TierEscalationRule",
    "WorkspaceBoundaryRule",
    "BashSafetyRule",
    "ApprovalRequiredListRule",
    "DefaultTierRule",
]


def build_default_rules() -> list:
    """返回 5 条内建规则的默认实例列表（按优先级顺序）。"""
    return [
        TierEscalationRule(),        # 100
        WorkspaceBoundaryRule(),     # 90
        BashSafetyRule(),            # 80
        ApprovalRequiredListRule(),  # 70
        DefaultTierRule(),           # 10
    ]
```

- [ ] **Step 2: `tier_escalation.py` — priority 100**

```python
"""TierEscalationRule — 任务级 tier 提升（只升不降）。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision
from tianshu.tools.types import ToolTier


@dataclass
class TierEscalationRule:
    rule_id: str = "tier_escalation"
    priority: int = 100

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        """读取 edict.runtime.tier_overrides，仅生效 tier > 原 tier 的提升。"""
        runtime = getattr(ctx.edict, "runtime", None)
        overrides = getattr(runtime, "tier_overrides", None) or {}
        if not overrides:
            return None

        override_val = overrides.get(ctx.tool_name)
        if override_val is None:
            return None

        try:
            override_tier = ToolTier(int(override_val))
        except (TypeError, ValueError):
            return None

        if override_tier <= ctx.tool_tier:
            return None  # 不生效（安全单向）

        # tier 提升后，直接要求审批（除非后续规则覆盖）
        return PolicyDecision(
            verdict="require_approval",
            rule_id=self.rule_id,
            reason=f"tier escalated from {ctx.tool_tier.name} to {override_tier.name} via edict.runtime.tier_overrides",
            metadata={"original_tier": ctx.tool_tier.name, "escalated_tier": override_tier.name},
        )
```

> 注：`EdictRuntime` 当前没有 `tier_overrides` 字段 — 这在 Step 4 添加。此处先写好读取代码，初版 `overrides` 会一直是空 dict。

- [ ] **Step 3: `workspace_boundary.py` — priority 90**

```python
"""WorkspaceBoundaryRule — 硬约束：越界路径直接 deny。

检查 path/cwd/file_path 类参数是否在 workspace_root 下。路径越界不走审批，
因为审批按钮本身就是诱导攻击面（攻击者可能社工用户点批准）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tianshu.tools.policy import PolicyContext, PolicyDecision

# 参数名白名单：这些字段被当作 path 处理
PATH_ARG_KEYS = ("path", "cwd", "file_path", "filename", "dir", "directory")


@dataclass
class WorkspaceBoundaryRule:
    rule_id: str = "workspace_boundary"
    priority: int = 90
    allowed_globs: tuple[str, ...] = field(default_factory=tuple)

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        workspace = ctx.workspace_root.resolve()
        extra_globs = self._resolve_profile_globs(ctx)

        for key in PATH_ARG_KEYS:
            if key not in ctx.args:
                continue
            raw = ctx.args[key]
            if not isinstance(raw, str) or not raw:
                continue

            resolved = self._resolve(raw, workspace)

            if self._is_inside(resolved, workspace):
                continue

            # 越界 — 再查 profile 白名单
            if any(resolved.match(glob) for glob in extra_globs):
                continue

            return PolicyDecision(
                verdict="deny",
                rule_id=self.rule_id,
                reason=f"path {raw!r} resolved to {resolved} is outside workspace {workspace}",
                metadata={"arg_key": key, "resolved": str(resolved)},
            )

        return None  # 所有 path 参数都在 workspace 内 → 弃权

    @staticmethod
    def _resolve(raw: str, workspace: Path) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = workspace / p
        try:
            return p.resolve()
        except OSError:
            return p  # 无法 resolve 时用原始 path 继续判断

    @staticmethod
    def _is_inside(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    @staticmethod
    def _resolve_profile_globs(ctx: PolicyContext) -> tuple[str, ...]:
        """读取 edict.runtime.policy_profile.allowed_paths（Step 4 填充）。"""
        runtime = getattr(ctx.edict, "runtime", None)
        profile = getattr(runtime, "policy_profile", None) if runtime else None
        if profile is None:
            return ()
        return tuple(getattr(profile, "allowed_paths", ()) or ())
```

- [ ] **Step 4: `bash_safety.py` — priority 80**

```python
"""BashSafetyRule — T3 shell 类工具的黑白名单。

黑名单永远生效（profile 也不能覆盖）。白名单来自 profile.allowed_bash_prefixes。
未命中白名单 → 进入默认审批流（返回 require_approval）。
"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision

BASH_TOOL_NAMES = {"shell_exec", "bash"}

# 黑名单 — 一旦子串匹配即 deny（大小写不敏感）
BASH_DENYLIST_SUBSTRINGS: tuple[str, ...] = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "dd of=/dev",
    ":(){:|:&};:",             # fork bomb
    "sudo ",
    "curl | sh",
    "curl|sh",
    "wget | sh",
    "wget|sh",
    "git push --force",
    "git push -f ",
    "chmod 777 /",
    "> /dev/sda",
)


@dataclass
class BashSafetyRule:
    rule_id: str = "bash_safety"
    priority: int = 80

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        if ctx.tool_name not in BASH_TOOL_NAMES:
            return None

        command = (ctx.args.get("command") or "").strip()
        if not command:
            return None

        lowered = command.lower()

        # 1. 黑名单（最高优先，即使 profile 白名单也不能覆盖）
        for needle in BASH_DENYLIST_SUBSTRINGS:
            if needle in lowered:
                return PolicyDecision(
                    verdict="deny",
                    rule_id=self.rule_id,
                    reason=f"command matched deny substring {needle!r}",
                    metadata={"matched_pattern": needle},
                )

        # 2. 白名单（来自 profile）
        allowed_prefixes = self._resolve_profile_prefixes(ctx)
        if allowed_prefixes and any(command.startswith(p) for p in allowed_prefixes):
            return PolicyDecision(
                verdict="allow",
                rule_id=self.rule_id,
                reason="command matched profile bash prefix allow-list",
                metadata={"matched_prefix": next(p for p in allowed_prefixes if command.startswith(p))},
            )

        # 3. 未命中 → T3 默认审批
        return PolicyDecision(
            verdict="require_approval",
            rule_id=self.rule_id,
            reason=f"bash command requires approval: {command[:80]}",
            metadata={"command_preview": command[:200]},
        )

    @staticmethod
    def _resolve_profile_prefixes(ctx: PolicyContext) -> tuple[str, ...]:
        runtime = getattr(ctx.edict, "runtime", None)
        profile = getattr(runtime, "policy_profile", None) if runtime else None
        if profile is None:
            return ()
        return tuple(getattr(profile, "allowed_bash_prefixes", ()) or ())
```

- [ ] **Step 5: `approval_required_list.py` — priority 70**

```python
"""ApprovalRequiredListRule — 兼容已有 edict.runtime.approval_required_tools。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision


@dataclass
class ApprovalRequiredListRule:
    rule_id: str = "approval_required_list"
    priority: int = 70

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        runtime = getattr(ctx.edict, "runtime", None)
        required = getattr(runtime, "approval_required_tools", None) or []
        if ctx.tool_name not in required:
            return None
        return PolicyDecision(
            verdict="require_approval",
            rule_id=self.rule_id,
            reason=f"tool listed in edict.runtime.approval_required_tools",
        )
```

- [ ] **Step 6: `default_tier.py` — priority 10**

```python
"""DefaultTierRule — 兜底按 tier 裁决。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision
from tianshu.tools.types import ToolTier


@dataclass
class DefaultTierRule:
    rule_id: str = "default_tier"
    priority: int = 10

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        # profile 的 auto_approve_max_tier 允许放行低 tier
        runtime = getattr(ctx.edict, "runtime", None)
        profile = getattr(runtime, "policy_profile", None) if runtime else None
        max_auto = None
        if profile is not None:
            raw_max = getattr(profile, "auto_approve_max_tier", None)
            if raw_max is not None:
                try:
                    max_auto = ToolTier(int(raw_max))
                except (TypeError, ValueError):
                    max_auto = None

        if max_auto is not None and ctx.tool_tier <= max_auto:
            return PolicyDecision(
                verdict="allow",
                rule_id=self.rule_id,
                reason=f"tier {ctx.tool_tier.name} <= profile.auto_approve_max_tier {max_auto.name}",
            )

        if ctx.tool_tier == ToolTier.T3_DANGEROUS:
            return PolicyDecision(
                verdict="require_approval",
                rule_id=self.rule_id,
                reason="T3_DANGEROUS tool requires approval by default",
            )
        if ctx.tool_tier == ToolTier.T2_WRITE:
            return PolicyDecision(
                verdict="require_approval",
                rule_id=self.rule_id,
                reason="T2_WRITE tool requires approval by default",
            )

        return PolicyDecision(
            verdict="allow",
            rule_id=self.rule_id,
            reason=f"tier {ctx.tool_tier.name} is safe by default",
        )
```

- [ ] **Step 7: 手动 smoke — import & construct**

Run:
```bash
uv run python -c "
from tianshu.tools.policy_rules import build_default_rules
rules = build_default_rules()
print([r.rule_id for r in rules])
"
```
Expected: `['tier_escalation', 'workspace_boundary', 'bash_safety', 'approval_required_list', 'default_tier']`

- [ ] **Step 8: Commit**

```bash
git add src/tianshu/tools/policy_rules/
git commit -m "feat(tools): add 5 built-in PolicyRule implementations"
```

---

### Task 2.3: PolicyHook handler（BEFORE_TOOL_CALL 注入）

**Files:**
- Create: `src/tianshu/executor/policy_hook.py`

- [ ] **Step 1: 写 `policy_hook.py`**

```python
"""PolicyHook — BEFORE_TOOL_CALL 的 priority=50 handler，内部委托 PolicyEngine。

设计要点：
- 不替换 ApprovalManager 的 handler（priority=10），两者共存于同一 hook 链。
- PolicyHook 先跑（priority 10 → 先跑；priority 50 → 后跑）... 等等，HookRegistry 是 priority 升序 sort 后按 list 顺序跑，所以 priority=10 先于 priority=50。
  我们希望 PolicyHook 先跑（pre-filter），ApprovalManager 的残留 UI 代码后跑（实际 Step 2.4 会把它砍成无操作）—— 所以 PolicyHook 用 priority=5，让它先触发。
- 决策事件通过 storage.append_event("policy.decision", ...) 写入。
"""

from __future__ import annotations

import logging
from pathlib import Path

from tianshu.executor.hooks import HookResult
from tianshu.tools.policy import PolicyContext, PolicyDecision, PolicyEngine
from tianshu.tools.types import ToolTier

logger = logging.getLogger(__name__)


class PolicyHook:
    """内嵌 PolicyEngine + SessionRuleStore 查询的 BEFORE_TOOL_CALL handler。"""

    def __init__(
        self,
        engine: PolicyEngine,
        workspace_root: Path,
        storage: object,
        tool_registry: object,
        session_rule_store: object | None = None,
        approval_manager: object | None = None,
    ) -> None:
        self._engine = engine
        self._workspace_root = workspace_root.resolve()
        self._storage = storage
        self._tool_registry = tool_registry
        self._session_rule_store = session_rule_store
        self._approval_manager = approval_manager

    async def on_before_tool_call(self, **context: object) -> HookResult | None:
        tool_name = context.get("tool_name")
        tool_args = context.get("tool_args") or {}
        edict = context.get("edict")
        memorial = context.get("memorial")
        iteration = context.get("iteration") or 0

        if not tool_name or not edict:
            return None  # 没上下文就放行，交给别的 handler

        # 解析 tool tier（fail-secure → 缺失 = T3）
        defn = self._tool_registry.get_definition(tool_name) if self._tool_registry else None
        tier_val = defn.tier if defn else ToolTier.T3_DANGEROUS.value
        try:
            tool_tier = ToolTier(int(tier_val))
        except (TypeError, ValueError):
            tool_tier = ToolTier.T3_DANGEROUS

        ctx = PolicyContext(
            tool_name=str(tool_name),
            tool_tier=tool_tier,
            args=dict(tool_args) if isinstance(tool_args, dict) else {},
            edict=edict,  # type: ignore[arg-type]
            memorial=memorial,  # type: ignore[arg-type]
            workspace_root=self._workspace_root,
            iteration=int(iteration),
        )

        decision = await self._engine.evaluate(ctx)

        # Session rule cache — 仅对 require_approval 查询（Step 3 启用）
        if decision.verdict == "require_approval" and self._session_rule_store is not None:
            rule = await self._session_rule_store.find_match(
                tool_name=ctx.tool_name,
                args=ctx.args,
                edict_id=getattr(edict, "id", None),
            )
            if rule is not None:
                decision = PolicyDecision(
                    verdict="allow",
                    rule_id=f"session_rule:{rule.rule_id}",
                    reason=f"matched session rule (scope={rule.scope}, source={rule.source})",
                    metadata={"session_rule_id": rule.rule_id, "scope": rule.scope, "source": rule.source},
                )
                self._emit_event(ctx, "policy.session_rule_matched", decision)

        self._emit_event(ctx, "policy.decision", decision)

        if decision.verdict == "allow":
            return None
        if decision.verdict == "deny":
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] {decision.reason}",
            )
        if decision.verdict == "require_approval":
            return await self._request_approval(ctx, decision)
        return None

    async def _request_approval(
        self, ctx: PolicyContext, decision: PolicyDecision,
    ) -> HookResult | None:
        """走已有 ApprovalManager UI 流 —— 写 tool.approval_required 事件并 wait。"""
        if self._approval_manager is None:
            logger.error("policy_hook: require_approval but no ApprovalManager configured")
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] approval required but no approval manager",
            )

        memorial_id = getattr(ctx.memorial, "id", None) if ctx.memorial else None
        if not memorial_id:
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] approval required but no memorial context",
            )

        # 写事件，触发前端 toast
        try:
            self._storage.append_event(  # type: ignore[attr-defined]
                getattr(ctx.edict, "id", ""),
                memorial_id,
                "tool.approval_required",
                {
                    "tool_name": ctx.tool_name,
                    "rule_id": decision.rule_id,
                    "reason": decision.reason,
                    "tool_tier": ctx.tool_tier.name,
                    "args_summary": self._summarize_args(ctx.args),
                },
            )
        except Exception:
            logger.exception("policy_hook: failed to append tool.approval_required event")

        decree = await self._approval_manager.wait_for_approval(memorial_id, ctx.tool_name)  # type: ignore[attr-defined]
        if decree is None:
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] approval timed out or rejected",
            )
        if decree.action == "approve":
            return None  # 放行
        return HookResult(
            block=True,
            reason=f"[{decision.rule_id}] rejected by decree: {decree.comment or 'no reason'}",
        )

    def _emit_event(
        self,
        ctx: PolicyContext,
        event_type: str,
        decision: PolicyDecision,
    ) -> None:
        edict_id = getattr(ctx.edict, "id", None)
        memorial_id = getattr(ctx.memorial, "id", None) if ctx.memorial else None
        if not edict_id:
            return
        try:
            self._storage.append_event(  # type: ignore[attr-defined]
                edict_id,
                memorial_id,
                event_type,
                {
                    "tool_name": ctx.tool_name,
                    "tool_tier": ctx.tool_tier.name,
                    "verdict": decision.verdict,
                    "rule_id": decision.rule_id,
                    "reason": decision.reason,
                    "iteration": ctx.iteration,
                    "args_summary": self._summarize_args(ctx.args),
                    "metadata": decision.metadata,
                },
            )
        except Exception:
            logger.exception("policy_hook: failed to write %s event", event_type)

    @staticmethod
    def _summarize_args(args: dict) -> dict:
        """每字段截断到 200 字符，避免事件 payload 过大。"""
        out: dict = {}
        for k, v in args.items():
            if isinstance(v, str):
                out[k] = v if len(v) <= 200 else v[:200] + "...[truncated]"
            else:
                out[k] = v
        return out
```

- [ ] **Step 2: 手动 smoke — import**

Run: `uv run python -c "from tianshu.executor.policy_hook import PolicyHook; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/executor/policy_hook.py
git commit -m "feat(executor): add PolicyHook delegating to PolicyEngine"
```

---

### Task 2.4: ApprovalManager 收敛 — 移除入口判断

**Files:**
- Modify: `src/tianshu/executor/approvals.py`

- [ ] **Step 1: 砍掉 `on_before_tool_call` 的 `approval_required_tools` 判断**

`on_before_tool_call` 的全部入口判断（approval_required_tools / emit tool.approval_required / wait_for_approval）现在由 `PolicyHook` 负责。ApprovalManager 只负责 decree 状态机 + `submit_decree` 唤醒 `_pending` 事件。

用 Edit 工具整段替换方法。把 `on_before_tool_call` 缩为空操作：

```python
    async def on_before_tool_call(self, **context: object) -> object:
        """Deprecated pre-Step-2 entry point.

        入口判断已迁移到 PolicyHook（Spec Section 3 规则 4 ApprovalRequiredListRule）。
        保留方法签名以兼容已有 HookRegistry 注册，但直接返回 None 放行。

        实时审批的 wait_for_approval / submit_decree 仍然由本类提供，
        由 PolicyHook 在 require_approval 分支里直接调用。
        """
        return None
```

> 不要 `unregister`，也不要改 app.py 的 `hook_registry.register(HookType.BEFORE_TOOL_CALL, approval_manager.on_before_tool_call, priority=10)` — 这个 handler 现在是 no-op，占位不碍事。Step 2.5 会在它前面（更高 priority）注册 `PolicyHook`。

- [ ] **Step 2: 手动 smoke — 构造**

Run: `uv run python -c "from tianshu.executor.approvals import ApprovalManager; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/executor/approvals.py
git commit -m "refactor(executor): collapse ApprovalManager entry check into PolicyHook"
```

---

### Task 2.5: 在 `app.py` 装配 PolicyEngine + PolicyHook

**Files:**
- Modify: `src/tianshu/app.py`

- [ ] **Step 1: 追加 import**

文件顶部（约 44 行附近）添加：

```python
from tianshu.tools.policy import PolicyEngine
from tianshu.tools.policy_rules import build_default_rules
from tianshu.executor.policy_hook import PolicyHook
```

- [ ] **Step 2: 在 ApprovalManager 构造后立即构造 PolicyEngine + PolicyHook**

找到约 224 行 `app.state.approval_manager = approval_manager` 后面，插入：

```python
    # --- PolicyEngine + PolicyHook ---
    policy_engine = PolicyEngine(rules=build_default_rules())
    app.state.policy_engine = policy_engine

    policy_hook = PolicyHook(
        engine=policy_engine,
        workspace_root=Path(settings.workspace_dir).resolve(),
        storage=storage,
        tool_registry=tools,
        session_rule_store=None,   # Step 3 填充
        approval_manager=approval_manager,
    )
    app.state.policy_hook = policy_hook
    hook_registry.register(
        HookType.BEFORE_TOOL_CALL,
        policy_hook.on_before_tool_call,
        priority=5,  # 先于 approval_manager.on_before_tool_call(priority=10) 执行
    )
```

> `ApprovalManager.on_before_tool_call` 现在是 no-op，即使后跑也不会产生副作用；但优先级 5 < 10 让 `PolicyHook` 先跑，语义更清晰。

- [ ] **Step 3: 手动 smoke — server 起来**

1. `uv run uvicorn tianshu.app:app --reload`，观察 startup 无错误
2. 提交一个 edict goal="列出当前目录"，只用 T0 `list_dir`，观察不产生 `policy.decision` 事件（快路径）
3. 提交一个 edict goal="编辑 README.md"，触发 T1 `edit_file(path='README.md')`，观察产生 `policy.decision` 事件 verdict=allow rule_id=default_tier
4. 提交一个 edict goal="编辑 /etc/passwd"，强制 agent 调 `edit_file(path='/etc/passwd')`（可用 amend/retry 引导），观察产生 `policy.decision` verdict=deny rule_id=workspace_boundary，任务 fail

> 对应 Spec Section 8 清单第 2、3 条。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/app.py
git commit -m "feat(app): wire PolicyEngine and PolicyHook into HookRegistry"
```

---

## Step 3: Session Rules（Gap C）

### Task 3.1: `SessionRule` 数据模型 + `SessionRuleStore` 协议 + InMemory 实现

**Files:**
- Create: `src/tianshu/tools/policy_store.py`

- [ ] **Step 1: 写文件**

```python
"""SessionRuleStore — 信任缓存层（edict / always scope）。

Spec Section 4。两种实现：
- InMemorySessionRuleStore：edict scope（进程内）
- SqliteSessionRuleStore：always scope（持久化）— Task 3.2 实现
组合使用：CompositeSessionRuleStore 先查 in-memory，再查 sqlite。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRule:
    rule_id: str
    tool_name: str
    arg_fingerprint: str
    scope: Literal["edict", "always"]
    edict_id: str | None
    granted_at: datetime
    granted_by_decree_id: str | None
    source: Literal["approval", "profile", "manual"]
    reason: str
    expires_at: datetime | None


# ---------- arg_fingerprint 函数族 ----------


def fingerprint_edit_file(args: dict) -> str:
    """edit_file 指纹 = dirname(path)。覆盖同目录下所有编辑。"""
    import os
    path = args.get("path") or args.get("file_path") or ""
    return f"dir:{os.path.dirname(path) or '.'}"


def fingerprint_bash(args: dict) -> str:
    """bash 指纹 = 命令前两个 token。例如 'git push origin main' → 'git push'。"""
    cmd = (args.get("command") or "").strip()
    tokens = cmd.split()[:2]
    return "bash:" + " ".join(tokens)


def fingerprint_memory(args: dict) -> str:
    """memory_tools 指纹 = sorted filtered arg keys。"""
    keys = sorted(k for k in args.keys() if k not in {"value", "content"})
    return "memory:" + ",".join(keys)


def fingerprint_default(args: dict) -> str:
    """默认：args 稳定 JSON → sha1，用于严格相等匹配。"""
    try:
        canonical = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        canonical = repr(sorted(args.items()))
    return "hash:" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


_FINGERPRINT_FUNCS: dict[str, "callable"] = {
    "edit_file": fingerprint_edit_file,
    "write_file": fingerprint_edit_file,
    "shell_exec": fingerprint_bash,
    "bash": fingerprint_bash,
}


def compute_fingerprint(tool_name: str, args: dict) -> str:
    """根据 tool_name 选用对应算法。memory_tools.* 前缀走 memory 算法。"""
    if tool_name in _FINGERPRINT_FUNCS:
        return _FINGERPRINT_FUNCS[tool_name](args)
    if tool_name.startswith("memory_"):
        return fingerprint_memory(args)
    return fingerprint_default(args)


# ---------- Protocol ----------


class SessionRuleStore(Protocol):
    async def create(self, rule: SessionRule) -> None: ...

    async def find_match(
        self, tool_name: str, args: dict, edict_id: str | None,
    ) -> SessionRule | None: ...

    async def list_by_scope(
        self, scope: str, edict_id: str | None = None,
    ) -> list[SessionRule]: ...

    async def revoke(self, rule_id: str) -> None: ...

    async def clear_edict(self, edict_id: str) -> None: ...


# ---------- In-memory 实现 ----------


@dataclass
class InMemorySessionRuleStore:
    """edict scope 的规则 — 进程内 dict。"""

    _rules: dict[str, SessionRule] = field(default_factory=dict)

    async def create(self, rule: SessionRule) -> None:
        self._rules[rule.rule_id] = rule

    async def find_match(
        self, tool_name: str, args: dict, edict_id: str | None,
    ) -> SessionRule | None:
        fp = compute_fingerprint(tool_name, args)
        now = datetime.now(UTC)
        for rule in self._rules.values():
            if rule.tool_name != tool_name:
                continue
            if rule.arg_fingerprint != fp:
                continue
            if rule.scope == "edict" and rule.edict_id != edict_id:
                continue
            if rule.expires_at and rule.expires_at < now:
                continue
            return rule
        return None

    async def list_by_scope(
        self, scope: str, edict_id: str | None = None,
    ) -> list[SessionRule]:
        out = []
        for rule in self._rules.values():
            if rule.scope != scope:
                continue
            if edict_id is not None and rule.edict_id != edict_id:
                continue
            out.append(rule)
        return out

    async def revoke(self, rule_id: str) -> None:
        self._rules.pop(rule_id, None)

    async def clear_edict(self, edict_id: str) -> None:
        self._rules = {
            rid: r for rid, r in self._rules.items() if r.edict_id != edict_id
        }


# ---------- Composite（InMemory + Sqlite） ----------


@dataclass
class CompositeSessionRuleStore:
    """组合存储：edict scope 走 InMemory，always scope 走 Sqlite。"""

    in_memory: InMemorySessionRuleStore
    sqlite: "SqliteSessionRuleStore"

    async def create(self, rule: SessionRule) -> None:
        if rule.scope == "always":
            await self.sqlite.create(rule)
        else:
            await self.in_memory.create(rule)

    async def find_match(
        self, tool_name: str, args: dict, edict_id: str | None,
    ) -> SessionRule | None:
        hit = await self.in_memory.find_match(tool_name, args, edict_id)
        if hit is not None:
            return hit
        return await self.sqlite.find_match(tool_name, args, edict_id)

    async def list_by_scope(
        self, scope: str, edict_id: str | None = None,
    ) -> list[SessionRule]:
        if scope == "always":
            return await self.sqlite.list_by_scope(scope, edict_id)
        return await self.in_memory.list_by_scope(scope, edict_id)

    async def revoke(self, rule_id: str) -> None:
        await self.in_memory.revoke(rule_id)
        await self.sqlite.revoke(rule_id)

    async def clear_edict(self, edict_id: str) -> None:
        await self.in_memory.clear_edict(edict_id)


# ---------- Factory helpers ----------


def make_session_rule(
    *,
    tool_name: str,
    arg_fingerprint: str,
    scope: Literal["edict", "always"],
    source: Literal["approval", "profile", "manual"],
    reason: str,
    edict_id: str | None = None,
    granted_by_decree_id: str | None = None,
    expires_after: timedelta | None = None,
) -> SessionRule:
    now = datetime.now(UTC)
    expires_at: datetime | None = None
    if expires_after is not None:
        expires_at = now + expires_after
    elif scope == "always":
        expires_at = now + timedelta(days=30)  # 默认 30 天
    return SessionRule(
        rule_id=str(uuid.uuid4()),
        tool_name=tool_name,
        arg_fingerprint=arg_fingerprint,
        scope=scope,
        edict_id=edict_id,
        granted_at=now,
        granted_by_decree_id=granted_by_decree_id,
        source=source,
        reason=reason,
        expires_at=expires_at,
    )


def assert_can_grant(tool_name: str, scope: str) -> None:
    """BashSafetyRule 的硬约束：bash 不能 always scope。

    调用方在 create 前调用；违规抛 ValueError 上层捕获并降级。
    """
    if scope == "always" and tool_name in {"shell_exec", "bash"}:
        raise ValueError(
            f"Cannot grant 'always' scope to bash-family tool {tool_name!r}"
        )
```

- [ ] **Step 2: 手动 smoke**

Run:
```bash
uv run python -c "
import asyncio
from tianshu.tools.policy_store import (
    InMemorySessionRuleStore, make_session_rule, compute_fingerprint, assert_can_grant
)
from datetime import timedelta

async def main():
    store = InMemorySessionRuleStore()
    rule = make_session_rule(
        tool_name='edit_file',
        arg_fingerprint=compute_fingerprint('edit_file', {'path': 'src/foo.py'}),
        scope='edict',
        source='approval',
        reason='test',
        edict_id='e1',
    )
    await store.create(rule)
    hit = await store.find_match('edit_file', {'path': 'src/bar.py'}, 'e1')
    assert hit is not None, 'same dir should match'
    miss = await store.find_match('edit_file', {'path': 'other/foo.py'}, 'e1')
    assert miss is None, 'different dir should miss'
    try:
        assert_can_grant('bash', 'always')
        print('FAIL: should raise')
    except ValueError:
        print('ok')

asyncio.run(main())
"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/policy_store.py
git commit -m "feat(tools): add SessionRuleStore with in-memory and composite impls"
```

---

### Task 3.2: `session_rules` 表 + Sqlite 实现

**Files:**
- Modify: `src/tianshu/storage.py`
- Modify: `src/tianshu/tools/policy_store.py`

- [ ] **Step 1: 在 Storage 层加 DDL**

打开 `src/tianshu/storage.py`，找到 `init_db` 方法中的 `CREATE TABLE` 调用区域。在现有表之后追加：

```python
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_rules (
                rule_id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                arg_fingerprint TEXT NOT NULL,
                scope TEXT NOT NULL CHECK (scope IN ('edict', 'always')),
                edict_id TEXT,
                granted_at TEXT NOT NULL,
                granted_by_decree_id TEXT,
                source TEXT NOT NULL CHECK (source IN ('approval', 'profile', 'manual')),
                reason TEXT,
                expires_at TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_rules_tool_scope "
            "ON session_rules(tool_name, scope, arg_fingerprint)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_rules_edict "
            "ON session_rules(edict_id)"
        )
```

- [ ] **Step 2: 在 `tools/policy_store.py` 末尾加 SqliteSessionRuleStore**

追加到文件末尾：

```python
# ---------- SQLite 实现 ----------


@dataclass
class SqliteSessionRuleStore:
    """always scope 持久化。依赖 tianshu.storage.Storage 的 `_conn`。"""

    storage: object  # Storage 实例，不绑死类型避免循环导入

    async def create(self, rule: SessionRule) -> None:
        conn = self.storage._conn  # type: ignore[attr-defined]
        conn.execute(
            """
            INSERT OR REPLACE INTO session_rules (
                rule_id, tool_name, arg_fingerprint, scope, edict_id,
                granted_at, granted_by_decree_id, source, reason, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule.rule_id,
                rule.tool_name,
                rule.arg_fingerprint,
                rule.scope,
                rule.edict_id,
                rule.granted_at.isoformat(),
                rule.granted_by_decree_id,
                rule.source,
                rule.reason,
                rule.expires_at.isoformat() if rule.expires_at else None,
            ),
        )
        conn.commit()

    async def find_match(
        self, tool_name: str, args: dict, edict_id: str | None,
    ) -> SessionRule | None:
        conn = self.storage._conn  # type: ignore[attr-defined]
        fp = compute_fingerprint(tool_name, args)
        now_iso = datetime.now(UTC).isoformat()
        row = conn.execute(
            """
            SELECT rule_id, tool_name, arg_fingerprint, scope, edict_id,
                   granted_at, granted_by_decree_id, source, reason, expires_at
            FROM session_rules
            WHERE tool_name = ? AND arg_fingerprint = ? AND scope = 'always'
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY granted_at DESC
            LIMIT 1
            """,
            (tool_name, fp, now_iso),
        ).fetchone()
        if not row:
            return None
        return _row_to_rule(row)

    async def list_by_scope(
        self, scope: str, edict_id: str | None = None,
    ) -> list[SessionRule]:
        conn = self.storage._conn  # type: ignore[attr-defined]
        if edict_id is not None:
            rows = conn.execute(
                "SELECT * FROM session_rules WHERE scope = ? AND edict_id = ? "
                "ORDER BY granted_at DESC",
                (scope, edict_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM session_rules WHERE scope = ? "
                "ORDER BY granted_at DESC",
                (scope,),
            ).fetchall()
        return [_row_to_rule(r) for r in rows]

    async def revoke(self, rule_id: str) -> None:
        conn = self.storage._conn  # type: ignore[attr-defined]
        conn.execute("DELETE FROM session_rules WHERE rule_id = ?", (rule_id,))
        conn.commit()

    async def clear_edict(self, edict_id: str) -> None:
        """always scope 默认不随 edict 清理 — no-op。"""
        return None


def _row_to_rule(row) -> SessionRule:
    (
        rule_id, tool_name, arg_fingerprint, scope, edict_id,
        granted_at, granted_by_decree_id, source, reason, expires_at,
    ) = row
    return SessionRule(
        rule_id=rule_id,
        tool_name=tool_name,
        arg_fingerprint=arg_fingerprint,
        scope=scope,
        edict_id=edict_id,
        granted_at=datetime.fromisoformat(granted_at),
        granted_by_decree_id=granted_by_decree_id,
        source=source,
        reason=reason,
        expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
    )
```

- [ ] **Step 3: 手动 smoke — 表创建**

1. 删除 `data/tianshu.db`（或 db_path）
2. `uv run python -c "from tianshu.storage import Storage; s = Storage('data/tianshu.db'); s.init_db(); print('ok')"`
3. `sqlite3 data/tianshu.db ".schema session_rules"` 确认表存在，3 列都就位

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/storage.py src/tianshu/tools/policy_store.py
git commit -m "feat(storage): add session_rules table and SqliteSessionRuleStore"
```

---

### Task 3.3: Decree 扩展 + ApprovalManager 写 rule

**Files:**
- Modify: `src/tianshu/models/decree.py`
- Modify: `src/tianshu/executor/approvals.py`

- [ ] **Step 1: Decree 新增字段**

```python
"""Decree model — human review decisions on memorials."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from ulid import ULID


class Decree(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    memorial_id: str
    action: Literal["approve", "reject", "retry", "amend", "cancel"]
    comment: str | None = None
    amended_goal: str | None = None
    actor: str = "human"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Spec Section 4: session rule 升级支持
    grant_scope: Literal["once", "edict", "always"] | None = None
    grant_reason: str | None = None
```

- [ ] **Step 2: ApprovalManager 注入 SessionRuleStore**

修改 `__init__` 签名：

```python
    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        session_rule_store: object | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._session_rule_store = session_rule_store
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, Decree] = {}
        # Spec Section 4: 在 wait_for_approval 时记住 tool_name → 方便 _handle_approve 写 rule
        self._pending_tool: dict[str, str] = {}
```

修改 `wait_for_approval` 方法开头，记录 tool_name：

```python
    async def wait_for_approval(
        self,
        memorial_id: str,
        tool_name: str,
    ) -> Decree | None:
        """Block until a decree is submitted for this memorial, or timeout."""
        evt = asyncio.Event()
        self._pending[memorial_id] = evt
        self._pending_tool[memorial_id] = tool_name
        ...
```

在 `finally:` 里清理：

```python
        finally:
            self._pending.pop(memorial_id, None)
            self._pending_tool.pop(memorial_id, None)
```

- [ ] **Step 3: `_handle_approve` 写 session rule**

修改 `_handle_approve` 末尾（`await self._bus.emit(...)` 之后），新增：

```python
        # Spec Section 4: grant_scope 升级为 session rule
        if decree.grant_scope and decree.grant_scope != "once" and self._session_rule_store:
            await self._write_session_rule_from_decree(memorial, decree)
```

在类末尾新增 helper：

```python
    async def _write_session_rule_from_decree(
        self, memorial: Memorial, decree: Decree,
    ) -> None:
        """根据 decree.grant_scope 写 session rule，供后续调用直接命中。"""
        from tianshu.tools.policy_store import (
            assert_can_grant, compute_fingerprint, make_session_rule,
        )

        tool_name = self._pending_tool.get(decree.memorial_id) or ""
        if not tool_name:
            logger.warning(
                "decree %s: no tool_name recorded for memorial %s, skip session rule",
                decree.id, decree.memorial_id,
            )
            return

        # bash + always 被硬约束禁止
        try:
            assert_can_grant(tool_name, decree.grant_scope or "once")
        except ValueError as e:
            logger.warning("decree %s: %s — downgrading to once", decree.id, e)
            return

        # 读取最近一次 tool.approval_required 事件以拿到 args
        args = self._fetch_latest_approval_args(memorial.id, tool_name)
        fingerprint = compute_fingerprint(tool_name, args)

        rule = make_session_rule(
            tool_name=tool_name,
            arg_fingerprint=fingerprint,
            scope=decree.grant_scope,  # "edict" | "always"
            source="approval",
            reason=decree.grant_reason or f"granted by decree {decree.id}",
            edict_id=memorial.edict_id if decree.grant_scope == "edict" else None,
            granted_by_decree_id=decree.id,
        )
        try:
            await self._session_rule_store.create(rule)
        except Exception:
            logger.exception("failed to create session rule from decree %s", decree.id)
            return

        self._storage.append_event(
            memorial.edict_id,
            memorial.id,
            "policy.session_rule_created",
            {
                "rule_id": rule.rule_id,
                "tool_name": tool_name,
                "scope": rule.scope,
                "source": rule.source,
                "arg_fingerprint": rule.arg_fingerprint,
                "decree_id": decree.id,
            },
        )

    def _fetch_latest_approval_args(
        self, memorial_id: str, tool_name: str,
    ) -> dict:
        """从 events 表反查最近一次 tool.approval_required 的 args_summary。"""
        try:
            rows = self._storage.get_events_by_memorial(memorial_id)  # may exist
        except Exception:
            rows = []
        for row in reversed(rows or []):
            event_type = row.get("type") if isinstance(row, dict) else None
            if event_type != "tool.approval_required":
                continue
            payload = row.get("payload") or {}
            if payload.get("tool_name") == tool_name:
                return payload.get("args_summary") or {}
        return {}
```

> `get_events_by_memorial` 可能不存在。如果不存在，用 Storage 直接 SQL 查：`self._storage._conn.execute("SELECT type, payload FROM events WHERE memorial_id = ? ORDER BY created_at DESC LIMIT 20", (memorial_id,)).fetchall()` 并解析 payload JSON。根据实际 Storage API 调整。

- [ ] **Step 4: 手动 smoke — Decree 扩展字段兼容**

Run:
```bash
uv run python -c "
from tianshu.models.decree import Decree
d = Decree(memorial_id='m1', action='approve', grant_scope='edict', grant_reason='trust this dir')
print(d.model_dump(mode='json'))
"
```
Expected: JSON 输出包含 `grant_scope='edict'`。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/models/decree.py src/tianshu/executor/approvals.py
git commit -m "feat(executor): Decree.grant_scope writes session rules on approve"
```

---

### Task 3.4: app.py 装配 SessionRuleStore 并注入 ApprovalManager + PolicyHook

**Files:**
- Modify: `src/tianshu/app.py`

- [ ] **Step 1: 追加 import**

```python
from tianshu.tools.policy_store import (
    CompositeSessionRuleStore,
    InMemorySessionRuleStore,
    SqliteSessionRuleStore,
)
```

- [ ] **Step 2: 装配 SessionRuleStore（插在 ApprovalManager 构造之前）**

在 `# --- ApprovalManager ---` 块之前插入：

```python
    # --- SessionRuleStore ---
    session_rule_store = CompositeSessionRuleStore(
        in_memory=InMemorySessionRuleStore(),
        sqlite=SqliteSessionRuleStore(storage=storage),
    )
    app.state.session_rule_store = session_rule_store
```

- [ ] **Step 3: ApprovalManager 构造时注入 store**

```python
    approval_manager = ApprovalManager(
        event_bus=event_bus,
        storage=storage,
        session_rule_store=session_rule_store,
    )
```

- [ ] **Step 4: PolicyHook 构造时接入 store**

找到 Step 2.5 添加的 PolicyHook 构造，改为：

```python
    policy_hook = PolicyHook(
        engine=policy_engine,
        workspace_root=Path(settings.workspace_dir).resolve(),
        storage=storage,
        tool_registry=tools,
        session_rule_store=session_rule_store,
        approval_manager=approval_manager,
    )
```

- [ ] **Step 5: 手动 smoke — 端到端闭环**

1. 启动服务
2. 提交 edict goal="编辑 src/foo.py 加一行注释"，触发 T1 `edit_file(path='src/foo.py')`
3. （当前规则：T1 默认 allow，所以不会 require_approval — 需要造一个 T2 或用 `approval_required_tools` 强制）
4. 改为 `runtime.approval_required_tools=["edit_file"]`，再提交
5. 前端审批时选 "allow for edict"（grant_scope=edict），放行
6. 再提交 goal="编辑 src/bar.py"（相同目录 src/），观察直接 allow，event `policy.session_rule_matched` 出现
7. 提交 goal="编辑 other/bar.py"（不同目录），再次 require_approval

> 对应 Spec Section 8 清单第 5 条。

> 手动验证清单第 10 条：POST `/decrees` action=approve grant_scope=always tool_name=shell_exec → 后台 WARNING 日志 "bash tool cannot be granted always scope"，当次调用放行但不写 rule。

- [ ] **Step 6: Commit**

```bash
git add src/tianshu/app.py
git commit -m "feat(app): wire SessionRuleStore into ApprovalManager and PolicyHook"
```

---

## Step 4: Policy Profile（扩展 E）

### Task 4.1: `PolicyProfile` 数据类 + 3 内建模板

**Files:**
- Create: `src/tianshu/tools/policy_profile.py`

- [ ] **Step 1: 写文件**

```python
"""PolicyProfile — 任务级权限预配（proactive）。

Spec Section 5。在 edict 启动时展开为 edict-scope session rules，
解决长任务被频繁审批打断的问题。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from tianshu.tools.policy_store import (
    SessionRule,
    assert_can_grant,
    make_session_rule,
)
from tianshu.tools.types import ToolTier

if TYPE_CHECKING:
    from tianshu.models.edict import Edict

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyProfile:
    allowed_paths: tuple[str, ...] = ()
    allowed_bash_prefixes: tuple[str, ...] = ()
    tier_overrides: dict[str, int] = field(default_factory=dict)
    auto_approve_max_tier: int = ToolTier.T1_WORKSPACE.value
    expires_after_seconds: int | None = None
    template_name: str | None = None


# 3 个硬编码模板（Spec Section 5）
BUILTIN_TEMPLATES: dict[str, PolicyProfile] = {
    "safe-explore": PolicyProfile(
        allowed_paths=(),
        allowed_bash_prefixes=(),
        auto_approve_max_tier=ToolTier.T0_READONLY.value,
        template_name="safe-explore",
    ),
    "refactor-in-place": PolicyProfile(
        allowed_paths=("**/*",),
        allowed_bash_prefixes=("git status", "git diff"),
        auto_approve_max_tier=ToolTier.T1_WORKSPACE.value,
        template_name="refactor-in-place",
    ),
    "trusted-automation": PolicyProfile(
        allowed_paths=("**/*",),
        allowed_bash_prefixes=("git ", "pytest", "ruff", "black", "mypy"),
        auto_approve_max_tier=ToolTier.T2_WRITE.value,
        template_name="trusted-automation",
    ),
}


async def expand_profile_to_rules(
    profile: PolicyProfile,
    edict: "Edict",
    store: object,
) -> int:
    """把 profile 展开为一批 edict-scope session rules，返回创建数量。

    硬约束：只创建 edict scope，不能 always；bash + always 组合拒绝；
    每条 rule source='profile'。
    """
    if profile is None:
        return 0

    created = 0
    expires_after = (
        timedelta(seconds=profile.expires_after_seconds)
        if profile.expires_after_seconds
        else None
    )

    # 1. allowed_paths → edit_file / write_file rules
    for path_glob in profile.allowed_paths:
        for tool in ("edit_file", "write_file"):
            try:
                assert_can_grant(tool, "edict")
            except ValueError:
                continue
            rule = make_session_rule(
                tool_name=tool,
                arg_fingerprint=f"glob:{path_glob}",
                scope="edict",
                source="profile",
                reason=f"preconfigured by policy_profile (template={profile.template_name})",
                edict_id=edict.id,
                expires_after=expires_after,
            )
            await store.create(rule)
            created += 1

    # 2. allowed_bash_prefixes → shell_exec rules（bash + edict 是允许的，但不是 always）
    for prefix in profile.allowed_bash_prefixes:
        rule = make_session_rule(
            tool_name="shell_exec",
            arg_fingerprint=_prefix_to_fingerprint(prefix),
            scope="edict",
            source="profile",
            reason=f"preconfigured bash prefix {prefix!r} (template={profile.template_name})",
            edict_id=edict.id,
            expires_after=expires_after,
        )
        await store.create(rule)
        created += 1

    return created


def _prefix_to_fingerprint(prefix: str) -> str:
    """匹配 tools.policy_store.fingerprint_bash 的格式。"""
    tokens = prefix.strip().split()[:2]
    return "bash:" + " ".join(tokens)
```

- [ ] **Step 2: 手动 smoke**

Run:
```bash
uv run python -c "
from tianshu.tools.policy_profile import BUILTIN_TEMPLATES, PolicyProfile
for name, prof in BUILTIN_TEMPLATES.items():
    print(name, prof.allowed_bash_prefixes, prof.auto_approve_max_tier)
"
```
Expected:
```
safe-explore () 0
refactor-in-place ('git status', 'git diff') 1
trusted-automation ('git ', 'pytest', 'ruff', 'black', 'mypy') 2
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/policy_profile.py
git commit -m "feat(tools): add PolicyProfile with 3 builtin templates"
```

---

### Task 4.2: `EdictRuntime.policy_profile` 字段 + `tier_overrides`

**Files:**
- Modify: `src/tianshu/models/edict.py`

- [ ] **Step 1: 新增字段**

在 `EdictRuntime` class 内追加：

```python
class EdictRuntime(BaseModel):
    timeout_seconds: int = 300
    max_iterations: int = 20
    max_concurrency: int = 1
    retry_limit: int = 0
    token_budget: int | None = None
    cost_budget_cny: float | None = None
    approval_required_tools: list[str] = Field(default_factory=list)
    # Spec Section 5: Policy Profile 预配权限
    policy_profile: "PolicyProfilePayload | None" = None
    tier_overrides: dict[str, int] = Field(default_factory=dict)


class PolicyProfilePayload(BaseModel):
    """Pydantic 版 PolicyProfile — 用于 JSON 序列化。

    运行时 Executor 会把它转成 tools.policy_profile.PolicyProfile（frozen dataclass）
    再调用 expand_profile_to_rules。
    """

    allowed_paths: list[str] = Field(default_factory=list)
    allowed_bash_prefixes: list[str] = Field(default_factory=list)
    tier_overrides: dict[str, int] = Field(default_factory=dict)
    auto_approve_max_tier: int = 1  # T1_WORKSPACE
    expires_after_seconds: int | None = None
    template_name: str | None = None
```

> 顺序：`PolicyProfilePayload` 必须在 `EdictRuntime` 之前定义，或用 `EdictRuntime.model_rebuild()`。用 forward reference + `model_rebuild` 更稳：把 `PolicyProfilePayload` 移到 `Edict` 类定义之后再 rebuild，或者直接放在 `EdictRuntime` 之前。

正确的文件结构：先定义 `PolicyProfilePayload`，再定义 `EdictRuntime`。按此顺序重写。

- [ ] **Step 2: 手动 smoke**

Run:
```bash
uv run python -c "
from tianshu.models.edict import Edict, EdictRuntime, PolicyProfilePayload
e = Edict(goal='x', runtime=EdictRuntime(policy_profile=PolicyProfilePayload(allowed_paths=['src/*'], template_name='refactor-in-place')))
print(e.runtime.policy_profile.allowed_paths)
print(e.runtime.tier_overrides)
"
```
Expected: `['src/*']` + `{}`

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/models/edict.py
git commit -m "feat(models): add policy_profile and tier_overrides to EdictRuntime"
```

---

### Task 4.3: Executor 在 SESSION_START 前展开 profile

**Files:**
- Modify: `src/tianshu/executor/executor.py`

- [ ] **Step 1: Executor 注入 session_rule_store**

打开 `executor/executor.py`，在 `__init__` 加参数：

```python
    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        config_manager: ConfigManager,
        hook_registry: HookRegistry,
        session_rule_store: object | None = None,
    ) -> None:
        ...
        self._session_rule_store = session_rule_store
```

- [ ] **Step 2: 在 `execute_edict` 的 SESSION_START hook 之前展开 profile**

找到约 191 行 `# Session start hook` 之前，插入：

```python
        # Spec Section 5: 展开 PolicyProfile → edict-scope session rules
        if (
            edict.runtime.policy_profile is not None
            and self._session_rule_store is not None
        ):
            try:
                from tianshu.tools.policy_profile import (
                    PolicyProfile,
                    expand_profile_to_rules,
                )

                payload = edict.runtime.policy_profile
                profile = PolicyProfile(
                    allowed_paths=tuple(payload.allowed_paths),
                    allowed_bash_prefixes=tuple(payload.allowed_bash_prefixes),
                    tier_overrides=dict(payload.tier_overrides),
                    auto_approve_max_tier=int(payload.auto_approve_max_tier),
                    expires_after_seconds=payload.expires_after_seconds,
                    template_name=payload.template_name,
                )
                created = await expand_profile_to_rules(
                    profile, edict, self._session_rule_store,
                )
                self._storage.append_event(
                    edict.id,
                    memorial.id,
                    "policy.profile_applied",
                    {
                        "template_name": profile.template_name,
                        "rules_created": created,
                        "allowed_paths": list(profile.allowed_paths),
                        "allowed_bash_prefixes": list(profile.allowed_bash_prefixes),
                        "auto_approve_max_tier": profile.auto_approve_max_tier,
                    },
                )
                logger.info(
                    "[EXEC] Edict %s: applied profile template=%s created=%d rules",
                    edict.id, profile.template_name, created,
                )
            except Exception:
                logger.exception(
                    "[EXEC] Edict %s: failed to apply policy profile",
                    edict.id,
                )
```

- [ ] **Step 3: `execute_edict` 结束时清理 edict-scope rules**

在 `finally:` 块里 `self._storage.update_memorial(memorial)` 之前或之后，追加：

```python
            # Spec Section 5: 任务结束清理 edict-scope rules
            if self._session_rule_store is not None:
                try:
                    await self._session_rule_store.clear_edict(edict.id)
                except Exception:
                    logger.exception(
                        "[EXEC] Edict %s: failed to clear edict session rules",
                        edict.id,
                    )
```

- [ ] **Step 4: app.py 构造 Executor 时传 session_rule_store**

打开 `app.py`，找到 `executor = Executor(...)` 调用（约 159 行），把构造位置**移到** SessionRuleStore 构造**之后**：

原本的 executor 构造块必须位于 session_rule_store 构造之后。如果当前顺序不对，把整块 Executor 构造和其下面的 `executor.set_agent` / `executor.set_persona_loader` / `app.state.executor = executor` / `executor.set_dag_scheduler` / `executor.set_lane_manager` **连带**移动到 session_rule_store 构造之后。

然后构造改为：

```python
    executor = Executor(
        event_bus=event_bus,
        storage=storage,
        config_manager=config_manager,
        hook_registry=hook_registry,
        session_rule_store=session_rule_store,
    )
```

> 注意：因为 Executor 现在依赖 SessionRuleStore，且 SessionRuleStore 依赖 Storage（已存在），新的顺序必须是：Storage → SessionRuleStore → ApprovalManager → PolicyEngine/PolicyHook → Executor。同时 Agent 仍然先于 Executor 构造（Executor.set_agent 需要）。重新排序时确认 `HookRegistry` 早于 `Executor`（已成立）。

- [ ] **Step 5: 手动 smoke — profile 展开与清理**

1. 提交 edict with `runtime.policy_profile = {"template_name": "refactor-in-place", "allowed_paths": ["**/*"], "allowed_bash_prefixes": ["git status", "git diff"], "auto_approve_max_tier": 1}`, goal="用 git diff 看看改动，然后修一下 src/foo.py"
2. 观察 events 表 `policy.profile_applied` 事件存在
3. 后续 `edit_file(src/*)` 和 `shell_exec('git diff')` 直接 allow（rule_id=session_rule:...）
4. `shell_exec('rm -rf /')` 仍然 deny（BashSafetyRule 黑名单优先）
5. 任务完成后 `SELECT * FROM session_rules WHERE edict_id=?` 应为 0 行（InMemory 已 clear）

> 对应 Spec Section 8 清单第 11、12 条。

- [ ] **Step 6: Commit**

```bash
git add src/tianshu/executor/executor.py src/tianshu/app.py
git commit -m "feat(executor): expand PolicyProfile to session rules on edict start"
```

---

## Step 5: Web UI 消费（Gap D）

### Task 5.1: 后端 Policy API 路由

**Files:**
- Modify: `src/tianshu/gateway/api.py`

- [ ] **Step 1: 在文件末尾（在 WebSocket 路由前）追加 Policy 段**

找到 `# --- Decree (approval) endpoints ---` 段的末尾，其下追加：

```python
# --- Policy endpoints (Spec Section 6) ---


@gateway_router.get("/edicts/{edict_id}/policy_events")
async def list_policy_events(edict_id: str, request: Request):
    """返回该 edict 的 policy.* + hook.* + tool.approval_required + decree.* 事件。"""
    storage = request.app.state.storage
    conn = storage._conn
    rows = conn.execute(
        """
        SELECT id, edict_id, memorial_id, type, payload, created_at
        FROM events
        WHERE edict_id = ?
          AND (type LIKE 'policy.%' OR type LIKE 'hook.%'
               OR type = 'tool.approval_required'
               OR type LIKE 'decree.%')
        ORDER BY created_at ASC
        """,
        (edict_id,),
    ).fetchall()
    data = []
    for row in rows:
        eid, eid2, mid, typ, payload, ts = row
        import json as _json
        try:
            parsed = _json.loads(payload) if isinstance(payload, str) else payload
        except Exception:
            parsed = {"raw": payload}
        data.append({
            "id": eid,
            "memorial_id": mid,
            "type": typ,
            "payload": parsed,
            "created_at": ts,
        })
    return ApiResponse(success=True, data={"events": data})


@gateway_router.get("/policy/session_rules")
async def list_session_rules(request: Request, scope: str = "always"):
    store = getattr(request.app.state, "session_rule_store", None)
    if store is None:
        return ApiResponse(success=True, data={"rules": []})
    rules = await store.list_by_scope(scope=scope)
    data = [
        {
            "rule_id": r.rule_id,
            "tool_name": r.tool_name,
            "arg_fingerprint": r.arg_fingerprint,
            "scope": r.scope,
            "edict_id": r.edict_id,
            "granted_at": r.granted_at.isoformat(),
            "granted_by_decree_id": r.granted_by_decree_id,
            "source": r.source,
            "reason": r.reason,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in rules
    ]
    return ApiResponse(success=True, data={"rules": data})


@gateway_router.delete("/policy/session_rules/{rule_id}", response_model=ApiResponse)
async def revoke_session_rule(rule_id: str, request: Request):
    store = getattr(request.app.state, "session_rule_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="SessionRuleStore not configured")
    await store.revoke(rule_id)
    storage = request.app.state.storage
    try:
        storage._conn.execute(
            "INSERT INTO events (edict_id, memorial_id, type, payload, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            ("", None, "policy.session_rule_revoked",
             __import__("json").dumps({"rule_id": rule_id, "source": "manual"})),
        )
        storage._conn.commit()
    except Exception:
        pass
    return ApiResponse(success=True, data={"rule_id": rule_id, "revoked": True})


@gateway_router.get("/policy/stats")
async def policy_stats(request: Request):
    """今日 allow/deny/approved/rejected 聚合。"""
    storage = request.app.state.storage
    conn = storage._conn
    stats = {"allow": 0, "deny": 0, "require_approval": 0, "approved": 0, "rejected": 0}
    import json as _json
    rows = conn.execute(
        """
        SELECT type, payload FROM events
        WHERE date(created_at) = date('now')
          AND type IN ('policy.decision', 'decree.approved', 'decree.rejected')
        """
    ).fetchall()
    for typ, payload in rows:
        if typ == "decree.approved":
            stats["approved"] += 1
        elif typ == "decree.rejected":
            stats["rejected"] += 1
        elif typ == "policy.decision":
            try:
                p = _json.loads(payload) if isinstance(payload, str) else payload
                v = p.get("verdict", "")
                if v in stats:
                    stats[v] += 1
            except Exception:
                pass
    return ApiResponse(success=True, data=stats)


@gateway_router.get("/policy/templates")
async def list_policy_templates():
    from tianshu.tools.policy_profile import BUILTIN_TEMPLATES
    data = [
        {
            "name": name,
            "allowed_paths": list(p.allowed_paths),
            "allowed_bash_prefixes": list(p.allowed_bash_prefixes),
            "tier_overrides": dict(p.tier_overrides),
            "auto_approve_max_tier": p.auto_approve_max_tier,
        }
        for name, p in BUILTIN_TEMPLATES.items()
    ]
    return ApiResponse(success=True, data={"templates": data})
```

> Storage `_conn` 直接操作是仿照已有代码（`gateway/api.py` 部分路由也这样做）。如果项目里已有 `Storage.get_events_by_edict` 或类似 helper，优先复用它们 — 先 grep `def get_events` 确认。

- [ ] **Step 2: 手动 smoke — API 可达**

1. 启动服务
2. `curl http://localhost:8000/api/policy/templates | jq` → 预期返回 3 个模板
3. `curl http://localhost:8000/api/policy/session_rules?scope=always | jq` → 空列表
4. 提交一个 edict 触发 policy.decision 事件后，`curl http://localhost:8000/api/edicts/<id>/policy_events | jq` → 能看到事件

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/gateway/api.py
git commit -m "feat(api): add policy endpoints for events/rules/stats/templates"
```

---

### Task 5.2: 前端 API 客户端

**Files:**
- Create: `web/src/api/policy.ts`

- [ ] **Step 1: 写文件**

```typescript
import { api } from "./client";

export interface PolicyEvent {
  id: number;
  memorial_id: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface SessionRule {
  rule_id: string;
  tool_name: string;
  arg_fingerprint: string;
  scope: "edict" | "always";
  edict_id: string | null;
  granted_at: string;
  granted_by_decree_id: string | null;
  source: "approval" | "profile" | "manual";
  reason: string;
  expires_at: string | null;
}

export interface PolicyStats {
  allow: number;
  deny: number;
  require_approval: number;
  approved: number;
  rejected: number;
}

export interface PolicyTemplate {
  name: string;
  allowed_paths: string[];
  allowed_bash_prefixes: string[];
  tier_overrides: Record<string, number>;
  auto_approve_max_tier: number;
}

export async function fetchPolicyEvents(edictId: string): Promise<PolicyEvent[]> {
  const res = await api.get(`/edicts/${edictId}/policy_events`);
  return res.data?.data?.events ?? [];
}

export async function fetchSessionRules(scope: "edict" | "always" = "always"): Promise<SessionRule[]> {
  const res = await api.get(`/policy/session_rules`, { params: { scope } });
  return res.data?.data?.rules ?? [];
}

export async function revokeSessionRule(ruleId: string): Promise<void> {
  await api.delete(`/policy/session_rules/${ruleId}`);
}

export async function fetchPolicyStats(): Promise<PolicyStats> {
  const res = await api.get(`/policy/stats`);
  return res.data?.data ?? { allow: 0, deny: 0, require_approval: 0, approved: 0, rejected: 0 };
}

export async function fetchPolicyTemplates(): Promise<PolicyTemplate[]> {
  const res = await api.get(`/policy/templates`);
  return res.data?.data?.templates ?? [];
}
```

> 确认 `api.ts` / `client.ts` 导出的 HTTP 客户端变量名，按实际项目调整（可能是 `apiClient` 或 `http`）。参考现有 `web/src/api/decrees.ts` 保持一致。

- [ ] **Step 2: Commit（前端源码变更可和后续组件一起 build 再 commit；单独先 commit API 便于 review）**

```bash
git add web/src/api/policy.ts
git commit -m "feat(web): add policy API client"
```

---

### Task 5.3: Policy Timeline 面板（Edict 详情页）

**Files:**
- Create: `web/src/components/policy/PolicyTimeline.tsx`
- Modify: `web/src/pages/EdictDetailPage.tsx`

- [ ] **Step 1: 写 `PolicyTimeline.tsx`**

```typescript
import { useEffect, useState } from "react";
import { Card, Tag, Timeline, Tooltip, Typography, Empty } from "antd";
import { fetchPolicyEvents, PolicyEvent } from "../../api/policy";

const { Text } = Typography;

function verdictColor(verdict: string): string {
  switch (verdict) {
    case "allow": return "green";
    case "deny": return "red";
    case "require_approval": return "orange";
    default: return "blue";
  }
}

interface Props {
  edictId: string;
  refreshKey?: number;
}

export function PolicyTimeline({ edictId, refreshKey }: Props) {
  const [events, setEvents] = useState<PolicyEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPolicyEvents(edictId)
      .then((data) => { if (!cancelled) setEvents(data); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [edictId, refreshKey]);

  if (!loading && events.length === 0) {
    return (
      <Card title="Policy Timeline" size="small">
        <Empty description="No policy events" />
      </Card>
    );
  }

  return (
    <Card title="Policy Timeline" size="small" loading={loading}>
      <Timeline mode="left">
        {events.map((e) => {
          const p = e.payload as Record<string, unknown>;
          const verdict = (p.verdict as string) ?? "";
          const ruleId = (p.rule_id as string) ?? "";
          const toolName = (p.tool_name as string) ?? "";
          const reason = (p.reason as string) ?? "";
          const tag =
            e.type === "policy.decision" ? (
              <Tag color={verdictColor(verdict)}>{verdict}</Tag>
            ) : (
              <Tag>{e.type.replace(/^(policy|hook|tool|decree)\./, "")}</Tag>
            );
          return (
            <Timeline.Item
              key={e.id}
              color={verdictColor(verdict)}
              label={new Date(e.created_at).toLocaleTimeString()}
            >
              {tag}
              {toolName && <Text code style={{ marginLeft: 8 }}>{toolName}</Text>}
              {ruleId && <Text type="secondary" style={{ marginLeft: 8 }}>{ruleId}</Text>}
              {reason && (
                <Tooltip title={reason}>
                  <Text
                    type="secondary"
                    ellipsis
                    style={{ marginLeft: 8, maxWidth: 400, display: "inline-block" }}
                  >
                    {reason}
                  </Text>
                </Tooltip>
              )}
            </Timeline.Item>
          );
        })}
      </Timeline>
    </Card>
  );
}
```

> 组件使用 AntD，按项目实际已用版本语法调整（AntD v5 的 Timeline 是 `items` prop；v4 仍然是 children）。查看 `web/package.json` 确认版本。

- [ ] **Step 2: 在 `EdictDetailPage.tsx` 嵌入**

打开 `web/src/pages/EdictDetailPage.tsx`，在现有详情卡片区域合适位置（比如 Memorials Tab 或单独一个折叠项）追加：

```typescript
import { PolicyTimeline } from "../components/policy/PolicyTimeline";

// 在 JSX 合适位置：
<PolicyTimeline edictId={edictId} />
```

- [ ] **Step 3: 手动 smoke**

1. `cd web && npm run dev`
2. 打开一个已有 edict 详情页
3. 确认 Policy Timeline 面板出现，没有 event 时显示 empty state
4. 触发一个需要审批的 edict，刷新页面，确认 policy.decision / hook.before_tool_call / tool.approval_required / decree.approved 按时间顺序展示，verdict 标签颜色正确

- [ ] **Step 4: Commit**

```bash
git add web/src/components/policy/PolicyTimeline.tsx web/src/pages/EdictDetailPage.tsx
git commit -m "feat(web): add PolicyTimeline component to Edict detail"
```

---

### Task 5.4: Audit Dashboard 新增 Policy Decisions Tab

**Files:**
- Modify: `web/src/pages/AuditDashboardPage.tsx`

- [ ] **Step 1: 加 Tab**

打开 `AuditDashboardPage.tsx`，在现有 Tabs 结构里新增一个 Tab：

```typescript
import { fetchPolicyStats, PolicyStats } from "../api/policy";
import { Col, Row, Statistic, Card } from "antd";

// 组件内：
const [stats, setStats] = useState<PolicyStats | null>(null);
useEffect(() => {
  fetchPolicyStats().then(setStats);
  const t = setInterval(() => fetchPolicyStats().then(setStats), 10000);
  return () => clearInterval(t);
}, []);

// 在 Tabs items 里追加：
{
  key: "policy",
  label: "Policy Decisions",
  children: stats ? (
    <Row gutter={16}>
      <Col span={4}><Card><Statistic title="Allow" value={stats.allow} valueStyle={{ color: "#52c41a" }} /></Card></Col>
      <Col span={4}><Card><Statistic title="Deny" value={stats.deny} valueStyle={{ color: "#ff4d4f" }} /></Card></Col>
      <Col span={4}><Card><Statistic title="Require Approval" value={stats.require_approval} valueStyle={{ color: "#fa8c16" }} /></Card></Col>
      <Col span={4}><Card><Statistic title="Approved" value={stats.approved} /></Card></Col>
      <Col span={4}><Card><Statistic title="Rejected" value={stats.rejected} /></Card></Col>
    </Row>
  ) : null,
}
```

> 具体语法按页面已有 `Tabs` 写法调整（可能是 `items={[]}` 或旧版 `<TabPane>`）。先读几行原文件适配。

- [ ] **Step 2: 手动 smoke**

1. 访问 AuditDashboard 页，切到 Policy Decisions Tab
2. 触发几个任务产生 decision → 10 秒后统计数字刷新

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/AuditDashboardPage.tsx
git commit -m "feat(web): add Policy Decisions tab to AuditDashboard"
```

---

### Task 5.5: Session Rules 管理页

**Files:**
- Create: `web/src/pages/SessionRulesPage.tsx`
- Modify: `web/src/App.tsx`（或 routes 配置文件）
- Modify: 左侧菜单配置

- [ ] **Step 1: 写页面**

```typescript
import { useEffect, useState } from "react";
import { Table, Button, Tag, Popconfirm, message, Card, Select } from "antd";
import { fetchSessionRules, revokeSessionRule, SessionRule } from "../api/policy";

const { Column } = Table;

export default function SessionRulesPage() {
  const [rules, setRules] = useState<SessionRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<string>("all");

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchSessionRules("always");
      setRules(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const filtered = sourceFilter === "all"
    ? rules
    : rules.filter((r) => r.source === sourceFilter);

  const handleRevoke = async (ruleId: string) => {
    try {
      await revokeSessionRule(ruleId);
      message.success("Rule revoked");
      await load();
    } catch (e) {
      message.error("Failed to revoke");
    }
  };

  return (
    <Card title="Session Rules (always scope)">
      <div style={{ marginBottom: 16 }}>
        <Select
          value={sourceFilter}
          onChange={setSourceFilter}
          style={{ width: 200 }}
          options={[
            { value: "all", label: "All sources" },
            { value: "approval", label: "Approval" },
            { value: "profile", label: "Profile" },
            { value: "manual", label: "Manual" },
          ]}
        />
      </div>
      <Table dataSource={filtered} rowKey="rule_id" loading={loading} pagination={{ pageSize: 20 }}>
        <Column title="Tool" dataIndex="tool_name" />
        <Column title="Fingerprint" dataIndex="arg_fingerprint" />
        <Column
          title="Source"
          dataIndex="source"
          render={(v: string) => <Tag color={v === "approval" ? "blue" : v === "profile" ? "geekblue" : "default"}>{v}</Tag>}
        />
        <Column title="Granted at" dataIndex="granted_at" render={(v: string) => new Date(v).toLocaleString()} />
        <Column title="Expires" dataIndex="expires_at" render={(v: string | null) => v ? new Date(v).toLocaleDateString() : "never"} />
        <Column title="Reason" dataIndex="reason" ellipsis />
        <Column
          title="Actions"
          render={(_, row: SessionRule) => (
            <Popconfirm title="Revoke this rule?" onConfirm={() => handleRevoke(row.rule_id)}>
              <Button size="small" danger>Revoke</Button>
            </Popconfirm>
          )}
        />
      </Table>
    </Card>
  );
}
```

- [ ] **Step 2: 注册路由**

打开 `web/src/App.tsx`（或 router 配置），添加：

```typescript
import SessionRulesPage from "./pages/SessionRulesPage";

// 在路由表：
<Route path="/session-rules" element={<SessionRulesPage />} />
```

- [ ] **Step 3: 加菜单项**

找到左侧菜单数据源（`web/src/components/layout/*` 下），追加：

```typescript
{ key: "/session-rules", icon: <SafetyOutlined />, label: "Session Rules" }
```

- [ ] **Step 4: 手动 smoke**

1. 先通过审批勾选 "always" 生成几条 always scope rules（走 Step 3.3 的闭环）
2. 打开 `/session-rules` 页面，应看到规则列表
3. 点击 "Revoke" → 规则消失
4. 再次触发相同工具调用 → 需要重新审批（因为规则已撤销）

> 对应 Spec Section 8 清单第 6 条。

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/SessionRulesPage.tsx web/src/App.tsx web/src/components/layout/
git commit -m "feat(web): add Session Rules management page"
```

---

### Task 5.6: Policy Profile 配置面板（创建 Edict 表单）

**Files:**
- Create: `web/src/components/policy/PolicyProfilePanel.tsx`
- Modify: `web/src/pages/EdictCreatePage.tsx`

- [ ] **Step 1: 写组件**

```typescript
import { useEffect, useState } from "react";
import { Collapse, Form, Select, Input, InputNumber, Space, Typography } from "antd";
import { fetchPolicyTemplates, PolicyTemplate } from "../../api/policy";

const { TextArea } = Input;
const { Text } = Typography;

export interface PolicyProfileValue {
  template_name: string | null;
  allowed_paths: string[];
  allowed_bash_prefixes: string[];
  auto_approve_max_tier: number;
  expires_after_seconds: number | null;
}

interface Props {
  value?: PolicyProfileValue;
  onChange?: (value: PolicyProfileValue | null) => void;
}

const tierOptions = [
  { value: 0, label: "T0 Readonly" },
  { value: 1, label: "T1 Workspace" },
  { value: 2, label: "T2 Write" },
];

export function PolicyProfilePanel({ value, onChange }: Props) {
  const [templates, setTemplates] = useState<PolicyTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(value?.template_name ?? null);
  const [pathsText, setPathsText] = useState((value?.allowed_paths ?? []).join("\n"));
  const [bashText, setBashText] = useState((value?.allowed_bash_prefixes ?? []).join("\n"));
  const [maxTier, setMaxTier] = useState(value?.auto_approve_max_tier ?? 1);
  const [expires, setExpires] = useState<number | null>(value?.expires_after_seconds ?? null);

  useEffect(() => { fetchPolicyTemplates().then(setTemplates); }, []);

  const emit = (override: Partial<PolicyProfileValue>) => {
    const next: PolicyProfileValue = {
      template_name: selectedTemplate,
      allowed_paths: pathsText.split("\n").map((s) => s.trim()).filter(Boolean),
      allowed_bash_prefixes: bashText.split("\n").map((s) => s.trim()).filter(Boolean),
      auto_approve_max_tier: maxTier,
      expires_after_seconds: expires,
      ...override,
    };
    onChange?.(next);
  };

  const applyTemplate = (name: string | null) => {
    setSelectedTemplate(name);
    if (!name) { emit({ template_name: null }); return; }
    const tpl = templates.find((t) => t.name === name);
    if (!tpl) { emit({ template_name: name }); return; }
    setPathsText(tpl.allowed_paths.join("\n"));
    setBashText(tpl.allowed_bash_prefixes.join("\n"));
    setMaxTier(tpl.auto_approve_max_tier);
    emit({
      template_name: name,
      allowed_paths: tpl.allowed_paths,
      allowed_bash_prefixes: tpl.allowed_bash_prefixes,
      auto_approve_max_tier: tpl.auto_approve_max_tier,
    });
  };

  return (
    <Collapse items={[{
      key: "profile",
      label: "Policy Profile (optional)",
      children: (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Form.Item label="Template">
            <Select
              allowClear
              placeholder="Pick a template or custom"
              value={selectedTemplate ?? undefined}
              onChange={applyTemplate}
              options={[
                ...templates.map((t) => ({ value: t.name, label: t.name })),
                { value: "custom", label: "custom" },
              ]}
            />
          </Form.Item>
          <Form.Item label="Allowed paths (glob, one per line)">
            <TextArea rows={3} value={pathsText} onChange={(e) => { setPathsText(e.target.value); emit({ allowed_paths: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) }); }} />
          </Form.Item>
          <Form.Item label="Allowed bash prefixes (one per line)">
            <TextArea rows={3} value={bashText} onChange={(e) => { setBashText(e.target.value); emit({ allowed_bash_prefixes: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) }); }} />
          </Form.Item>
          <Form.Item label="Auto-approve max tier">
            <Select value={maxTier} onChange={(v) => { setMaxTier(v); emit({ auto_approve_max_tier: v }); }} options={tierOptions} />
          </Form.Item>
          <Form.Item label="Expires after (seconds, blank = task end)">
            <InputNumber value={expires ?? undefined} onChange={(v) => { setExpires(v as number | null); emit({ expires_after_seconds: (v as number | null) }); }} />
          </Form.Item>
          <Text type="secondary">
            Profile rules are edict-scope only. Deny-list (e.g., rm -rf /) always wins.
          </Text>
        </Space>
      ),
    }]} />
  );
}
```

- [ ] **Step 2: 在 `EdictCreatePage.tsx` 嵌入组件并把值塞进提交 payload**

找到 `EdictCreatePage.tsx` 中组装 `runtime` 字段的位置（大概是 `edict_kwargs.runtime = ...` 或表单 initial values），新增：

```typescript
import { PolicyProfilePanel, PolicyProfileValue } from "../components/policy/PolicyProfilePanel";

// state
const [policyProfile, setPolicyProfile] = useState<PolicyProfileValue | null>(null);

// JSX（在 runtime 相关 fields 附近）
<PolicyProfilePanel value={policyProfile ?? undefined} onChange={setPolicyProfile} />

// 提交时（在现有 submit handler 里）
const runtime = {
  ...existingRuntimeFields,
  ...(policyProfile && (policyProfile.allowed_paths.length || policyProfile.allowed_bash_prefixes.length || policyProfile.template_name)
      ? { policy_profile: policyProfile }
      : {}),
};
```

> 具体字段名与现有 create 请求 payload shape 对齐（查 `api/edicts.ts`）。

- [ ] **Step 3: 手动 smoke**

1. 打开创建 Edict 页
2. 展开 Policy Profile 折叠项
3. 选模板 `refactor-in-place`，表单自动填入 `**/*` 和 `git status/diff`
4. 提交任务
5. 后端 events 出现 `policy.profile_applied`
6. 任务中的 `edit_file` / `git diff` 直接 allow（session_rule 命中）

> 对应 Spec Section 8 清单第 11 条。

- [ ] **Step 4: Commit**

```bash
git add web/src/components/policy/PolicyProfilePanel.tsx web/src/pages/EdictCreatePage.tsx
git commit -m "feat(web): add PolicyProfilePanel for edict creation"
```

---

### Task 5.7: 实时通知（WebSocket toast）

**Files:**
- Modify: `web/src/App.tsx` 或现有 WebSocket hook 文件

- [ ] **Step 1: 在现有 WebSocket 订阅器里增加 policy 事件 handler**

先 grep 定位：`grep -rn "useEffect.*WebSocket\|new WebSocket" web/src` 找当前 ws 订阅逻辑。

在事件处理函数里追加：

```typescript
import { notification } from "antd";

// 在 onMessage 解析 event 后：
if (event.type === "tool.approval_required") {
  notification.warning({
    message: "Approval required",
    description: `${event.payload?.tool_name}: ${event.payload?.reason ?? ""}`,
    duration: 0, // 不自动关闭
    onClick: () => {
      // 跳转到审批队列页
      window.location.href = "/approval-queue";
    },
  });
}
if (event.type === "policy.decision" && event.payload?.verdict === "deny") {
  notification.error({
    message: "Policy deny",
    description: `${event.payload?.tool_name}: ${event.payload?.reason ?? ""}`,
    duration: 5,
  });
}
```

> 项目可能已用 AntD `notification.open`，按现有用法适配。

- [ ] **Step 2: 手动 smoke**

1. 打开页面保持在前端
2. 在另一个窗口提交一个需要审批的任务
3. 前端右上角出现 warning toast "Approval required"
4. 点击 toast 跳转到审批队列

- [ ] **Step 3: Commit**

```bash
git add web/src/
git commit -m "feat(web): add realtime toast for approval_required and policy deny"
```

---

### Task 5.8: 端到端 Manual verification 全量跑一遍

**Files:** none（纯手动验证）

- [ ] **Step 1: 按 Spec Section 8 清单逐项复测**

使用 Spec Section 8 的 12 项 checklist 从头到尾跑一遍，逐条记录 `✅ / ❌`。失败的立刻回到对应 Step 修。所有项通过后提交本次工作的总结 commit（如有文档更新）。

| # | 场景 | 期望 | 对应 Step |
|---|------|------|----------|
| 1 | T0 `list_dir` | 快路径，无 policy.decision | 1.2 / 1.4 |
| 2 | T1 `edit_file` workspace 内 | allow via default_tier | 2.2 / 2.5 |
| 3 | T1 `edit_file` /etc/passwd | deny via workspace_boundary | 2.2 / 2.5 |
| 4 | T3 `shell_exec 'ls'` | require_approval, toast | 2.2 / 2.5 / 5.7 |
| 5 | 审批 "always" → 同类再调 | allow via session_rule | 3.3 / 3.4 |
| 6 | Session Rules 页撤销 | 下次调用再审批 | 5.5 |
| 7 | 规则抛异常 | 弃权 + WARNING | 2.1 |
| 8 | Engine 异常 | deny + ERROR | 2.1 |
| 9 | 未声明 tier 工具 | T3 默认 | 1.2 |
| 10 | `bash` + `grant_scope=always` | 拒绝写 rule | 3.3 |
| 11 | refactor-in-place 模板 | profile_applied, edit_file 不弹 | 4.3 / 5.6 |
| 12 | 白名单 `git push` + 黑名单 `rm -rf /` | 前放行后拒 | 4.3 / 2.2 |

- [ ] **Step 2: 回归 — 不要破坏现有功能**

重点巡检：
- 已有的 `approval_required_tools` 字段的 edict 仍能正常触发审批（由 ApprovalRequiredListRule 接手）
- 已有的 Auditor 事后审计未被影响
- AgentReAct 循环不变
- Plan review / decree 其他 action（reject/retry/amend/cancel）仍正常

- [ ] **Step 3: 完工 commit**

```bash
git add -A
git commit -m "chore(policy): manual smoke verification complete for tool policy pipeline"
```

---

## 技术债登记（未来补齐）

严格按 Spec Section 8 的 10 项测试债 + Spec "已知限制与待讨论" 6 项登记到项目 tracker：

| # | 类型 | 模块 | 优先级 |
|---|------|------|-------|
| 1 | 单元测试 | 5 条内建 PolicyRule × allow/deny/abstain | 高 |
| 2 | 单元测试 | PolicyEngine 决策算法 | 高 |
| 3 | 单元测试 | SessionRuleStore find_match + expire + revoke | 中 |
| 4 | 单元测试 | arg_fingerprint 每工具 edge cases | 高 |
| 5 | 单元测试 | PolicyProfile 展开正确性 | 高 |
| 6 | 集成测试 | registry.execute → PolicyHook → Engine 全链路 | 高 |
| 7 | 集成测试 | 审批 → SessionRule 创建 → 命中闭环 | 中 |
| 8 | 集成测试 | Engine 失败 → deny + 指标告警 | 高 |
| 9 | 集成测试 | Profile 启动 → 规则生效 → 清理 | 中 |
| 10 | E2E | Playwright Policy Timeline + Session Rules 管理 | 低 |
| 11 | 待实现 | ToolCombinationRule（需 recent_calls 历史链路） | 低 |
| 12 | 待实现 | Tainted args tracking（prompt injection 防护） | 中 |
| 13 | 待实现 | bash tier 动态判定（已知只读命令白名单） | 低 |
| 14 | 待实现 | SKILL.md tier 声明解析 | 中 |
| 15 | 待实现 | Profile 组织级共享 / 自定义模板持久化 | 低 |
| 16 | 待实现 | Redis/PG 协同 SessionRuleStore（多 agent 分布式） | 低 |

---

## Self-Review Checklist（完成所有 task 后运行）

- [ ] Spec 的 Gap A/B/C/D 和 扩展 E 每一条都有对应的 Step
- [ ] 每个 Task 都有明确的 file paths + code blocks（不是占位符）
- [ ] 手动验证清单 12 条和 task 有对应关系（上表）
- [ ] 每个 Task 以 commit 收尾
- [ ] PolicyEngine / rules / store / profile 的类型签名跨任务一致（`SessionRule.arg_fingerprint` 在 Task 3.1 和 Task 4.1 的 `_prefix_to_fingerprint` 一致使用 `"bash:"` 前缀）
- [ ] `ToolTier` 值语义在 Step 1/2/4 中统一使用 IntEnum，avoid magic numbers
- [ ] app.py 的依赖顺序保证：Storage → SessionRuleStore → ApprovalManager → PolicyEngine/PolicyHook → Executor
