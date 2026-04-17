# Phase 4 实现文档

> 分支: `feat_phase3` | 快照日期: 2026-03-24
>
> 本文档接续 [phase1-3.md](phase1-3.md)，记录 Phase 4（Persona 体系完善与路由修复）的实现。Phase 3 建立了多 Agent 与 DAG 执行框架，但 persona 路由存在接线 bug（属性名不匹配、硬编码 fallback、静默失败）。Phase 4 修复全部路由 bug、提取 DRY 常量、创建 5 个部门 prompt 模板、调整 loader 架构使命名角色能共享部门模板。

---

## 1. 阶段概览

| Phase | 目标 | 一句话总结 |
|-------|------|-----------|
| 4 | Persona 体系完善 | 路由 bug 修复、DRY 重构、5 部门模板、loader 架构调整、前端 prompt 编辑 |

### 改动模块总表

| 模块 | 文件 | 改动类型 |
|------|------|----------|
| Persona 常量 | `persona/model.py` | 新增 `DEFAULT_EXECUTOR_ID` |
| Persona 加载 | `persona/loader.py` | 重构 seed / dict_to_persona |
| Prompt 构建 | `persona/prompt_builder.py` | 增强层级标记 |
| 记忆管理 | `memory/manager.py` | 修复属性名 + 提取共享方法 |
| 任务规划 | `planner/planner.py` | 修复 + 日志增强 |
| Agent 执行 | `executor/agent.py` | 增加 fallback 警告 |
| DAG 调度 | `executor/dag_scheduler.py` | 修复 None persona |
| 编排器 | `executor/executor.py` | 替换 magic string |
| 前端 | `web/src/pages/PersonaDetailPage.tsx` | Prompt 表格编辑列 |
| Persona 模板 | `personas/{ducha,hubu,neige,tongzheng,wenyuan}/` | 新增 5×3 文件 |

---

## 2. Bug 修复（6 项）

### 2.1 Memory recall 属性名不匹配

- **问题**: `on_before_agent_start` 中通过 `first_task.persona_id` 获取 persona ID，但 `PlanTask` 模型的字段名是 `assigned_official`
- **根因**: `memory/manager.py:366` 使用了错误的属性名，导致所有 memory recall 默认使用 `"bingbu"`
- **修复**: 改为 `first_task.assigned_official`，并增加 `first_task` 的 None 守卫

### 2.2 Agent fallback 静默丢失 persona

- **问题**: `Agent.execute()` 有两条构建 system prompt 的路径 — 主路径用 `prompt_builder`（包含 persona），fallback 路径 `_build_system_prompt()` 完全忽略 persona
- **根因**: `executor/agent.py:108` 的 fallback 方法没有 persona 参数
- **修复**: 在 fallback 分支添加 `logger.warning`，明确提示 persona 上下文丢失

```python
if persona and hasattr(persona, "id"):
    logger.warning(
        "[AGENT] Edict %s: prompt_builder unavailable, persona %s context will be lost",
        edict.id, getattr(persona, "id", "unknown"),
    )
```

### 2.3 DAG scheduler persona 解析为 None

- **问题**: 当 `node.assigned_official` 为 None 或指向不存在的 persona 时，worker 收到 `persona=None`
- **根因**: `dag_scheduler.py:197-200` 只在 `assigned_official` 非空时尝试解析，否则直接传 None
- **修复**: 增加确定性 fallback 到 `DEFAULT_EXECUTOR_ID`

```python
if persona is None and self._persona_loader:
    fallback = self._persona_loader.get(DEFAULT_EXECUTOR_ID)
    if fallback:
        persona = fallback
        logger.warning("[DAG] ... falling back to bingbu")
```

### 2.4 Memory on_agent_end 硬编码 bingbu

- **问题**: `on_agent_end` 中 `persona_id = "bingbu"` 后仅从 context 取 persona，但上游 bug（#3、#5）导致 persona 经常为 None
- **修复**: 通过修复上游 bug + 提取 `_resolve_persona_id()` 共享方法统一解决（见 §3.2）

### 2.5 PersonaLoader 静默跳过不完整目录

- **问题**: `_load_persona_from_dir()` 在 SOUL.md 或 ROLE.md 缺失时仅输出模糊 warning
- **修复**: 明确列出缺失的文件名和完整路径

```python
logger.warning(
    "Persona '%s' missing %s — skipping. "
    "Create these files in %s to activate this persona.",
    persona_dir.name, " and ".join(missing), persona_dir,
)
```

### 2.6 Planner 分配验证不记录日志

- **问题**: `_validate_assignments()` 对无效 persona 进行重分配，但不记录日志
- **修复**: 添加 reassignment trace + 最终分配汇总日志

```python
logger.info("Task %s: persona reassigned %s → %s (selector match)", ...)
logger.info("Plan assignments finalized: %s", assignments)
```

---

## 3. 去重复重构（2 项）

### 3.1 DEFAULT_EXECUTOR_ID 常量

**文件**: `persona/model.py`

```python
DEFAULT_EXECUTOR_ID = "bingbu"
"""Default persona ID used as fallback when no specific persona is assigned."""
```

**替换范围**:

| 文件 | 替换处 |
|------|--------|
| `executor/executor.py:170` | memorial 的 persona_id fallback |
| `planner/planner.py:249` | `_passthrough_plan()` 默认参数 |
| `planner/planner.py:299,301` | `_validate_assignments()` fallback |
| `executor/dag_scheduler.py:203` | DAG 节点 persona fallback |
| `memory/manager.py` | 通过 `_resolve_persona_id()` 间接引用 |

> `selector.py`、`access_control.py`、`storage.py`、`prompts.py` 中的 `"bingbu"` 是数据定义或 prompt 示例，保留原样。

### 3.2 _resolve_persona_id() 共享方法

**文件**: `memory/manager.py`

```python
@staticmethod
def _resolve_persona_id(context: dict, plan: object = None) -> str:
    """Extract persona ID with consistent fallback chain.

    Priority: context["persona"].id > plan.tasks[0].assigned_official > DEFAULT_EXECUTOR_ID
    """
```

统一了 `on_before_agent_start` 和 `on_agent_end` 中原本不一致的 persona 解析逻辑（前者 3 层 fallback，后者 2 层）。

---

## 4. 功能增强（5 项）

### 4.1 Prompt 层级标记

**文件**: `persona/prompt_builder.py`

在 system prompt 的每个 persona 层前添加 Markdown 标题：

| 层 | 标记 |
|----|------|
| Layer 2 | `# Court Protocol` |
| Layer 3 | `# Persona Identity [{persona.id}]` |
| Layer 4 | `# Role Specification` |

便于 prompt 调试和 LLM 识别层级边界。

### 4.2 Planner 分配日志

**文件**: `planner/planner.py`

`_validate_assignments()` 完成后输出：
```
INFO  Plan assignments finalized: {'main': 'ducha', 'research': 'neige', ...}
```

### 4.3 Memory 目录自动初始化

**状态**: 已预存在于 `app.py:225`（`memory_manager.ensure_memory_dirs()`），Phase 4 确认无需额外改动。

### 4.4 Selector 智能重分配

**文件**: `planner/planner.py`

无效 persona 分配时使用 `selector.select_for_task(description)` 基于任务描述关键词匹配最佳部门，而非简单回退到 bingbu。

### 4.5 Agent fallback 警告

**文件**: `executor/agent.py`

prompt_builder 不可用时输出 `WARNING` 日志，消灭"静默失败"。

---

## 5. Persona 模板体系

### 5.1 模板总表

| 部门 | 目录 | 角色 | 核心职责 | tool_tier_max | 可委派至 |
|------|------|------|---------|---------------|---------|
| 都察院 (Censorate) | `personas/ducha/` | 都御史 | 代码审查、质量检查、安全审计 | 1 | — |
| 内阁 (Grand Secretariat) | `personas/neige/` | 内阁首辅 | 战略规划、技术调研、任务分解 | 1 | bingbu, ducha, wenyuan |
| 文渊阁 (Grand Archive) | `personas/wenyuan/` | 文渊阁大学士 | 文档编写、知识管理 | 1 | — |
| 通政司 (Office of Transmission) | `personas/tongzheng/` | 通政使 | 跨部门协调、状态报告、通知 | 1 | — |
| 户部 (Ministry of Revenue) | `personas/hubu/` | 户部尚书 | 成本管理、预算监控、Token 追踪 | 1 | — |

> 兵部 (`personas/bingbu/`) 在 Phase 1 已创建，此处不重复。

### 5.2 目录结构

```
personas/
├── court/
│   ├── COURT.md          # 朝廷共享上下文（Layer 2）
│   └── MEMORY.md         # 朝廷共享记忆（Layer 6）
├── bingbu/               # 兵部 — 任务执行（Phase 1）
├── ducha/                # 都察院 — 代码审查（Phase 4 新增）
│   ├── SOUL.md           # 身份定义 + frontmatter 元数据
│   ├── ROLE.md           # 职责规范
│   └── MEMORY.md         # 记忆种子
├── neige/                # 内阁 — 战略规划（Phase 4 新增）
├── wenyuan/              # 文渊阁 — 文档管理（Phase 4 新增）
├── tongzheng/            # 通政司 — 协调通知（Phase 4 新增）
└── hubu/                 # 户部 — 成本管理（Phase 4 新增）
```

### 5.3 SOUL.md frontmatter 格式

```yaml
---
name: 都察院 (Censorate)
department: ducha
tools_allowed: []
tools_denied: []
tool_tier_max: 1
can_delegate: false
delegates_to: []
---
```

### 5.4 Selector 关键词映射

```
bingbu:     execute, run, deploy, build, implement, create
ducha:      audit, review, check, inspect, verify, validate
hubu:       cost, budget, finance, expense, token
wenyuan:    memory, knowledge, search, recall, document
tongzheng:  notify, alert, message, report, communicate
neige:      plan, strategy, coordinate, decide, synthesize
```

---

## 6. Persona Loader 架构调整

### 6.1 问题

Phase 3 的 `_seed_from_files()` 会将文件目录（如 `personas/ducha/`）自动插入 SQLite 作为独立 persona 记录。当用户在 DB 中创建了命名角色（如 "唐伯虎" dept=bingbu），文件级 persona 和命名角色会并列显示在百官列表中。

### 6.2 架构决策

**文件目录 = 部门 prompt 模板**，不是独立 persona。DB 中的命名角色通过 `department` 字段关联文件目录。

### 6.3 改动

#### `_seed_from_files()` → No-op

```python
def _seed_from_files(self) -> None:
    """File directories serve as prompt templates only.
    They are NOT auto-seeded into the database as standalone personas."""
    pass
```

#### `_dict_to_persona()` → 按 department 查找

```python
# Before: persona_dir = self._dir / d["id"]     # 按 persona ID 查找
# After:
dept_dir = self._dir / d.get("department", d["id"])  # 优先按 department 查找
id_dir = self._dir / d["id"]
if dept_dir.is_dir():
    persona_dir = dept_dir    # 唐伯虎(id=tbh, dept=bingbu) → personas/bingbu/
elif id_dir.is_dir():
    persona_dir = id_dir      # 兼容 id == 目录名的情况
```

### 6.4 效果

```
BEFORE (Phase 3):
  DB: 唐伯虎(tbh/bingbu) + 都察院(ducha/ducha)  ← 文件被 seed 进 DB
  UI: 7+ 角色显示（命名 + 部门混列）

AFTER (Phase 4):
  DB: 唐伯虎(tbh/bingbu)                         ← 只有命名角色
  Files: personas/bingbu/SOUL.md                   ← 唐伯虎的 prompt 来源
  UI: 5 个命名角色，各自使用部门模板的 SOUL/ROLE
```

---

## 7. 前端增强

### 7.1 Prompt 分层表格编辑列

**文件**: `web/src/pages/PersonaDetailPage.tsx`

在 Prompt 分层分析表格的 6 列后新增"操作"列，可编辑层显示 `<EditOutlined />` 按钮，点击复用已有 Drawer 编辑器。

### 7.2 可编辑层映射

| 层名 | persona_id | filename |
|------|-----------|----------|
| COURT.md | `"court"` | `COURT.md` |
| Court MEMORY.md | `"court"` | `MEMORY.md` |
| SOUL.md | 当前 personaId | `SOUL.md` |
| ROLE.md | 当前 personaId | `ROLE.md` |
| MEMORY.md | 当前 personaId | `MEMORY.md` |

不可编辑层（Base Identity、Recent Activity、Skills、Task Context）不显示按钮。

---

## 8. 修复后 Persona 路由数据流

```
EDICT
  │
  ▼
PLANNER ─── LLM 分配 persona ──► _validate_assignments()
  │                                 ├── valid_ids 检查
  │                                 ├── 无效 → selector.select_for_task()
  │                                 │          使用 DEFAULT_EXECUTOR_ID fallback
  │                                 └── logger.info(最终分配)
  ▼
DAG_SCHEDULER
  │  解析: loader.get(assigned_official)
  │  fallback: loader.get(DEFAULT_EXECUTOR_ID) + logger.warning
  ▼
AGENT ─── prompt_builder.build(persona)
  │         ├── # Court Protocol        ← 层级标记
  │         ├── # Persona Identity [id] ← 层级标记
  │         ├── # Role Specification    ← 层级标记
  │         └── fallback 警告           ← 新增
  ▼
MEMORY_MANAGER
  └── _resolve_persona_id(context, plan)  ← 共享方法
       ├── context["persona"].id           （优先级 1）
       ├── plan.tasks[0].assigned_official （优先级 2）
       └── DEFAULT_EXECUTOR_ID             （优先级 3）
```

---

## 9. Dogfood 验证结果

Phase 4 所有改动通过 7 项端到端验证：

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | 6 个 persona 全部加载 | ✓ |
| 2 | 6 种任务类型路由到正确 persona | ✓ |
| 3 | 关键词路由（审查→ducha, 规划→neige, 文档→wenyuan） | ✓ |
| 4 | 6 个 persona 的 prompt 都有正确的层级标记 | ✓ |
| 5 | 6 个 persona 的 memory 目录已创建 | ✓ |
| 6 | Memory recall persona 解析优先级正确 | ✓ |
| 7 | 所有任务类型有专属 persona，无 fallback | ✓ |
