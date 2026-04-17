# Persona（人格系统）

覆盖 `src/tianshu/persona/` 全部 7 个 Python 文件 + `personas/`（git 模板）+ `~/.tianshu/personas/`（运行时副本）。

---

## 1. 模板 vs 运行时分离

feat_phase5 的关键变更：**人格身份文件双层存储**。

| 层 | 路径 | 语义 | 写入方 |
|---|---|---|---|
| 模板 | `personas/{department}/` | git 跟踪的部门级模板（seed source） | 代码仓库 |
| 运行时 | `~/.tianshu/personas/{persona_id}/` | 单个 official 的私有副本，可独立演化 | UI / API |

首次加载某 persona 时，`PersonaLoader.ensure_runtime_identity(persona_id, template_dir)` 会从模板目录拷贝 `SOUL.md` + `ROLE.md` 到运行时目录（幂等，已存在不覆盖）。UI 的修改只落到运行时路径，git 模板永不动。

这一模式镜像了 `MEMORY.md` 的处理（见 `memory.md` §6）：模板是起点，运行时是真相源。

## 2. AgentPersona 模型（`persona/model.py`）

```python
class AgentPersona(BaseModel):
    id: str                 # "neige" | "bingbu" | "ducha" | ...
    name: str
    department: str
    soul_path: Path         # 运行时 SOUL.md
    role_path: Path         # 运行时 ROLE.md
    memory_path: Path       # 模板 MEMORY.md（运行时在 ~/.tianshu/memory/）
    skills_dir: Path | None
    tools_allowed: list[str]
    tools_denied: list[str]
    skills_allowed: list[str]
    tool_tier_max: int
    can_delegate: bool
    delegates_to: list[str]
    llm_config_name: str | None  # None = 用全局配置
```

`DEFAULT_EXECUTOR_ID = "bingbu"` — 全局默认执行者 ID。

## 3. PersonaLoader（`persona/loader.py`）

**双源加载**：SQLite（`personas` 表，primary）+ 文件系统（模板 seed）。

- `load_all()` → 启动时调用：`_seed_from_files()`（no-op，仅作模板源）+ `_load_from_db()` 填 in-memory 缓存
- `get(persona_id)` → 从缓存取 `AgentPersona`
- `save(persona)` → 写 SQLite `personas` 表 + 更新缓存
- `delete(persona_id)` → 删 SQLite + 缓存 + 模板目录（防止 `_seed_from_files` 复活）
- `ensure_runtime_identity(persona_id, template_source_dir)` → 拷贝 SOUL/ROLE 到 `~/.tianshu/personas/{id}/`
- `_read_frontmatter(path)` → 解析 SOUL.md YAML frontmatter（name, department, tools_allowed, tool_tier_max, can_delegate, delegates_to, llm_config_name, skills_allowed）

`_dict_to_persona(d)` 关键逻辑：先找 `personas/{department}/` 作为模板目录，若无则回退到 `personas/{id}/`；然后调用 `ensure_runtime_identity` 拿到运行时 path；DB 里存的 path 被忽略，**以运行时目录为准**。

## 4. OfficialSelector（`persona/selector.py`）

任务 → persona 的映射。

**TASK_DEPARTMENT_PREFERENCE**（task_type → department）：

| task_type | department |
|---|---|
| `plan` | neige |
| `execute` | bingbu |
| `audit` | ducha |
| `notify` | tongzheng |
| `memory` | wenyuan |
| `cost` | hubu |

**_DEPARTMENT_KEYWORDS**（关键字匹配，用于 `select_for_task(description)`）：

| department | keywords |
|---|---|
| `bingbu` | execute / run / deploy / build / implement / create |
| `ducha` | audit / review / check / inspect / verify / validate |
| `hubu` | cost / budget / finance / expense / token |
| `wenyuan` | memory / knowledge / search / recall / document |
| `tongzheng` | notify / alert / message / report / communicate |
| `neige` | plan / strategy / coordinate / decide / synthesize |

API：
- `select(task_type)` — 按 `TASK_DEPARTMENT_PREFERENCE`
- `select_for_task(description)` — 关键字打分取最高分 department，无命中则 fallback
- `_fallback_persona()` — 优先非 neige 的任一 persona；都无则返回 neige
- `get_default_map()` / `get_keyword_map()` — UI 用，展示任务→官员的当前映射状态

## 5. PromptBuilder（`persona/prompt_builder.py`）— 8 层注入

```python
async def build(edict, persona=None, skills_char_budget=30000) -> str
```

| Layer | 内容 | 来源 | 条件 |
|---|---|---|---|
| 1 | Base Identity（`_BASE_IDENTITY` 常量） | 内建 | 恒有 |
| 2 | COURT.md（朝廷共享上下文） | `personas/court/COURT.md`（模板） | persona 非空 |
| 3 | SOUL.md（人格身份） | `~/.tianshu/personas/{id}/SOUL.md`（运行时） | persona 非空 |
| 4 | ROLE.md（角色职责） | `~/.tianshu/personas/{id}/ROLE.md`（运行时） | persona 非空 |
| 5 | MEMORY.md（核心长期记忆） | `~/.tianshu/memory/{id}/MEMORY.md` | persona 非空 |
| 5.1 | L1 Critical Facts | `DrawerStore.get_l1(wing=id)` | `drawer_store + l1_enabled` |
| 5.5 | Recent Activity（近 2 天日志） | `~/.tianshu/memory/{id}/YYYY-MM-DD.md`（`char_budget=2000`） | persona 非空 |
| 6 | Court MEMORY.md | `~/.tianshu/memory/court/MEMORY.md` | persona 非空 |
| 7 | Skills 索引 + always=true skills | `SkillsLoader.load_index` + `load_always` | 恒有 |
| 8 | Task Context（`Current task ID: {edict.id}`） | `edict` | 恒有 |

Layer 3 有 fallback：SOUL.md 不存在时退化为 `f"You are {persona.name}, serving in the {persona.department} department..."` 并打 warning。

`build_layers(edict, persona)` 返回每层 chars / tokens_est，供 `PersonaDetailPage` 的 prompt preview 使用。

## 6. 7 部门现状

`personas/` 目录（git 跟踪模板）：

| id | 部门 | 职能（代码中的角色） |
|---|---|---|
| `neige` | 内阁 | Planner 默认人格；战略规划与跨部门协调 |
| `bingbu` | 兵部 | `DEFAULT_EXECUTOR_ID`；默认执行者 |
| `ducha` | 都察院 | 审计、Code Review、verdict=flag/block 写 insight |
| `tongzheng` | 通政司 | 渲染、通知、ConsultationSession 主持 |
| `wenyuan` | 文渊阁 | 文档、知识管理、memory/recall 任务 |
| `hubu` | 户部 | 成本审查、budget/token 任务 |
| `court` | 朝廷 | 共享上下文目录（`COURT.md`，PromptBuilder Layer 2），不作为独立 persona |

每个部门目录内容：
- `SOUL.md` — 身份（YAML frontmatter + body）
- `ROLE.md` — 职责（Layer 4）
- `MEMORY.md` — 初始记忆模板（seed 到 `~/.tianshu/memory/{id}/MEMORY.md`）
- `court/` 独有 `COURT.md`

## 7. DAG 人格重分派

当 planner 产出多任务 plan 时：
1. `DAGScheduler.handle_plan_completed` 将每个 `DAGNode.assigned_official` 独立调度
2. 每个节点 `persona = persona_loader.get(node.assigned_official)` 取对应 persona
3. 若节点指定的 persona 不存在（被删除或未 seed），回退到 `DEFAULT_EXECUTOR_ID = "bingbu"`
4. 子节点建 memorial 时记录 `persona_id`，审计与记忆归属按此计

persona 可以 `can_delegate=True`，在工具调用中使用 `delegates_to` 列表触发子代理；目前内建工具未消费此字段，由 plugin 或 skill 决定用法。

## 8. 其他组件

- `persona/department.py` — 部门 metadata（name、description），供 UI 渲染
- `persona/metrics.py` — 每个 persona 的 `used_count` / `avg_latency_ms` / `success_rate` 统计
- `persona/evaluator.py` — `PerformanceEvaluator` 按 memorials 结果打分，用于 UI 仪表板
- `persona/memory_manager.py` — 此文件为 legacy，`memory/manager.py` 是当前实现

## 代码路径索引

- `src/tianshu/persona/model.py`
- `src/tianshu/persona/loader.py`
- `src/tianshu/persona/selector.py`
- `src/tianshu/persona/prompt_builder.py`
- `src/tianshu/persona/department.py`
- `src/tianshu/persona/metrics.py`
- `src/tianshu/persona/evaluator.py`
- `personas/{bingbu,ducha,hubu,neige,tongzheng,wenyuan}/` — git 模板
- `personas/court/COURT.md` — 朝廷共享
- `~/.tianshu/personas/{persona_id}/` — 运行时副本（SOUL.md / ROLE.md）
