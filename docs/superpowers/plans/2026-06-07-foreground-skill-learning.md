# 前景主导技能自学习系统 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 agent 在执行过程中主动把可复用方法固化为 skill(支持多文件),人可在 web 看见并撤销/编辑/pin,后台 curator 据此做单条质量迭代。

**Architecture:** 前景主导——`skill_manage` 为主路径(已即时可见),`AGENT_END` reviewer 降级兜底,`SkillCurator` 周期做库级整理 + 单条迭代。人的管控动作回流成 metrics 信号驱动迭代。

**Tech Stack:** Python(FastAPI + SQLite)、React+TS+Vite(web)、litellm。

**两处对默认流程的偏离(已与用户确认):**
1. **功能优先、测试最后补**(项目既定约定):task 步骤是「实现 → 验证 → commit」,不写 TDD-first 失败测试;测试集中在 Phase 6 补齐到 80%。
2. **前端 Phase 4 用契约 + 组件清单 + 参照文件**:前端难 TDD 且按约定测试后补,给出 API 契约、组件清单、需参照的现有文件,而非逐行 React 代码(避免臆造与现有模式不符的代码)。

**对应 spec:** `docs/superpowers/specs/2026-06-06-foreground-skill-learning-design.md`

---

## 文件结构

| 文件 | 职责 | 改动 |
|---|---|---|
| `src/tianshu/storage.py` | DB migration | 加 `skill_metrics` 两列 |
| `src/tianshu/skills/metrics.py` | 指标 dataclass + store | 加 `human_curated`/`last_human_action` + 两个方法 |
| `src/tianshu/config_manager.py` | 运行期配置 | `AgentConfigState` 加 3 项 |
| `src/tianshu/skills/loader.py` | skill 读写 | 改引导文案 + 加多文件写/删 |
| `src/tianshu/tools/skill_tools.py` | LLM 工具 | `skill_manage` 加 write_file/remove_file + 发事件 |
| `src/tianshu/skills/reviewer.py` | 兜底固化 | 创建成功发 `skill.learned` |
| `src/tianshu/skills/curator.py` | 后台优化 | 加单条迭代 pass |
| `src/tianshu/app.py` | 接线 | reviewer 注入 event_bus + register_skill_tools 传 event_bus |
| `src/tianshu/gateway/api.py` | HTTP API | 加 archive/pin/edit 端点 |
| `web/src/...` | 管控面 | 新建 skill 管理页(Phase 4 契约) |

---

## Phase 0:数据模型 + 配置(地基)

### Task 0.1:skill_metrics 加两列

**Files:**
- Modify: `src/tianshu/storage.py`(migrations 列表,约 `:640` 处)

- [ ] **Step 1:在 migrations 列表追加两条 ALTER**

在 `# Phase 8: persona 全局记忆读开关` 那条之后(`personas ADD COLUMN memory_global_read` 之后)追加:

```python
            # 2026-06-07: skill_metrics 人在回路字段（前景主导技能学习）
            "ALTER TABLE skill_metrics ADD COLUMN human_curated INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE skill_metrics ADD COLUMN last_human_action TEXT",
```

(迁移循环已有 `duplicate column name` 幂等保护,无需额外处理。)

- [ ] **Step 2:验证 migration 幂等运行**

Run: `.venv/bin/python -c "from tianshu.storage import Storage; Storage(':memory:')"`
Expected: 无异常退出(内存库建表 + migration 跑通)。

- [ ] **Step 3:Commit**

```bash
git add src/tianshu/storage.py
git commit -m "feat(skills): skill_metrics 增加 human_curated/last_human_action 列"
```

### Task 0.2:SkillMetrics dataclass 加字段

**Files:**
- Modify: `src/tianshu/skills/metrics.py:24`(dataclass)、`:183`(_row_to_metrics)

- [ ] **Step 1:dataclass 末尾加两字段**

在 `absorbed_into: str | None = None`(`:24`)之后加:

```python
    # Human-in-the-loop (前景主导)
    human_curated: bool = False
    last_human_action: str | None = None
```

- [ ] **Step 2:_row_to_metrics 读新列(用既有 col() 容错)**

在 `_row_to_metrics` 的 `SkillMetrics(...)` 构造里,`absorbed_into=col("absorbed_into", None),` 之后加:

```python
            human_curated=bool(col("human_curated", 0)),
            last_human_action=col("last_human_action", None),
```

- [ ] **Step 3:验证 import + 构造**

Run: `.venv/bin/python -c "from tianshu.skills.metrics import SkillMetrics; m=SkillMetrics('x'); print(m.human_curated, m.last_human_action)"`
Expected: `False None`

- [ ] **Step 4:Commit**

```bash
git add src/tianshu/skills/metrics.py
git commit -m "feat(skills): SkillMetrics 支持 human_curated/last_human_action"
```

### Task 0.3:SkillMetricsStore 加 set_human_curated + list_iteration_candidates

**Files:**
- Modify: `src/tianshu/skills/metrics.py`(`SkillMetricsStore`,`set_pinned` 之后)

- [ ] **Step 1:加 set_human_curated 方法**

在 `set_pinned`(`:134`)之后加:

```python
    def set_human_curated(self, skill_name: str, curated: bool = True) -> None:
        """Mark a skill as human-edited (golden); curator's auto-iterate skips it."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "UPDATE skill_metrics SET human_curated = ?, last_human_action = ? WHERE skill_name = ?",
            (1 if curated else 0, now, skill_name),
        )
        self._conn.commit()

    def touch_human_action(self, skill_name: str) -> None:
        """Record a human action timestamp (archive/pin) without changing curated flag."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "UPDATE skill_metrics SET last_human_action = ? WHERE skill_name = ?",
            (now, skill_name),
        )
        self._conn.commit()
```

- [ ] **Step 2:加 list_iteration_candidates 方法**

在 `list_agent_created`(`:152`)之后加:

```python
    def list_iteration_candidates(
        self, min_success_rate: float, min_usage: int,
    ) -> list[SkillMetrics]:
        """Agent skills eligible for auto-iteration:
        active, not pinned, not human-curated, enough usage, low success rate.
        """
        out: list[SkillMetrics] = []
        for m in self.list_agent_created():
            if m.state != "active" or m.pinned or m.human_curated:
                continue
            if m.usage_count < min_usage:
                continue
            rate = m.success_rate
            if rate is None or rate >= min_success_rate:
                continue
            out.append(m)
        return out
```

- [ ] **Step 3:验证**

Run: `.venv/bin/python -c "import sqlite3; from tianshu.skills.metrics import SkillMetricsStore; print(hasattr(SkillMetricsStore, 'set_human_curated'), hasattr(SkillMetricsStore, 'list_iteration_candidates'))"`
Expected: `True True`

- [ ] **Step 4:Commit**

```bash
git add src/tianshu/skills/metrics.py
git commit -m "feat(skills): metrics store 加 human_curated 写入与迭代候选筛选"
```

### Task 0.4:AgentConfigState 加 3 项配置

**Files:**
- Modify: `src/tianshu/config_manager.py:39`(`AgentConfigState`)

- [ ] **Step 1:在 `skill_curator_prune_builtins` 之后加**

```python
    # 前景主导技能学习
    skill_guard_agent_created: bool = True
    skill_iterate_min_success_rate: float = 0.5
    skill_iterate_min_usage: int = 3
```

- [ ] **Step 2:验证默认值**

Run: `.venv/bin/python -c "from tianshu.config_manager import AgentConfigState; c=AgentConfigState(); print(c.skill_guard_agent_created, c.skill_iterate_min_success_rate, c.skill_iterate_min_usage)"`
Expected: `True 0.5 3`

- [ ] **Step 3:Commit**

```bash
git add src/tianshu/config_manager.py
git commit -m "feat(skills): 加前景主导技能学习配置项"
```

> 说明:这些 config 有合理默认,本轮按 dataclass 默认生效即可。如需 web 可配/DB 持久化,参照 `skill_review_enabled` 的现有透出链路(`models/api.py` + `gateway/api.py:1053`),非本轮必需。

---

## Phase 1:① 前景化引导

### Task 1.1:改写 load_index 引导文案为过程中语义

**Files:**
- Modify: `src/tianshu/skills/loader.py:116-117`

- [ ] **Step 1:替换引导文案**

把当前:

```python
            "After completing a difficult task, consider saving reusable approaches "
            "as a new skill with skill_manage()."
```

改为:

```python
            "When you discover a non-obvious, reusable approach or a script you had "
            "to figure out, save it RIGHT THEN with skill_manage(action='create') — "
            "don't wait until the task ends. It becomes available to you immediately "
            "via skill_view. Bundle helper scripts with "
            "skill_manage(action='write_file')."
```

- [ ] **Step 2:验证注入文本包含新引导**

Run: `.venv/bin/python -c "from tianshu.skills.loader import SkillsLoader; import inspect; src=inspect.getsource(SkillsLoader.load_index); print('RIGHT THEN' in src)"`
Expected: `True`

- [ ] **Step 3:Commit**

```bash
git add src/tianshu/skills/loader.py
git commit -m "feat(skills): 引导 agent 过程中即时固化技能（前景化）"
```

---

## Phase 2:③ 多文件 skill

### Task 2.1:loader 加 write_skill_file / remove_skill_file(含路径安全)

**Files:**
- Modify: `src/tianshu/skills/loader.py`(`create_skill` 之后,`:451`)

- [ ] **Step 1:加常量(文件顶部已有 import 区域之后,类外)**

在模块级(靠近其它常量处)加:

```python
_SKILL_RESOURCE_DIRS = ("scripts", "references", "assets", "templates")
_MAX_RESOURCE_BYTES = 1024 * 1024  # 1 MiB per resource file
```

- [ ] **Step 2:在 SkillsLoader 加两个方法**

在 `create_skill`(`:451` 返回后)之后插入:

```python
    def _resolve_skill_resource(self, name: str, rel_path: str) -> Path:
        """Resolve a resource path INSIDE an existing skill dir, safely.

        Rejects absolute paths, traversal, and non-whitelisted top dirs.
        Returns the absolute target path (parent may not exist yet).
        """
        if not rel_path or rel_path.startswith("/") or "\\" in rel_path:
            raise ValueError(f"invalid resource path: {rel_path!r}")
        parts = Path(rel_path).parts
        if ".." in parts:
            raise ValueError(f"path traversal not allowed: {rel_path!r}")
        if parts[0] not in _SKILL_RESOURCE_DIRS:
            raise ValueError(
                f"top dir must be one of {_SKILL_RESOURCE_DIRS}, got {parts[0]!r}"
            )
        # Locate the skill dir across search dirs
        for base, _src in self._search_dirs():
            skill_dir = base / name
            if (skill_dir / "SKILL.md").is_file():
                target = (skill_dir / rel_path).resolve()
                if not str(target).startswith(str(skill_dir.resolve()) + "/"):
                    raise ValueError(f"resolved path escapes skill dir: {rel_path!r}")
                return target
        raise FileNotFoundError(f"Skill '{name}' not found")

    def write_skill_file(self, name: str, rel_path: str, content: str) -> dict:
        """Write a resource file inside a skill dir. Invalidates caches."""
        if len(content.encode("utf-8")) > _MAX_RESOURCE_BYTES:
            raise ValueError(f"resource exceeds {_MAX_RESOURCE_BYTES} bytes")
        target = self._resolve_skill_resource(name, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, content)
        self._l1_cache.pop(name, None)
        self._l2_metadata = None
        return {"name": name, "file": rel_path, "bytes": len(content.encode("utf-8"))}

    def remove_skill_file(self, name: str, rel_path: str) -> bool:
        """Remove a resource file inside a skill dir. Invalidates caches."""
        target = self._resolve_skill_resource(name, rel_path)
        if target.is_file():
            target.unlink()
            self._l1_cache.pop(name, None)
            self._l2_metadata = None
            return True
        return False
```

- [ ] **Step 3:验证路径安全(traversal/绝对路径被拒)**

Run:
```bash
.venv/bin/python - <<'PY'
import tempfile, pathlib
from tianshu.skills.loader import SkillsLoader
d = pathlib.Path(tempfile.mkdtemp())
(d / "skills").mkdir()
ld = SkillsLoader(builtin_dir=d/"builtin", workspace_dir=None, user_dir=d/"skills")
(d/"builtin").mkdir(exist_ok=True)
ld.create_skill("demo", "---\nname: demo\ndescription: d\n---\nbody")
ld.write_skill_file("demo", "scripts/run.py", "print(1)\n")
print("write ok")
for bad in ("../evil", "/etc/passwd", "secrets/x"):
    try:
        ld.write_skill_file("demo", bad, "x"); print("FAIL allowed", bad)
    except ValueError: print("rejected", bad)
PY
```
Expected: `write ok` 然后三行 `rejected ...`

- [ ] **Step 4:Commit**

```bash
git add src/tianshu/skills/loader.py
git commit -m "feat(skills): loader 支持 skill 目录多文件写入/删除（路径白名单+防穿越）"
```

### Task 2.2:skill_manage 加 write_file / remove_file action

**Files:**
- Modify: `src/tianshu/tools/skill_tools.py`(handlers + `_ACTION_HANDLERS` + schema)

- [ ] **Step 1:加两个 handler(在 `_handle_delete` 之后,`:165`)**

```python
async def _handle_write_file(skills: SkillsLoader, name: str, **kwargs: Any) -> ToolResult:
    file_path = kwargs.get("file_path")
    file_content = kwargs.get("file_content")
    if not file_path or file_content is None:
        return error_result("'file_path' and 'file_content' are required for write_file")
    try:
        result = skills.write_skill_file(name, file_path, file_content)
        return ok_result(json.dumps({"status": "file_written", **result}, ensure_ascii=False))
    except (FileNotFoundError, ValueError) as e:
        return error_result(str(e))


async def _handle_remove_file(skills: SkillsLoader, name: str, **kwargs: Any) -> ToolResult:
    file_path = kwargs.get("file_path")
    if not file_path:
        return error_result("'file_path' is required for remove_file")
    try:
        removed = skills.remove_skill_file(name, file_path)
        if removed:
            return ok_result(json.dumps({"status": "file_removed", "file": file_path}))
        return error_result(f"File '{file_path}' not found in skill '{name}'")
    except (FileNotFoundError, ValueError) as e:
        return error_result(str(e))
```

- [ ] **Step 2:注册到 `_ACTION_HANDLERS`(`:184`)**

```python
_ACTION_HANDLERS = {
    "create": _handle_create,
    "edit": _handle_edit,
    "patch": _handle_patch,
    "delete": _handle_delete,
    "activate": _handle_activate,
    "write_file": _handle_write_file,
    "remove_file": _handle_remove_file,
}
```

- [ ] **Step 3:`_skill_manage` 放行新 action 的参数路由**

`_skill_manage`(`:205`)当前对 `create/delete/activate` 传 `metrics_store`,其余只传 `**kwargs`。`write_file`/`remove_file` 不需要 metrics_store,落入 `else` 分支即可——无需改路由,只需确认 action 校验串包含它们(下一步 schema 的 enum 已覆盖;`_skill_manage` 的 handler 查表已支持)。无代码改动,跳到 Step 4。

- [ ] **Step 4:更新 skill_manage 的 schema(`:280` description + enum + properties)**

把 `action` enum 改为:

```python
                    "action": {
                        "type": "string",
                        "enum": ["create", "edit", "patch", "delete", "activate",
                                 "write_file", "remove_file"],
                        "description": "The action to perform",
                    },
```

在 properties 里 `patch_new` 之后加:

```python
                    "file_path": {
                        "type": "string",
                        "description": "Resource path inside the skill dir "
                                       "(top dir: scripts/references/assets/templates). "
                                       "Required for write_file/remove_file.",
                    },
                    "file_content": {
                        "type": "string",
                        "description": "Resource file content (required for write_file).",
                    },
```

把 description 改为(追加多文件说明):

```python
            description=(
                "Create, edit, patch, delete a skill, or write/remove a bundled "
                "resource file (scripts/references/assets/templates). "
                "Use after figuring out a reusable approach to save it for reuse."
            ),
```

- [ ] **Step 5:验证 schema 暴露新 action**

Run:
```bash
.venv/bin/python - <<'PY'
from tianshu.tools.skill_tools import _ACTION_HANDLERS
print(sorted(_ACTION_HANDLERS) == sorted(
    ["create","edit","patch","delete","activate","write_file","remove_file"]))
PY
```
Expected: `True`

- [ ] **Step 6:Commit**

```bash
git add src/tianshu/tools/skill_tools.py
git commit -m "feat(skills): skill_manage 加 write_file/remove_file 支持多文件技能"
```

### Task 2.3:guard 扫描资源文件(受 config 开关)

**Files:**
- Modify: `src/tianshu/skills/loader.py`(`write_skill_file`)或 `skill_tools.py`(handler)

设计:在 `_handle_write_file` 里,写入前对脚本类资源做 guard 扫描;开关由调用方传入。为不改 loader 纯度,把扫描放在 tool 层。

- [ ] **Step 1:`register_skill_tools` 增加 guard 与开关参数**

`register_skill_tools(registry, skills, metrics_store=None)` 改签名:

```python
def register_skill_tools(
    registry: ToolRegistry,
    skills: SkillsLoader,
    metrics_store: MetricsStore | None = None,
    guard_agent_created: bool = True,
    event_bus: Any | None = None,
) -> None:
```

- [ ] **Step 2:write_file handler 前置 guard(开关开时)**

在 `_handle_write_file` 顶部、`write_skill_file` 调用前加(用闭包传入的 guard 开关):

```python
    if kwargs.get("_guard_enabled") and file_content is not None:
        from tianshu.skills.guard import SkillsGuard, TrustLevel
        guard = SkillsGuard()
        gres = guard.scan_content(file_content, TrustLevel.AGENT_CREATED)
        if not SkillsGuard.should_allow(gres, TrustLevel.AGENT_CREATED):
            findings = "; ".join(f.message for f in gres.findings)
            return error_result(f"guard blocked resource: {findings}")
```

在 `skill_manage` 的 lambda 注册处注入开关与 bus(二者随 `**kwargs` 自然流经 `_skill_manage` 透传给 handler——无需改 `_skill_manage` 签名,也不出现在 LLM schema 的 `parameters.properties` 里,LLM 看不到):

```python
    registry.register(
        "skill_manage",
        lambda **kwargs: _skill_manage(
            skills,
            metrics_store=metrics_store,
            _guard_enabled=guard_agent_created,
            event_bus=event_bus,
            **kwargs,
        ),
        ...
    )
```

handler 侧读取:`_handle_write_file` 用 `kwargs.get("_guard_enabled")`,`_handle_create` 用 `kwargs.get("event_bus")`(见 Task 3.2)。

- [ ] **Step 3:验证 guard 拦截(构造一个含 reverse shell 特征的脚本)**

Run:
```bash
.venv/bin/python - <<'PY'
from tianshu.skills.guard import SkillsGuard, TrustLevel
g = SkillsGuard()
r = g.scan_content("import socket,subprocess,os; s=socket.socket(); s.connect(('1.2.3.4',4444))", TrustLevel.AGENT_CREATED)
print("findings:", len(r.findings))
PY
```
Expected: `findings:` 后为 ≥1(确认 guard 能识别;最终是否 block 取决于策略矩阵 `AGENT_CREATED=("allow","allow","ask")`)。

- [ ] **Step 4:Commit**

```bash
git add src/tianshu/tools/skill_tools.py
git commit -m "feat(skills): 多文件资源写入受 guard 扫描（可配 skill_guard_agent_created）"
```

---

## Phase 3:④ 人在回路（后端）

### Task 3.1:reviewer 发 skill.learned 事件

**Files:**
- Modify: `src/tianshu/skills/reviewer.py`(`__init__` + `_handle_create`)

- [ ] **Step 1:`__init__` 收 event_bus + attach 方法**

`SkillReviewHandler.__init__` 末尾加 `self._event_bus = None`,并加:

```python
    def attach_event_bus(self, bus: Any) -> None:
        self._event_bus = bus
```

- [ ] **Step 2:`_handle_create` 成功后发事件**

在 `_handle_create` 的 `logger.info("[SKILL_REVIEW] Created skill ...")` 之后加:

```python
            self._emit_learned(name, reason, edict_id, created_by="reviewer")
```

并加方法:

```python
    def _emit_learned(self, name: str, reason: str, edict_id: str | None, created_by: str) -> None:
        if not self._event_bus:
            return
        try:
            from tianshu.models.events import make_event
            ev = make_event(
                event_type="skill.learned",
                edict_id=edict_id,
                memorial_id=None,
                producer="skill_reviewer",
                payload={"name": name, "reason": reason, "created_by": created_by},
            )
            self._event_bus.fire(ev)
        except Exception:
            logger.debug("[SKILL_REVIEW] emit skill.learned failed", exc_info=True)
```

- [ ] **Step 3:验证 import + 方法存在**

Run: `.venv/bin/python -c "from tianshu.skills.reviewer import SkillReviewHandler; print(hasattr(SkillReviewHandler,'attach_event_bus'))"`
Expected: `True`

- [ ] **Step 4:Commit**

```bash
git add src/tianshu/skills/reviewer.py
git commit -m "feat(skills): reviewer 创建技能时发 skill.learned 事件"
```

### Task 3.2:skill_manage create 发 skill.learned

**Files:**
- Modify: `src/tianshu/tools/skill_tools.py`(`_handle_create`)

- [ ] **Step 1:`_handle_create` 成功后发事件**

`_handle_create` 已收 `**kwargs`;`_skill_manage` 透传 `event_bus`。在 `result = skills.create_skill(...)` 成功后加:

```python
        bus = kwargs.get("event_bus")
        if bus is not None:
            try:
                from tianshu.models.events import make_event
                bus.fire(make_event(
                    event_type="skill.learned",
                    edict_id=None, memorial_id=None, producer="skill_manage",
                    payload={"name": name, "created_by": "agent"},
                ))
            except Exception:
                pass
```

(确保 `_skill_manage` 对 `create` 分支把 `event_bus` 透传进 `_handle_create` —— create 走 `handler(skills, name, metrics_store=..., **kwargs)`,`event_bus` 已在 kwargs 中。)

- [ ] **Step 2:验证**

Run: `.venv/bin/python -c "import inspect; from tianshu.tools import skill_tools; print('skill.learned' in inspect.getsource(skill_tools._handle_create))"`
Expected: `True`

- [ ] **Step 3:Commit**

```bash
git add src/tianshu/tools/skill_tools.py
git commit -m "feat(skills): agent 主动创建技能时发 skill.learned 事件"
```

### Task 3.3:app.py 接线 event_bus

**Files:**
- Modify: `src/tianshu/app.py:562-565`、`register_skill_tools` 调用处

- [ ] **Step 1:reviewer attach event_bus**

`skill_reviewer = SkillReviewHandler(...)` 之后(`:564` 后)加:

```python
    skill_reviewer.attach_event_bus(event_bus)
```

- [ ] **Step 2:register_skill_tools 传 guard 开关 + event_bus**

找到 `register_skill_tools(...)` 调用处(在 app.py 工具注册区),改为:

```python
    register_skill_tools(
        tool_registry, skills, metrics_store=metrics_store,
        guard_agent_created=config_manager.agent_config.skill_guard_agent_created,
        event_bus=event_bus,
    )
```

(若调用处变量名不同,按现有变量名调整 registry/skills 实参。)

- [ ] **Step 3:验证启动期 import 无误**

Run: `.venv/bin/python -c "import tianshu.app"`
Expected: 无异常。

- [ ] **Step 4:Commit**

```bash
git add src/tianshu/app.py
git commit -m "feat(skills): 接线 reviewer/skill_manage 的 event_bus 与 guard 开关"
```

### Task 3.4:gateway API —— 撤销/编辑/pin 端点

**Files:**
- Modify: `src/tianshu/gateway/api.py`(`GET /skills/{name}` 之后,`:1974`)

- [ ] **Step 1:加三个端点**

```python
@gateway_router.post("/skills/{name}/archive")
async def archive_skill(name: str, request: Request):
    """Human undo: archive an agent-created skill (recoverable)."""
    loader = request.app.state.skills_loader
    metrics = request.app.state.skill_metrics_store
    ok = loader.archive_skill(name)
    if ok and metrics is not None:
        metrics.mark_archived(name)
        metrics.touch_human_action(name)
    return ApiResponse(success=ok, data={"name": name, "archived": ok})


@gateway_router.post("/skills/{name}/pin")
async def pin_skill(name: str, request: Request, body: dict = Body(default={})):
    """Pin/unpin: exempt from curator transitions."""
    metrics = request.app.state.skill_metrics_store
    pinned = bool(body.get("pinned", True))
    if metrics is None:
        raise HTTPException(status_code=503, detail="metrics store unavailable")
    metrics.ensure_exists(name)
    metrics.set_pinned(name, pinned)
    metrics.touch_human_action(name)
    return ApiResponse(success=True, data={"name": name, "pinned": pinned})


@gateway_router.put("/skills/{name}")
async def edit_skill(name: str, request: Request, body: dict = Body(...)):
    """Human edit: save SKILL.md content + mark as golden (human_curated)."""
    loader = request.app.state.skills_loader
    metrics = request.app.state.skill_metrics_store
    content = body.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    try:
        loader.save_skill(name, content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"skill {name} not found")
    if metrics is not None:
        metrics.ensure_exists(name)
        metrics.set_human_curated(name, True)
    return ApiResponse(success=True, data={"name": name, "human_curated": True})
```

- [ ] **Step 2:确认依赖可用**

确认文件已 import `Body`(FastAPI)与 `app.state.skill_metrics_store`、`app.state.skills_loader`。`skills_loader` 已在 `GET /skills` 使用(`:1962`)。若 `skill_metrics_store` 未挂到 app.state,在 app.py 加 `app.state.skill_metrics_store = metrics_store`(紧邻 `app.state.skills_loader` 赋值处)。若 `Body` 未导入,在 fastapi import 行补 `Body`。

- [ ] **Step 3:验证路由注册**

Run: `.venv/bin/python -c "import tianshu.app" && echo ok`
Expected: `ok`(import 期无语法/引用错误)。

- [ ] **Step 4:Commit**

```bash
git add src/tianshu/gateway/api.py src/tianshu/app.py
git commit -m "feat(api): 技能撤销/编辑/pin 端点（人在回路）"
```

---

## Phase 4:④ 人在回路（前端）—— 契约 + 组件清单

> 前端按现有 web 模式实现。**参照文件**:`web/src/api/system.ts`(API client 写法)、`web/src/pages/SystemManagementPage.tsx`(页面+表格+操作按钮模式)、`web/src/api/types.ts`(类型)、`web/src/i18n/locales/{en,zh-modern,zh-classic}.json`(文案,**zh-classic 保留"菜单"彩蛋风格**)。前端按约定测试后补。

### Task 4.1:API client

**Files:** Create `web/src/api/skills.ts`

**契约(对接 Phase 3 端点):**
- `listSkills(): Promise<SkillMeta[]>` → `GET /skills`(已存在)
- `getSkill(name): Promise<SkillDetail>` → `GET /skills/{name}`(已存在)
- `archiveSkill(name): Promise<void>` → `POST /skills/{name}/archive`
- `pinSkill(name, pinned): Promise<void>` → `POST /skills/{name}/pin` body `{pinned}`
- `editSkill(name, content): Promise<void>` → `PUT /skills/{name}` body `{content}`

`SkillMeta` 字段对齐 `loader.list_all_metadata()` 输出 + metrics(`name, description, source, created_by, state, pinned, human_curated, usage_count, success_rate`)。

- [ ] Step 1:照 `web/src/api/system.ts` 的 fetch 封装写 `skills.ts` 上述 5 个函数。
- [ ] Step 2:在 `web/src/api/types.ts` 加 `SkillMeta`/`SkillDetail` 类型。
- [ ] Step 3:Commit `feat(web): skills API client`

### Task 4.2:"最近固化的技能" 页面/组件

**Files:** Create `web/src/pages/SkillsPage.tsx` + `web/src/components/skill/SkillList.tsx`、`SkillEditDialog.tsx`;改 `web/src/App.tsx`(加路由)、layout 导航。

**清单:**
- [ ] Step 1:`SkillList` —— 表格列:name、description、来源徽标(`created_by=agent` 高亮)、state、usage/success_rate;按 `created_at` 倒序(最近固化在前)。
- [ ] Step 2:每行操作:**撤销**(archiveSkill,二次确认)、**pin/unpin**(pinSkill)、**编辑**(打开 `SkillEditDialog`)。
- [ ] Step 3:`SkillEditDialog` —— 文本域编辑 SKILL.md,保存调 editSkill;保存后提示"已标记为人工校订(不会被自动迭代覆盖)"。
- [ ] Step 4:`App.tsx` 加 `/skills` 路由 + 导航入口;三套 i18n 文案补齐(en/zh-modern/zh-classic)。
- [ ] Step 5:Commit `feat(web): 技能管控面（最近固化/撤销/编辑/pin）`

### Task 4.3:订阅 skill.learned 事件（可选增量)

若 web 已有事件流(SSE/WS)展示审计,把 `skill.learned` 接入"最近活动"提示。参照现有 `curate.completed` 等事件在 web 的展示路径(若存在);不存在则跳过,`SkillsPage` 轮询 `listSkills` 即可。

---

## Phase 5:② 单条迭代（curator 增强）

### Task 5.1:curator 加单条迭代 pass

**Files:**
- Modify: `src/tianshu/skills/curator.py`(加 `_iterate_pass` + 在 `run()` 接入)

- [ ] **Step 1:加迭代提示常量(模块级,`_USER` 之后)**

```python
_ITERATE_SYSTEM = (
    "你是「修撰」——技能库质量校理。给定一个低效技能的 SKILL.md 与其指标，"
    "产出一份改进后的完整 SKILL.md（含 YAML frontmatter: name/description，name 不变）。"
    "聚焦：让指令更清晰、解释 why、去掉拖累执行的部分。只输出 SKILL.md 全文，不要代码块标记。"
)

_ITERATE_USER = """\
技能 `{name}` 成功率偏低（usage={usage} success_rate={rate}）。当前 SKILL.md：

{content}

请产出改进后的完整 SKILL.md（name 必须仍是 `{name}`）。"""
```

- [ ] **Step 2:加 _iterate_pass 方法(在 `_apply_plan` 之后)**

```python
    async def _iterate_pass(self) -> list[str]:
        """Auto-improve low-success agent skills (not pinned/human-curated)."""
        cfg = self._config.agent_config
        improved: list[str] = []
        candidates = self._metrics.list_iteration_candidates(
            min_success_rate=getattr(cfg, "skill_iterate_min_success_rate", 0.5),
            min_usage=getattr(cfg, "skill_iterate_min_usage", 3),
        )
        for m in candidates:
            skill = self._loader.get_skill(m.skill_name)
            if not skill:
                continue
            try:
                resp = await self._llm.chat(messages=[
                    {"role": "system", "content": _ITERATE_SYSTEM},
                    {"role": "user", "content": _ITERATE_USER.format(
                        name=m.skill_name, usage=m.usage_count,
                        rate=m.success_rate, content=skill.get("content", ""))},
                ])
                new_md = (getattr(resp, "content", None) or "").strip()
                if new_md.startswith("```") and "\n" in new_md:
                    new_md = new_md.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                if not new_md:
                    continue
                v = self._validator.validate(m.skill_name, new_md, source="agent-created")
                if not v.valid:
                    continue
                self._loader.save_skill(m.skill_name, new_md)
                improved.append(m.skill_name)
            except Exception:  # noqa: BLE001
                logger.debug("[CURATOR] iterate failed for %s", m.skill_name, exc_info=True)
        return improved
```

- [ ] **Step 3:CurateResult 加 iterated 字段 + run() 接入**

给 `CurateResult`(`:71`)加字段 `iterated: list[str] = field(default_factory=list)`,并在 `to_dict()` 返回 dict 里加 `"iterated": self.iterated,`。

在 `run()` 里 `result = CurateResult(...)` 构造之后(`candidates` 已收集)加:

```python
            if not dry_run:
                result.iterated = await self._iterate_pass()
```

在 `_write_report` 的 `lines` 列表里(归档那行之后)加:`f"- 单条迭代: {result.iterated or '(无)'}",`

- [ ] **Step 4:验证 import + 方法**

Run: `.venv/bin/python -c "from tianshu.skills.curator import SkillCurator; print(hasattr(SkillCurator,'_iterate_pass'))"`
Expected: `True`

- [ ] **Step 5:Commit**

```bash
git add src/tianshu/skills/curator.py
git commit -m "feat(skills): curator 单条技能质量迭代（低分自动改进，human_curated 豁免）"
```

---

## Phase 6:测试补齐(80%)

> 项目约定测试最后补。用 pytest,放 `tests/skills/`、`tests/tools/`。

### Task 6.1:loader 多文件 + 路径安全单测

**Files:** Create `tests/skills/test_loader_multifile.py`

- [ ] Step 1:写测试——`write_skill_file` 正常写入 scripts/references;`../`、绝对路径、非白名单顶层目录被拒(`pytest.raises(ValueError)`);超 1MiB 被拒;`remove_skill_file` 删除存在/不存在;写入后 `_l2_metadata is None`。
- [ ] Step 2:Run `/.venv/bin/python -m pytest tests/skills/test_loader_multifile.py -v` → 全绿。
- [ ] Step 3:Commit `test(skills): loader 多文件写入与路径安全`

### Task 6.2:skill_manage action 单测

**Files:** Create `tests/tools/test_skill_manage_files.py`

- [ ] Step 1:写测试——`write_file` 成功/缺参数报错;`remove_file`;`_guard_enabled=True` 时含恶意特征被 block(mock guard 或用真实特征);create 时若传 event_bus 则 `fire` 被调用(用 mock bus 断言 called)。
- [ ] Step 2:Run pytest → 全绿。
- [ ] Step 3:Commit `test(skills): skill_manage 多文件与事件`

### Task 6.3:metrics + curator 迭代单测

**Files:** Create `tests/skills/test_iteration.py`

- [ ] Step 1:`list_iteration_candidates` 筛选逻辑(pinned/human_curated/低 usage/高 success_rate 都被排除;只有 active+低分+足量被选);`set_human_curated` 写入 `human_curated=1` 且 `last_human_action` 非空。
- [ ] Step 2:`_iterate_pass` 用 mock llm_client 返回改进 SKILL.md,断言 `save_skill` 被调用且 human_curated 候选被跳过。
- [ ] Step 3:Run pytest → 全绿。
- [ ] Step 4:Commit `test(skills): 迭代候选筛选与 curator 单条迭代`

### Task 6.4:即时可见性集成测试(回归保护)

**Files:** Create `tests/skills/test_immediate_visibility.py`

- [ ] Step 1:create_skill → 立即 `get_skill`/`list_all_metadata` 命中(不依赖 watcher);write_skill_file 后缓存失效。
- [ ] Step 2:Run pytest → 全绿。
- [ ] Step 3:Commit `test(skills): 中途创建技能即时可见`

### Task 6.5:覆盖率校验

- [ ] Step 1:Run `.venv/bin/python -m pytest tests/skills tests/tools --cov=src/tianshu/skills --cov=src/tianshu/tools/skill_tools --cov-report=term-missing`
- [ ] Step 2:确认改动模块 ≥ 80%;不足则补测试。
- [ ] Step 3:Commit 任何补充测试。

---

## 验收对照(spec §2)

| spec 验收 | 对应 Task |
|---|---|
| 前景主路径强化(引导) | 1.1 |
| 即时可用(回归) | 6.4 |
| 多文件 skill + 路径/大小校验 | 2.1, 2.2, 6.1 |
| 人在回路(事件 + web + metrics) | 3.1–3.4, 4.x |
| 单条迭代(human 锁定/撤销/低分改进) | 0.3, 5.1, 6.3 |
| guard 可配 | 0.4, 2.3 |
