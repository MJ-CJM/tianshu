# Hermes-Inspired Enhancements — Self-Improving Skill System & Stability

**Date**: 2026-04-09
**Status**: Draft
**Branch**: feat_phase3

## Background

Hermes-agent 的核心竞争力不是技术堆砌，而是"persistent self-improving agent"的产品叙事。Tianshu 保持自己的"六部制治理"差异化定位，但从 hermes 中提取经过验证的子系统来增强竞争力。

**优先级排序**：
1. Skill Learning Loop（C1→C2→C3）
2. Fallback Model
3. Streaming + 可中断调用
4. Cross-session 记忆检索
5. 多端 Gateway 扩展（仅记录方向，不在本次实施）

---

## C1: `skill_manage` 工具 + 渐进式加载

### 目标

将 skill 系统从"静态全量注入"升级为"索引 + 按需加载 + agent 可写"，降低 token 消耗，同时让 agent 具备自主创建和改进 skill 的能力。

### 渐进式加载

**现状**：`SkillsLoader.load_all()` 把所有 skill 全文拼接注入 system prompt，30K char_budget 限制下能装的 skill 很少，且大部分与当次任务无关。

**改造**：System prompt 只注入 skill 索引（name + description），agent 按需通过工具加载全文。

System prompt 注入格式变更：

```
# Available Skills
Use skill_list() to see all skills. Use skill_view(name) to load full content.

<skills_index>
- file-ops: File read/write operations guidance
- shell: Shell command execution guidance
- deployment-checklist: Step-by-step deployment process
</skills_index>

If a skill matches your current task, load it with skill_view().
After completing a difficult task, consider saving reusable approaches as a new skill.
```

**`always: true` 的 skill 仍然全文注入**（向后兼容）。

### 新增工具

#### `skill_list`（T0 只读）

```
Parameters:
  category: string | null    — 按分类过滤（可选）
  include_dormant: bool      — 是否包含休眠 skill（默认 false，C3 阶段生效）

Returns:
  [
    {"name": "file-ops", "description": "...", "source": "builtin", "status": "healthy"},
    {"name": "shell", "description": "...", "source": "builtin", "status": "healthy"},
    ...
  ]
```

#### `skill_view`（T0 只读）

```
Parameters:
  name: string               — skill 名称

Returns:
  {"name": "...", "content": "...(full SKILL.md content)", "source": "...", "metrics": {...}}
```

调用时同步更新 `skill_metrics.usage_count`（C3 阶段生效）。

#### `skill_manage`（T2 需审批）

```
Parameters:
  action: "create" | "edit" | "patch" | "delete" | "activate"
  name: string
  content: string | null     — create/edit 时必填
  patch_old: string | null   — patch 时必填
  patch_new: string | null   — patch 时必填

Notes:
  - activate: 将休眠 skill 重新激活（C3 阶段生效）
```

**安全约束**：
- Tier T2，在 `BEFORE_TOOL_CALL` hook 中可被审批拦截
- 只能写入 user_dir (`~/.tianshu/skills`) 或 workspace skills，不能改 builtin
- 名称校验：`^[a-z0-9][a-z0-9._-]{0,63}$`
- 内容限制：沿用 256KB
- 写入前经过 `SkillValidator` 校验（C2 阶段实现）

**Agent 创建 skill 时的 frontmatter 格式**：

```yaml
---
name: deployment-checklist
description: "Step-by-step deployment process for this project"
metadata:
  tianshu:
    created_by: agent
    created_at: "2026-04-09"
    source_edict_id: "xxx"
---
(skill content)
```

### SkillsLoader 变更

新增方法：

```python
def load_index(self, filter_names: list[str] | None = None,
               include_dormant: bool = False) -> str:
    """Return skill index (name + description only) for system prompt injection."""
    ...

def patch_skill(self, name: str, old: str, new: str) -> dict:
    """Find-and-replace within a skill's content."""
    ...
```

`load_all()` 保留但仅用于 `always: true` skill 的全文注入。

### 文件变更

| 文件 | 变更 |
|------|------|
| `skills/loader.py` | 新增 `load_index()`、`patch_skill()` |
| 新增 `tools/skill_tools.py` | 注册 `skill_list`、`skill_view`、`skill_manage` 工具 |
| `executor/agent.py` | `_build_system_prompt` 改为索引模式 |
| `persona/prompt_builder.py` | Layer 7 改为索引注入 + always skill 全文 |

---

## C2: Hook 驱动的学习触发 + 安全扫描

### 目标

Agent 执行成功后自动评估是否应沉淀 skill，利用 tianshu 现有的 Hook + EventBus 架构，不 spawn 独立 agent。

### 触发链路

```
Agent.execute() 结束
  → HookType.AGENT_END 触发
    → SkillReviewHook._should_review() 判断
      → 满足条件 → emit EventBus "skill.review_requested"
        → SkillReviewHandler 执行轻量 LLM 调用
          → create / update / skip
```

### 触发条件

```python
async def _should_review(self, context: dict) -> bool:
    # 1. 只在成功完成的任务后 review
    if context.get("exit_reason") != ExitReason.COMPLETED:
        return False

    # 2. 太简单的任务不值得（< 3 次工具调用）
    if context.get("iteration_count", 0) < 3:
        return False

    # 3. 冷却期：距上次 review 至少间隔 N 次成功任务
    if self._tasks_since_last_review < self._review_interval:
        self._tasks_since_last_review += 1
        return False

    self._tasks_since_last_review = 0
    return True
```

### 配置项

新增到 `AgentConfigState`：

```python
skill_review_interval: int = 5       # 每 5 次成功任务后触发一次 review
skill_review_enabled: bool = True     # 全局开关
```

### Review LLM 调用

**不进入 agent loop**，做一次简单的 chat completion：

**输入**（控制在 ~2K token）：
- Task goal（edict 描述）
- Exit reason + iteration count
- Tool calls summary：每个工具调用一行，格式为 `tool_name(arg1_key, arg2_key) → success/error`（只含参数 key 和结果状态，不含参数值和完整输出）
- 当前 skills index（name + description 列表）

**Prompt**：

```
Review the task execution below. Decide if any reusable approach
should be saved as a skill.

Task goal: {edict_goal}
Exit reason: {exit_reason}
Iterations: {iteration_count}
Tool calls summary: {tool_calls_summary}

Existing skills: {skills_index}

Respond in JSON:
{
  "action": "create" | "update" | "skip",
  "skill_name": "...",
  "reason": "...",
  "content": "...",
  "patch_old": "...",
  "patch_new": "..."
}

Rules:
- Only save approaches that required trial-and-error or non-obvious solutions
- Don't save trivial or one-off tasks
- If an existing skill covers this, update it instead of creating a new one
- Respond "skip" if nothing is worth saving
```

**输出约束**：`max_tokens: 4096`，单次调用。

### 安全扫描（SkillValidator）

Agent 生成的 skill 写入前必须通过校验：

```python
class SkillValidator:
    def validate(self, name: str, content: str) -> ValidationResult:
        checks = [
            self._check_name_format(name),
            self._check_frontmatter(content),
            self._check_size(content),
            self._check_no_secrets(content),
            self._check_no_shell_injection(content),
        ]
        ...
```

| 检查项 | 规则 | 处理 |
|--------|------|------|
| 名称格式 | `^[a-z0-9][a-z0-9._-]{0,63}$` | 阻断 |
| Frontmatter | 合法 YAML，name + description 必填 | 阻断 |
| 内容大小 | ≤ 256KB | 阻断 |
| 敏感信息 | 正则匹配 API key / token / password 模式 | 阻断 |
| 危险命令 | `rm -rf /`、`sudo`、`chmod 777` 等模式 | 告警（不阻断），记录日志 |

### 治理体系整合

- Skill 创建/更新事件写入 EventBus：`skill.created` / `skill.updated`
- Auditor 可订阅这些事件做异步审计
- `skill_manage` 工具 tier=T2，`BEFORE_TOOL_CALL` hook 可拦截

### 文件变更

| 文件 | 变更 |
|------|------|
| 新增 `skills/reviewer.py` | SkillReviewHandler（LLM review 逻辑 + AGENT_END hook） |
| 新增 `skills/validator.py` | SkillValidator（安全扫描） |
| `app.py` | 注册 SkillReviewHook 到 HookRegistry |
| `config_manager.py` | 新增 `skill_review_interval`、`skill_review_enabled` |

---

## C3: Skill 质量追踪 + 衰减淘汰

### 目标

Hermes 没做但应该做的部分。量化追踪 skill 使用效果，自动淘汰低质量 skill，避免 skill 膨胀和污染。这是 tianshu 相比 hermes 的差异化优势。

### 质量指标模型

```python
@dataclass(frozen=True)
class SkillMetrics:
    skill_name: str
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime | None = None
    created_by: str = "manual"    # "manual" | "agent" | "plugin"
    source_edict_id: str | None = None
```

**成功率**：`success_count / usage_count`（`usage_count >= 3` 时才有统计意义）

### 指标采集点

```
skill_view() 被调用
  → usage_count += 1
  → last_used_at = now
  → 在 Agent 实例上追踪 _active_skills: set[str]（非 LoopState，因其为 frozen）

Agent.execute() 结束
  → exit_reason == COMPLETED → _active_skills 中所有 skill 的 success_count += 1
  → exit_reason != COMPLETED → _active_skills 中所有 skill 的 failure_count += 1
  → 清空 _active_skills
```

### 衰减与淘汰规则

| 状态 | 条件 | 处理 |
|------|------|------|
| **健康** | 成功率 ≥ 60% 或 usage_count < 3 | 正常保留 |
| **警告** | 成功率 < 60% 且 usage_count ≥ 3 | `skill_list` 中标注 ⚠️ |
| **休眠** | 超过 90 天未使用 | 从索引中隐藏（文件保留） |
| **建议淘汰** | 成功率 < 30% 且 usage_count ≥ 5 | 触发 `skill.retire_suggested` 事件 |

**关键原则**：
- **不自动删除**，只自动隐藏/建议。删除权交给用户或审批流程
- `created_by: agent` 的 skill 可自动降级为休眠；手动创建的永不自动淘汰
- 休眠 skill 可通过 `skill_manage(action="activate")` 重新激活

### 存储方案

SQLite 单表，与现有 storage 层一致：

```sql
CREATE TABLE skill_metrics (
    skill_name    TEXT PRIMARY KEY,
    usage_count   INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_used_at  TEXT,
    created_at    TEXT,
    created_by    TEXT DEFAULT 'manual',
    source_edict_id TEXT
);
```

不放在 frontmatter 中，避免高频写文件引起 SkillsWatcher 抖动。

### 文件变更

| 文件 | 变更 |
|------|------|
| 新增 `skills/metrics.py` | SkillMetrics 模型 + SkillMetricsStore（SQLite CRUD） |
| `skills/loader.py` | `load_index()` 加入休眠过滤 |
| `tools/skill_tools.py` | `skill_view` 更新 usage_count；`skill_list` 支持 `include_dormant` |
| `executor/agent.py` | execute 结束时更新 active_skills 的 success/failure |
| `storage/sqlite.py` | 新增 `skill_metrics` 表 migration |

---

## Fallback Model

### 目标

主模型连续失败时自动切换备用模型，提升 agent 执行稳定性。

### 设计

- `AgentConfigState` 新增 `fallback_llm_config_name: str | None`
- Agent loop 中 LLM 调用 tenacity 重试耗尽后，检查是否有 fallback 配置
- 有则从 `ConfigManager` 获取 fallback 的 `LLMConfigState`，用其完成当前 iteration
- 下一次 edict 执行恢复主模型
- 切换事件写入 EventBus：`llm.fallback_activated`
- 在 `AgentResult.recovery_attempts` 中记录 fallback 使用情况

### 文件变更

| 文件 | 变更 |
|------|------|
| `executor/agent.py` | LLM 调用失败后 fallback 逻辑 |
| `config_manager.py` | 新增 `fallback_llm_config_name` |

---

## Streaming + 可中断调用

### 目标

Agent 执行过程实时输出 LLM 文本，支持用户中途取消。

### 设计

**StreamCallback Protocol**：

```python
class StreamCallback(Protocol):
    async def on_delta(self, text: str) -> None: ...
    async def on_tool_call_start(self, name: str) -> None: ...
    async def on_tool_call_end(self, name: str, result: ToolResult) -> None: ...
```

**CancellationToken**：

```python
class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()
```

**Agent.execute 签名扩展**：

```python
async def execute(
    self,
    edict: Edict,
    ...,
    stream_callback: StreamCallback | None = None,
    cancellation_token: CancellationToken | None = None,
) -> AgentResult:
```

**LiteLLM streaming**：使用 `stream=True` 参数，遍历 async generator 中的 delta。每个 delta 调用 `stream_callback.on_delta()`，同时检查 `cancellation_token.is_cancelled`。

**Notifier 整合**：WebSocket 连接的 Notifier 实现 `StreamCallback`，将 delta 实时推送给前端。

### 文件变更

| 文件 | 变更 |
|------|------|
| `executor/agent.py` | 主循环支持 stream + cancellation |
| 新增 `executor/streaming.py` | StreamCallback protocol + CancellationToken |
| `notifier/` | WebSocket notifier 实现 StreamCallback |

---

## Cross-session 记忆检索

### 目标

Agent 可搜索过往任务的执行记录和 memory entries，形成"长期经验"。

### 设计

新增 `memory_search` 工具（T0 只读）：

```
Parameters:
  query: string              — 搜索关键词
  limit: int = 10            — 最大返回条数
  category: string | null    — 按 memory category 过滤

Returns:
  [
    {
      "id": "...",
      "category": "insight",
      "content": "...(summary)",
      "edict_id": "...",
      "created_at": "..."
    },
    ...
  ]
```

利用现有 `memory/` 模块的 FTS 能力。返回 summary 级别内容，不返回完整对话历史。

### 文件变更

| 文件 | 变更 |
|------|------|
| 新增 `tools/memory_tools.py` | 注册 `memory_search` 工具 |
| `memory/` | 确保 FTS search API 可被工具层调用 |

---

## 多端 Gateway 扩展（方向记录）

**不在本次实施范围。** 方向：
- CLI client：轻量 Python 脚本，调用 REST API 提交 edict + 轮询结果
- Slack / Telegram：作为 `NotificationChannel` 插件，复用现有 protocol
- 独立 spec 处理

---

## 实施顺序

```
Phase  内容                        依赖      预期交付
─────  ──────────────────────────  ────────  ────────
C1     skill_manage + 渐进式加载    无        独立可用
C2     Hook 学习触发 + 安全扫描     C1        自动学习
C3     质量追踪 + 衰减淘汰          C1        质量闭环
F      Fallback Model              无        可与 C1 并行
S      Streaming + 可中断           无        可与 C1 并行
M      Cross-session 记忆检索       无        可与 C1 并行
```

C1 是基础，必须先做。F/S/M 与 C1 无依赖，可并行。C2 依赖 C1 的工具。C3 依赖 C1 的 skill_view 采集点。
