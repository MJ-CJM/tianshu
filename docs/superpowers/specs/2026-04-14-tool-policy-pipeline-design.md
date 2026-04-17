# Tool Policy Pipeline — 完整权限管线（事前 + 事中 + 事后）

**Date**: 2026-04-14
**Status**: Draft
**Branch**: feat_phase3
**参考来源**: `/Users/chenjiamin/ai-tools/claude-code/claude-code/docs/share` 的《拆解 Claude Code》系列，特别是 02 篇《工具引擎与权限管线》、04 篇《多 Agent 可扩展架构》的 Hooks 与 Policy 部分。

---

## Background

Tianshu 的"都察院"（`auditor/`）在设计命名上承担了"监察百官"的职责，但当前实现**只做事后审计**：`RulesEngine` 三条规则（token 超支 / 执行 error / 空结果）+ `LLMReviewer` 对 Memorial 的事后判断。在 Phase 3 多 Agent 并发 + 插件生态即将成长的背景下，**事前权限裁决**、**事中实时审批状态机**、**决策可追溯**等"三法司中的巡按/监察"语义缺失会成为安全与稳定性的主要风险面。

参考资料对 Claude Code 的权限管线做了完整拆解，其核心结构是**事前 Tier 分层 → 多层 Policy Pipeline → 事中实时审批 → 事后审计**四段式。Tianshu 当前已有事后审计（Auditor）、事中实时审批（`executor/approvals.py`）、生命周期 Hook 体系（`executor/hooks.py`），但**事前 Policy Pipeline 完全缺失**，且 `ToolDefinition.tier` 字段已存在但在 runtime 未生效（代码中明确注释 `Phase 0: label only, no runtime interception`）。

`docs/plan/README.md` 中 OpenClaw-2（多层 Tool Policy Pipeline）标记为 Phase 1 实施但未落地，`.claude/` MEMORY 待办项 "Hooks UI 可视化" 指向 Web 前端从未消费已写入的 `hook.*` 事件。本 spec 一次性补齐这些缺口。

### 当前状态快照

经过代码走查，需要**纠正对现状的过度悲观**：

**已实现且完整**：
- `executor/hooks.py`: 10 种 `HookType`（BEFORE_AGENT_START / BEFORE_TOOL_CALL / AFTER_TOOL_CALL / LLM_INPUT / LLM_OUTPUT / AGENT_END / BEFORE_ITERATION / BEFORE_COMPACTION / SESSION_START / SESSION_END），`HookRegistry` 支持 priority 排序、5s timeout、blocking/modified_args，写 `hook.*` 事件到 events 表。
- `executor/approvals.py`: `ApprovalManager.on_before_tool_call` 已注册到 `BEFORE_TOOL_CALL`，通过 `edict.runtime.approval_required_tools` 列表触发审批，支持 approve/reject/retry/amend/cancel 五种 decree 动作。
- `auditor/`: 事后审计两层架构（Rules + LLM Reviewer），policy 字段 never/always/on_failure/on_flag。

**缺失或未生效（本 spec 覆盖）**：
- **缺口 A**: 多层 Tool Policy Pipeline（参数级 / workspace 边界 / 工具组合 / 决策可追溯）。
- **缺口 B**: `ToolDefinition.tier` 字段 runtime 未生效（Phase 0 遗留 IOU）。
- **缺口 C**: Session Rules 状态机（allow-once / allow-for-edict / allow-always）。
- **缺口 D**: Hooks / Policy / Approval 事件的 Web UI 可视化。
- **扩展 E**（用户追加需求）: 任务级 Policy Profile，供长任务启动前预配权限域，减少被频繁审批打断。

---

## Non-Goals（范围边界）

- ❌ 不替换 `executor/hooks.py` 和 `executor/approvals.py`，两者照旧工作，本设计在其上叠加。
- ❌ 不修改 `auditor/`（事后审计），它和事前 Policy 是互补关系。
- ❌ 不重写 LLM 调用链 / Agent ReAct 循环。
- ❌ 不做 PluginApi 级别的第三方策略注册（留给 Phase 2 的 PluginApi 扩展）。
- ❌ 不做声明式 YAML 规则加载（YAGNI，代码注入够用）。
- ❌ 不做规则模拟器 / dry-run 模式。
- ❌ 不做 Policy Profile 的组织级共享、版本管理、元审批。
- ❌ 本 spec **不写单元/集成测试**（尊重项目 "功能优先，测试最后补" 偏好），但必须留下技术债清单。

---

## Section 1: 整体架构与数据流

### 核心设计原则

1. **不替换已有基础设施** — `HookRegistry` 和 `ApprovalManager` 照旧工作，`PolicyHook` 作为 `BEFORE_TOOL_CALL` 的一个 handler（priority=50）。
2. **Tier 快路径在 Hook 之前** — T0 只读工具完全跳过 hook 链条，零开销。
3. **PolicyEngine 是 Hook 内部组件** — 对外界只暴露一个 hook handler，内部再分 SessionRuleStore / Engine 两层。
4. **决策事件统一事件流** — `policy.decision` 事件与已有 `hook.*` / `tool.approval_required` 事件并行，前端统一消费。

### 数据流

```
Agent 决定调 tool(args)
        │
        ▼
┌──────────────────────────────────────┐
│ ToolRegistry.execute(name, args)     │
│  ┌────────────────────────────────┐  │
│  │ ① Tier 快路径（新增）           │  │
│  │   tier == T0_READONLY           │──┼──→ func(**args)
│  │     → 直接 allow，跳过 Hook 链  │  │
│  │   tier >= T1                    │  │
│  │     → 进入慢路径                │  │
│  └───────────────┬────────────────┘  │
└──────────────────┼───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│ HookRegistry.run(BEFORE_TOOL_CALL)   │
│  • 已有的 hook handlers 照常跑       │
│  • 新增 PolicyHook (priority=50)     │
│      ② 查 SessionRuleStore cache     │
│      ③ 未命中 → PolicyEngine         │
│          .evaluate(ctx)               │
│          → allow / deny / ask         │
│      ④ 写 "policy.decision" 事件     │
│      ⑤ ask → ApprovalManager         │
│          （已有的实时审批走向）       │
└──────────────────┬───────────────────┘
                   │ allow
                   ▼
               func(**args)
                   │
                   ▼
         HookRegistry.run(AFTER_TOOL_CALL)
                   │
                   ▼
              ToolResult
```

### 改动范围

- 改动：`tools/registry.py`（tier 快路径入口）、`tools/types.py`（新增 `ToolTier` enum）、`executor/approvals.py`（与 PolicyHook 协作，移除 approval_required_tools 的入口判断）。
- 新增：`tools/policy.py`（PolicyEngine + 数据模型）、`tools/policy_rules/`（内建规则集）、`tools/policy_store.py`（SessionRuleStore）、`executor/policy_hook.py`（PolicyHook handler）、`web/routes/policy.py`（Web API）。
- 前端新增：Policy Timeline 面板、Policy Decisions Tab、Session Rules 管理页、Policy Profile 配置面板。
- 不动：`executor/hooks.py`、`auditor/`、LLM 调用链、Agent ReAct 循环。

---

## Section 2: Tier 快路径

### Tier 定义（语义契约）

| Tier | 含义 | 约束 | 快路径策略 |
|------|------|------|-----------|
| **T0_READONLY** | 只读 / 无副作用 | 不修改任何外部状态、不写文件、不发网络、不执行 shell | 直接 allow（零开销） |
| **T1_WORKSPACE** | workspace 内写 | 修改 workspace 内文件或内存状态 | 进入慢路径（PolicyEngine 验证路径边界） |
| **T2_WRITE** | 外部写 / 可逆副作用 | 写 workspace 外文件、发幂等网络请求、修改数据库 | 慢路径 + 默认需审批 |
| **T3_DANGEROUS** | 危险 / 不可逆 | 执行 shell、删除、force push、外发消息 | 慢路径 + 必须审批 |

**关键决定**: 只有 T0 走快路径。T1 看似应该安全，但 `edit_file(path="/etc/passwd")` 就能戳破 tier=T1 的假设 — 所以 T1 进慢路径由 PolicyEngine 做路径边界检查。这保持了"快路径 = 零副作用 = 常量时间"的心智契约不被污染。

### ToolTier Enum

新增 `tools/types.py`:

```python
from enum import IntEnum

class ToolTier(IntEnum):
    T0_READONLY = 0
    T1_WORKSPACE = 1
    T2_WRITE = 2
    T3_DANGEROUS = 3
```

保留 `ToolDefinition.tier: int` 字段类型不变（backward compat），代码里改用 `ToolTier` 常量。

### 快路径实现

`tools/registry.py::ToolRegistry.execute` 头部，在 `jsonschema.validate` 之后、`before hooks` 循环之前：

```python
# 伪代码
if defn.tier == ToolTier.T0_READONLY:
    # 快路径：T0 无副作用工具直接执行，跳过 HookRegistry
    # 仍然走 ToolRegistry 自身的 _hooks（老的 ToolHook 接口），保持向后兼容
    result = await func(**args)
    return result
# 慢路径：T1/T2/T3 继续走下面的 hook 链
```

### 内建工具的 tier 分配

| 工具 | Tier | 理由 |
|------|------|------|
| `list_dir` | T0_READONLY | 只读 |
| `find_files` | T0_READONLY | 只读 |
| `grep` | T0_READONLY | 只读 |
| `memory_search` | T0_READONLY | 只读 |
| `memory_tools.*`（读） | T0_READONLY | 只读 |
| `edit_file` | T1_WORKSPACE | 写 workspace，由 PolicyEngine 校验路径 |
| `memory_tools.*`（写） | T1_WORKSPACE | 写记忆 |
| `skill_tools.list_skills` | T0_READONLY | 只读 |
| `skill_tools.invoke` | 视 SKILL.md 声明 | 从 SKILL.md 解析，默认 T1 |
| `bash` / `shell`（如有） | T3_DANGEROUS | 危险 |

**Tier 缺失惩罚性默认**: 任何未声明 tier 的工具，runtime 视为 T3_DANGEROUS，并写 ERROR 日志。这是为了强制开发者在注册工具时显式声明 tier。

### 配置覆盖能力

用户在 `edict.runtime` 里可以为特定任务提升某个工具的 tier（把 `edit_file` 从 T1 提到 T3 强制审批），但**不能降级**（安全单向）。由 `TierEscalationRule`（Section 3）实现。

---

## Section 3: PolicyEngine 与规则模型

### 核心数据模型

新增 `tools/policy.py`，全部 frozen dataclass：

```python
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的历史记录，用于组合策略（未来扩展）。"""
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
    memorial: "Memorial"
    workspace_root: Path
    iteration: int
    recent_calls: tuple[ToolCallRecord, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    """策略决策结果。"""
    verdict: Literal["allow", "deny", "require_approval"]
    rule_id: str                      # 谁决定的，决策可追溯
    reason: str                       # 人类可读理由
    metadata: dict[str, Any] = field(default_factory=dict)


class PolicyRule(Protocol):
    rule_id: str
    priority: int                     # 高优先级先跑

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        """返回 None 表示弃权（让下一条规则决定）。"""
        ...
```

### 决策算法

`PolicyEngine.evaluate(ctx)` 核心逻辑：

1. 规则按 priority 降序执行。
2. 遇到 `deny` 或 `require_approval` **立即短路**返回。
3. `allow` 不短路（允许后面更严格的规则覆盖）。
4. `None` 即弃权，继续下一条规则。
5. 所有规则都弃权 → 返回默认 `allow`。
6. 规则抛异常 → 降级为弃权 + 写 WARNING 日志 + 指标计数（避免规则 bug 锁死系统）。
7. 单条规则超时（> 1s）→ 弃权 + WARNING。
8. PolicyEngine 整体超时（> 3s）→ **deny** + ERROR（fail-secure）。
9. PolicyEngine 自身抛异常 → **deny** + ERROR（fail-secure）。

### 内建规则集（初版 5 条）

| # | 规则 | priority | 作用 |
|---|------|---------|------|
| 1 | **TierEscalationRule** | 100 | 检查 `edict.runtime.tier_overrides`，允许单次任务将工具提到更高 tier（只能提升，不能降级） |
| 2 | **WorkspaceBoundaryRule** | 90 | 检查 `path` / `cwd` / `file_path` 类参数是否在 `workspace_root` 下；越界 → **deny**（硬约束，不走审批） |
| 3 | **BashSafetyRule** | 80 | 针对 T3 shell 类工具的黑名单：`rm -rf /`, `sudo`, 管道到 sh, `dd`, force push 等 → **deny**；未命中 → `require_approval`（T3 默认） |
| 4 | **ApprovalRequiredListRule** | 70 | **向后兼容** — 保留 `edict.runtime.approval_required_tools` 语义，命中 → `require_approval`。让现有 approval 逻辑**收敛到 PolicyEngine**，`ApprovalManager.on_before_tool_call` 退化成 pure UI 交互层 |
| 5 | **DefaultTierRule** | 10 | 兜底：T2 默认 `require_approval`，T3 必 `require_approval`，其他 `allow` |

**初版不做**：`ToolCombinationRule`（检测 "git reset + git push" 等组合），因为初版还没有 `recent_calls` 的 history 存储链路，引入会增加 data flow 改动。留给第二迭代。

### 扩展点

- 内建规则在 `PolicyEngine` 构造时注入（代码注入，初版够用）。
- 第三方通过 Phase 2 的 `PluginApi.register_policy_rule` 注册（未来）。
- 声明式 YAML 规则（YAGNI，初版不做）。

### 关键收益

决策可追溯 — 每个 `PolicyDecision` 都带 `rule_id` + `reason`，写入 `policy.decision` 事件后，前端能显式展示"这次为什么被拒/被批/要审批"。这是缺口 A 的核心价值。

---

## Section 4: Session Rules（allow-once / allow-for-edict / allow-always）

### 定位

Session Rules 是 PolicyEngine 的**信任缓存层**，不是独立决策层。它让 `require_approval` 在用户批准后"被记住"，避免同一类调用反复打断用户。**Reactive 模式** — 由 approval 动作驱动创建。Section 5 的 Policy Profile 是它的 **proactive 补充**。

### 三种 scope

| Scope | 有效范围 | 持久化 | 写入时机 |
|-------|---------|--------|---------|
| `edict` | 本次任务剩余调用 | in-memory | `decree.grant_scope = "edict"` |
| `always` | 跨 session 持久 | SQLite `session_rules` 表 | `decree.grant_scope = "always"` |
| `once`（隐式） | 本次调用 | 不写 rule | `decree.grant_scope` 省略 |

### 数据模型

```python
@dataclass(frozen=True)
class SessionRule:
    rule_id: str                      # UUID
    tool_name: str
    arg_fingerprint: str              # 参数指纹
    scope: Literal["edict", "always"]
    edict_id: str | None              # scope="edict" 时必填
    granted_at: datetime
    granted_by_decree_id: str | None  # 系统生成时为 None（Section 5）
    source: Literal["approval", "profile", "manual"]
    reason: str
    expires_at: datetime | None       # always scope 默认 30 天


class SessionRuleStore(Protocol):
    async def create(self, rule: SessionRule) -> None: ...
    async def find_match(
        self, tool_name: str, args: dict, edict_id: str | None
    ) -> SessionRule | None: ...
    async def list_by_scope(
        self, scope: str, edict_id: str | None = None
    ) -> list[SessionRule]: ...
    async def revoke(self, rule_id: str) -> None: ...
```

**实现**：`InMemorySessionRuleStore`（edict scope）+ `SqliteSessionRuleStore`（always scope，通过 Storage 层）。

### arg_fingerprint 算法（每工具自定义）

| 工具 | 指纹算法 | 覆盖范围 |
|------|---------|---------|
| `edit_file` | `dirname(path)` | 同一目录下的所有编辑 |
| `bash` | `command.split()[0:2]` 的 join | 覆盖到 `git push` 级别（而非整个 `git`） |
| `memory_tools.*` | `tuple(sorted(filter_keys(args)))` | 参数等价判定 |
| 默认 | `hash(frozenset(args.items()))` | 严格相等匹配 |

**关键约束 — bash 不允许 always scope**: BashSafetyRule 在 `SessionRule.create` 时拒绝 `tool_name="bash"` + `scope="always"` 的组合（避免 "allow always bash" 的灾难）。仅允许 once / edict。UI 展示时在审批面板上明确"This tool cannot be granted always scope"。

### PolicyEngine 集成流程

```
PolicyEngine.evaluate(ctx)
    ↓
initial_decision = 走 5 条规则
    ↓
if initial_decision.verdict == "require_approval":
    rule = SessionRuleStore.find_match(
        tool_name, args, edict_id
    )
    if rule:
        emit "policy.session_rule_matched" event
        return PolicyDecision(
            verdict="allow",
            rule_id=f"session_rule:{rule.rule_id}",
            reason=f"matched session rule (scope={rule.scope}, source={rule.source})"
        )
    # 未命中 → 保持 require_approval
return initial_decision
```

### Decree 扩展

`models/decree.py` 新增：
- `grant_scope: Literal["once", "edict", "always"] | None`（默认 None = once）
- `grant_reason: str | None`

`ApprovalManager._handle_approve` 检查 `grant_scope`，如果不是 `once` 则写入 `SessionRuleStore`，并在写入前执行 BashSafetyRule 的拒写检查（bash + always）。

### 治理能力

- CLI / Web UI 列出、撤销所有 `always` scope 的 session rules。
- `always` rule 默认过期时间 30 天（避免僵尸规则）。
- 每次 session rule 命中都写 `policy.decision` 事件（verdict=allow, rule_id=session_rule:xxx），完全可审计。

---

## Section 5: 任务级 Policy Profile（扩展 E，长任务预配）

### 定位

Section 4 的 Session Rules 是 **reactive**（approval 后记住）。Policy Profile 是 **proactive**（任务启动前预配一批 edict-scope 规则），解决长任务被频繁审批打断的问题。两者共用底层 SessionRuleStore，是同一张表里的不同 `source`。

### 数据模型

```python
@dataclass(frozen=True)
class PolicyProfile:
    """任务级权限预配，在 edict 启动时转化为 edict-scope session rules。"""
    allowed_paths: tuple[str, ...]              # 扩大 workspace_boundary，支持 fnmatch glob
    allowed_bash_prefixes: tuple[str, ...]      # bash 命令前缀白名单
    tier_overrides: dict[str, ToolTier]         # 工具 tier 提升（只能提升）
    auto_approve_max_tier: ToolTier             # 自动批的上限（默认 T1）
    expires_after: "timedelta | None" = None    # 默认随任务结束
    template_name: str | None = None            # 来自哪个预设模板
```

### Edict 集成

`models/edict.py` `EdictRuntime` 新增字段：

```python
class EdictRuntime:
    ...
    policy_profile: PolicyProfile | None = None
    # 以下两个已有字段保留（backward compat），同时由 profile 可填充
    approval_required_tools: list[str] = ...
    tier_overrides: dict[str, int] = ...
```

### Profile → Session Rules 展开

Executor 启动 edict 时：

```python
if edict.runtime.policy_profile:
    profile = edict.runtime.policy_profile
    for path_glob in profile.allowed_paths:
        await store.create(SessionRule(
            rule_id=uuid(),
            tool_name="edit_file",
            arg_fingerprint=path_glob,
            scope="edict",
            edict_id=edict.id,
            granted_by_decree_id=None,           # 系统生成
            source="profile",
            reason=f"Preconfigured by policy_profile (template={profile.template_name})",
            expires_at=now() + profile.expires_after if profile.expires_after else None,
        ))
    for prefix in profile.allowed_bash_prefixes:
        await store.create(SessionRule(
            tool_name="bash", arg_fingerprint=prefix, ...,
        ))
    storage.append_event(edict.id, None, "policy.profile_applied", {
        "template_name": profile.template_name,
        "rules_created": N,
    })
```

### PolicyEngine 规则调整

- `WorkspaceBoundaryRule` 读 `profile.allowed_paths` → 在 workspace 范围上叠加。
- `BashSafetyRule` 读 `profile.allowed_bash_prefixes` → 命中白名单的命令跳过审批（**黑名单仍然生效**: `rm -rf /` 无论怎么允许都拒）。
- `DefaultTierRule` 读 `profile.auto_approve_max_tier` → tier <= max 自动 allow。

### 初版硬编码 3 个模板

| 模板 | allowed_paths | allowed_bash_prefixes | auto_approve_max_tier |
|------|--------------|----------------------|----------------------|
| `safe-explore` | `()` | `()` | T0 |
| `refactor-in-place` | `("**/*",)` | `("git status", "git diff")` | T1 |
| `trusted-automation` | `("**/*",)` | `("git ", "pytest", "ruff", "black", "mypy")` | T2 |

初版**不做**用户自定义模板持久化（UI 里可以填自定义值，但不保存为可复用模板）。

### 硬约束（安全底线）

- ✅ Profile 只能**扩大**允许范围，**不能降级** BashSafetyRule 的黑名单。
- ✅ Profile 生成的 session rules `scope="edict"`，**禁止用 profile 生成 `always` scope**（proactive 预配不应该产生持久化信任）。
- ✅ Profile 生成的每条 rule 都带 `source="profile"` + `reason` 明确来源，便于撤销与审计。
- ✅ Profile 只能在**创建 edict 时**指定，不能运行中改。
- ✅ Profile 的 path glob 使用 `fnmatch` 风格（`**/*`、`src/*.py`），不支持完整正则。

### 与 Session Rules 管理页的交互

- Session Rules 管理页列表新增 `Source` 列，标明 `approval` / `profile` / `manual`。
- 来自 profile 的规则按 `edict_id` 分组折叠。
- 点击 profile 生成的规则可跳转到对应 edict 的 "Policy Profile" 配置查看。

---

## Section 6: 事件可视化与 Web UI

### 事件类型清单

后端复用已有的 `storage.append_event` 和 WebSocket 推送机制（recent commit `WebSocketStreamCallback for real-time streaming`），只新增事件类型：

| 事件 | 状态 | 写入者 | 用途 |
|------|------|-------|------|
| `policy.decision` | 新增 | PolicyEngine | 每次决策（verdict + rule_id + reason） |
| `policy.session_rule_created` | 新增 | ApprovalManager / Executor | 审批或 profile 生成规则时 |
| `policy.session_rule_matched` | 新增 | PolicyEngine | session rule 命中时 |
| `policy.session_rule_revoked` | 新增 | Web/CLI | 用户撤销 rule 时 |
| `policy.profile_applied` | 新增 | Executor | 启动 edict 时展开 profile |
| `hook.before_tool_call` | 已有（未展示） | HookRegistry | — |
| `hook.after_tool_call` | 已有（未展示） | HookRegistry | — |
| `tool.approval_required` | 已有（未展示） | ApprovalManager | — |
| `decree.approved/rejected/...` | 已有（未展示） | ApprovalManager | — |

### payload schema 示例

```json
{
  "event_type": "policy.decision",
  "edict_id": "edict_abc",
  "memorial_id": "mem_xyz",
  "timestamp": "2026-04-14T10:00:00Z",
  "payload": {
    "tool_name": "edit_file",
    "tool_tier": "T1_WORKSPACE",
    "verdict": "deny",
    "rule_id": "workspace_boundary",
    "reason": "path /etc/passwd is outside workspace /home/alice/proj",
    "iteration": 3,
    "args_summary": {"path": "/etc/passwd"}
  }
}
```

`args_summary` 初版截断到每字段前 200 字符，初版**不做脱敏**（留给自定义脱敏 hook，未来）。

### 前端展示点（4 个，scope 严格控制）

#### 1. Edict 详情页 → 新增 "Policy Timeline" 面板

- 按时间顺序合并展示 `policy.*` + `hook.*` + `tool.approval_required` + `decree.*` 事件。
- 每条显示：时间戳 · 工具名 · verdict 标签（allow=绿 / deny=红 / require_approval=橙）· rule_id · reason（悬浮展开）。
- 高亮 deny / require_approval 事件。

#### 2. AuditDashboard → 新增 "Policy Decisions" Tab

- 统计卡片：今日 allow / deny / approved / rejected 数量。
- 最近 N 条被拦截的工具调用（按 rule_id 可分组）。
- 点击跳转到对应 edict 详情。

#### 3. Session Rules 管理页（新增独立页面）

- 列出所有 `always` scope rules：tool_name · fingerprint · source · granted_at · expires_at · edict_id。
- 按 source 过滤（`approval` / `profile` / `manual`）。
- 每行一个"撤销"按钮（写 `policy.session_rule_revoked` 事件）。
- 初版不做规则创建 UI（创建只能通过 approval 或 profile）。

#### 4. 创建 Edict 表单 → "Policy Profile" 折叠面板

- 模板选择下拉（`safe-explore` / `refactor-in-place` / `trusted-automation` / `custom`）。
- 选择模板后表单预填，可继续编辑。
- Allowed Paths（多行文本，支持 glob）。
- Allowed Bash Prefixes（多行）。
- Auto Approve Max Tier（下拉 T0 / T1 / T2）。
- Expires After（可选时长）。

#### 5. 实时通知（WebSocket 推送）

- 订阅 `policy.decision` + `tool.approval_required`。
- `require_approval` 事件触发右上角 toast + 任务列表红点提示。
- 让"等待审批的任务"变得可见，不再需要用户轮询。

### 后端 API 增量

新建 `web/routes/policy.py`：

```
GET    /api/edicts/{id}/policy_events  → 该 edict 的 policy.* + hook.* 事件列表
GET    /api/policy/session_rules        → 列表 always scope rules
DELETE /api/policy/session_rules/{id}  → 撤销 rule
GET    /api/policy/stats                → 今日 allow/deny/approved 统计
GET    /api/policy/templates            → 硬编码模板列表
```

### 技术债偿还

这个 section 一次性兑现 `.claude/` MEMORY 待办 "Hooks UI 可视化" — `hook.*` 事件虽然是已有的，但前端从未消费过，Policy Timeline 面板同时展示 hook 事件就把这个缺口一并补上。

---

## Section 7: 错误处理与可观测

### 核心原则: 单点 fail-open / 整体 fail-secure

- **单条规则失败** → 弃权 + 继续（避免单条规则 bug 锁死系统）。
- **PolicyEngine 整体失败** → **deny**（如果连决策都做不了，不能让工具继续跑）。

### 失败模式清单

| 失败模式 | 默认行为 | 日志级别 | 备注 |
|---------|---------|---------|------|
| `Rule.evaluate` 抛异常 | 弃权 + 继续 | WARNING | Section 3 已定 |
| `Rule.evaluate` 超时（> 1s） | 弃权 + 继续 | WARNING | 新增规则级 timeout |
| `PolicyEngine.evaluate` 整体超时（> 3s） | **deny** | ERROR | 防止级联卡死 |
| `PolicyEngine` 自身异常 | **deny** | ERROR | fail-secure |
| `ToolDefinition.tier` 未声明/非法 | **降级到 T3** | ERROR | 强制开发者显式声明 |
| 所有规则弃权 | allow（默认） | INFO | Section 3 已定 |
| `SessionRuleStore` 查询失败 | 跳过 cache，继续走审批 | WARNING | 优雅降级 |
| `ApprovalManager` 存储失败 | `HookResult(block=True, reason="approval storage unavailable")` | ERROR | fail-secure |
| `Decree.grant_scope` 非法值 | 降级为 `once` | WARNING | 避免坏 payload 污染 |
| Hook handler 超时（5s） | 已有机制：记录 + 继续 | WARNING | 复用 `HOOK_TIMEOUT` |

### 超时预算（层级清晰）

```
Approval 层:      5 分钟     (已有 APPROVAL_TIMEOUT = 300.0)
Hook handler 层:  5 秒       (已有 HOOK_TIMEOUT = 5.0)
PolicyEngine 层:  3 秒       (新增 POLICY_ENGINE_TIMEOUT = 3.0)
单条 Rule 层:     1 秒       (新增 POLICY_RULE_TIMEOUT = 1.0)
```

每层独立超时，短的先触发。

### 日志策略

- `DEBUG`: 每条规则的 evaluate 入参和输出（开发期诊断）。
- `INFO`: 每次 `PolicyDecision` 结果（含 verdict / rule_id / reason）。
- `WARNING`: 规则弃权异常、session rule 降级、grant_scope 非法。
- `ERROR`: PolicyEngine 整体 deny、Tier 缺失降级、存储异常。

### 可观测指标

初版用结构化日志记录（Phase 3 再接 Prometheus / OTLP）：

```
policy_decision_total{verdict, rule_id}          # 决策计数
policy_rule_eval_duration_seconds{rule_id}       # 规则执行耗时
policy_session_rule_hits_total                   # session rule 命中次数
policy_deny_due_to_engine_error_total            # 引擎失败导致的 deny（告警用）
tool_approval_pending_total                      # 当前待审批数
tool_approval_timeout_total                      # 审批超时次数
policy_profile_applied_total{template_name}      # profile 展开次数
```

**关键告警**: `policy_deny_due_to_engine_error_total > 0` 必须告警 — 这是引擎自身故障信号，不告警会让用户误以为是策略正常拒绝。

### 降级回退路径总表

```
PolicyEngine fail   → deny tool call
SessionRuleStore fail → skip cache, continue to approval
ApprovalManager fail → block tool call with error
Tier missing        → treat as T3 (most dangerous)
Rule exception      → skip rule, continue pipeline
Decree payload bad  → downgrade grant_scope to "once"
```

每一个降级都有日志 + 指标 + 事件，**没有任何静默失败**。

---

## Section 8: 测试策略与技术债登记

### 核心决定

按项目 `.claude/` MEMORY 偏好 "功能优先，测试最后补"，本 spec **不写单元/集成测试**，但必须满足两个前提：

1. **代码写成可测试的形态**（设计时买的单）。
2. **留下清晰的"应补测试清单"**（技术债显式登记）。

### 可测试性设计（现在就要遵守的约束）

- `PolicyEngine.evaluate(ctx)` 是纯异步函数，无全局状态依赖 → 测试时直接喂 `PolicyContext`。
- `PolicyRule` 是 Protocol → fake rule 可任意注入。
- `PolicyContext` / `PolicyDecision` / `SessionRule` 全部 `frozen dataclass` → 测试数据构造零心智负担。
- `SessionRuleStore` 是 Protocol，提供 `InMemorySessionRuleStore` + `SqliteSessionRuleStore` 两种实现 → 测试默认用 in-memory。
- 所有时间相关字段接受可注入的 `clock: Callable[[], datetime]` → 测试时间敏感逻辑不用 sleep。
- PolicyEngine 依赖注入而非 global singleton → 测试可独立构造。

### 手动验证清单（本次实施完必须 dogfood）

| # | 场景 | 期望行为 | 验证位置 |
|---|------|---------|---------|
| 1 | T0 工具 `list_dir` 调用 | 快路径放行，无 `policy.decision` 事件 | events 表 + 日志 |
| 2 | T1 `edit_file` workspace 内 | 慢路径 allow，rule_id=`workspace_boundary` | Policy Timeline |
| 3 | T1 `edit_file` 越界（`/etc/passwd`） | **deny**（非 approval），任务 fail | Policy Timeline + memorial.error |
| 4 | T3 `bash ls` | `require_approval`，UI toast 弹审批 | AuditDashboard |
| 5 | 审批勾选 "always" → 同类再调 | 第二次直接 allow，rule_id=`session_rule:xxx` | Policy Timeline |
| 6 | Session Rules 页撤销规则 | 第三次同类调用重新弹审批 | 管理页 + 新审批 |
| 7 | 手动引入 `raise Exception` 到某条规则 | 弃权 + 继续，工具正常跑，WARNING 日志 | 日志 |
| 8 | 手动引入 `raise Exception` 到 Engine | **deny**，工具失败，ERROR 日志 + 指标 +1 | 日志 |
| 9 | 未声明 tier 的新工具 | 默认 T3，require_approval | Policy Timeline |
| 10 | `bash` + `grant_scope=always` | **拒绝写入 rule**，仅当次放行 | WARNING 日志 |
| 11 | 启动带 `refactor-in-place` 模板的 edict | `policy.profile_applied` 事件，后续 edit_file 不弹审批 | Policy Timeline |
| 12 | Profile `allowed_bash_prefixes=("git push",)` 后跑 `git push origin main` | 放行；跑 `rm -rf /` | 前者 allow，后者 deny（BashSafetyRule 黑名单仍生效） |

### 未来补齐的测试点清单（技术债登记）

| # | 类型 | 目标模块 | 优先级 | 最小 case 数 |
|---|------|---------|-------|-----------|
| 1 | 单元 | 每条内建 `PolicyRule` 的 allow/deny/abstain 矩阵 | 高 | 5 规则 × 3 结果 = 15 |
| 2 | 单元 | `PolicyEngine.evaluate` 决策算法（短路 / 弃权 / 异常 / 超时） | 高 | 8 |
| 3 | 单元 | `SessionRuleStore.find_match` + 过期 + 撤销 | 中 | 6 |
| 4 | 单元 | `arg_fingerprint` 每工具的指纹算法 | 高 | 每工具 3 个 edge case |
| 5 | 单元 | `PolicyProfile` 展开为 session rules 的正确性 | 高 | 6 |
| 6 | 集成 | `registry.execute` → `PolicyHook` → `PolicyEngine` 完整决策流 | 高 | 10 |
| 7 | 集成 | 审批 → SessionRule 创建 → 下次命中闭环 | 中 | 4 |
| 8 | 集成 | PolicyEngine 失败 → deny + 指标告警 | 高 | 3 |
| 9 | 集成 | Profile 启动 → 规则生效 → 任务结束后规则清理 | 中 | 4 |
| 10 | E2E | Policy Timeline + Session Rules 管理页（Playwright） | 低 | 5 |

### 覆盖率目标（补齐时）

- `policy.py` / rules：**95%+**（安全核心，严苛要求）。
- `SessionRuleStore`：**85%+**。
- Web UI：Playwright E2E 走关键路径，不追求 coverage 数字。

---

## 实施顺序

四段式依赖链，每段独立可 ship：

### Step 1: Tier runtime 生效（缺口 B）

- 改动：`tools/types.py` 新增 `ToolTier` enum；`tools/registry.py::execute` 头部加 T0 快路径；为所有内建工具补 tier 声明。
- 验收：手动验证清单第 1 条通过。
- 依赖：无。

### Step 2: PolicyEngine + 内建规则集（缺口 A）

- 改动：新增 `tools/policy.py`（数据模型 + Engine）、`tools/policy_rules/` 目录（5 条内建规则）、`executor/policy_hook.py`（PolicyHook handler）；在 `HookRegistry` 构造时注册。
- `executor/approvals.py::on_before_tool_call` 移除 "检查 approval_required_tools" 的入口判断，只保留 UI 交互（等 decree / 解锁 asyncio.Event）。
- 验收：手动验证清单第 2、3、4、7、8、9 条通过。
- 依赖：Step 1。

### Step 3: Session Rules（缺口 C）

- 改动：新增 `tools/policy_store.py`（`SessionRuleStore` Protocol + 两种实现）；`models/decree.py` 扩展 `grant_scope` + `grant_reason`；`ApprovalManager._handle_approve` 写入 rule；`PolicyEngine.evaluate` 集成查询。
- 新增 Storage 层 `session_rules` 表及对应 CRUD 方法。
- 验收：手动验证清单第 5、6、10 条通过。
- 依赖：Step 2。

### Step 4: Policy Profile（扩展 E）

- 改动：`tools/policy.py` 新增 `PolicyProfile` 数据类；`models/edict.py::EdictRuntime` 新增 `policy_profile` 字段；`Executor.start` 加 profile 展开逻辑；内建 `PolicyEngine` 规则读取 profile。
- 硬编码 3 个模板在 `tools/policy.py::BUILTIN_TEMPLATES`。
- 验收：手动验证清单第 11、12 条通过。
- 依赖：Step 3。

### Step 5: Web UI 消费（缺口 D）

- 改动：新建 `web/routes/policy.py`；前端新增 Policy Timeline 面板、Policy Decisions Tab、Session Rules 管理页、Policy Profile 配置面板、实时通知 toast。
- 与 Step 2-4 并行开发（API 契约先定），Step 2 完成后可独立联调。
- 验收：全部前端组件在本地 dev server 能跑通手动验证清单对应的 UI 交互。
- 依赖：Step 2（为消费 `policy.decision` 事件）。

---

## 已知限制与待讨论

- **规则组合策略**: `ToolCombinationRule`（检测 "git reset + git push" 等）未在初版实现，需要 `recent_calls` 历史存储链路，留给第二迭代。
- **Prompt injection 攻击面**: 如果 agent 的 args 被用户输入污染，比如 `edit_file(path="${input}")`，当前 WorkspaceBoundaryRule 只检查最终解析的 path，无法识别来源。需要 future 的 "tainted args tracking"。
- **多 Agent 并发的 Session Rule 共享**: 当前 `InMemorySessionRuleStore` 是进程内状态，Phase 3 分布式场景下多 agent 共享需要 Redis 或 PG 协同。
- **Profile 的组织级共享**: 初版只作用于单个 edict，多用户共享常用 profile 需要 Phase 2 的 PluginApi 扩展。
- **`bash` 工具 tier 的动态判定**: 某些 bash 命令本质只读（`ls`、`cat`），当前统一 T3。可以通过 `BashSafetyRule` 的 "已知只读命令白名单" 自动降级为 allow，但会让 BashSafetyRule 变复杂。留给第二迭代。
- **Tier 从 SKILL.md 声明**: 技能调用工具的 tier 解析目前是占位（默认 T1），需要 SKILL.md 格式扩展，和 Phase 2 的 skill 系统一起做。
