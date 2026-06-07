# 平行位面（行为配置自进化分叉）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让天枢的自进化从单线升级为"可分叉 + 可回滚 + 可选优"的平行位面——一个位面 = 一份可命名/分支/切换/回滚的"行为配置快照"（人格 SOUL/ROLE + 技能集 + 策略 + config），并能据真实使用的适应度自动变异、择优。

**Architecture:** 新增自包含 `src/tianshu/universe/` 模块（model / store / manager / fitness / evolver），通过"重定向 loader 根目录 + 重载 config 快照"实现切换；位面行为状态全量拷贝落盘 `~/.tianshu/universes/{id}/`，元数据入 `universes` 表；每道 memorial 标记 `universe_id` 以归因适应度。1a 先交付手动基建（git-for-behavior），1b 在其上加自进化引擎（照搬 `SkillCurator` 骨架）。代码变体位面为第二步，仅留扩展点。

**Tech Stack:** Python 3.12 / FastAPI / SQLite(WAL) / pydantic / dataclass(frozen) / pytest；前端 React + TypeScript + Ant Design + react-router + axios。

> **测试约定（项目偏好「功能优先、测试最后补」）**：每个 Task 以"实现 + 运行可见性验证"为主，单元/集成测试集中在各阶段末的测试 Task（Task 11、Task 20）统一补齐到 80%。这覆盖了 superpowers 默认逐步 TDD（用户指令优先）。

---

## File Structure

**新建（自包含模块，本计划主体）**
| 文件 | 职责 |
|---|---|
| `src/tianshu/universe/__init__.py` | 模块导出 |
| `src/tianshu/universe/model.py` | `Universe` 数据契约 + `UniverseStatus`/`UniverseOrigin` 枚举 |
| `src/tianshu/universe/store.py` | `UniverseStore`：目录布局、manifest 读写、行为快照(snapshot)/还原(restore) |
| `src/tianshu/universe/manager.py` | `UniverseManager`：genesis/branch/switch/rollback/diff/archive，重定向 loaders + config |
| `src/tianshu/universe/fitness.py` | `compute_fitness`：按 universe_id 聚合 memorial 指标 + 显式反馈 → 综合分（1b） |
| `src/tianshu/universe/evolver.py` | `UniverseEvolver`：采信号→LLM 变异→分支候选→熔断/晋升推荐（1b） |

**修改（集成点）**
| 文件 | 改动 |
|---|---|
| `src/tianshu/storage.py` | `universes` 表 + `memorials.universe_id` 迁移 + Universe CRUD + memorial 读写带 universe_id + 显式反馈存储 |
| `src/tianshu/models/memorial.py` | `Memorial` 加 `universe_id` 字段 |
| `src/tianshu/persona/loader.py` | `PersonaLoader.repoint_runtime(root)` |
| `src/tianshu/skills/loader.py` | `SkillsLoader.repoint_user_dir(root)` |
| `src/tianshu/config_manager.py` | `AgentConfigState` 加 universe 配置字段 |
| `src/tianshu/executor/*` | 诏令执行开始固化 `universe_id`（1a）+ 探索路由（1b） |
| `src/tianshu/scheduler/scheduler.py` | `register_system_jobs` 接收 `universe_evolver` 并注册 cron（1b） |
| `src/tianshu/gateway/api.py` | `/universes` 端点组 + 显式反馈端点 |
| `src/tianshu/app.py` | 装配 `UniverseManager`（1a）+ `UniverseEvolver`（1b）+ genesis 初始化 + 事件订阅 |
| `web/src/api/universe.ts`、`types.ts`、`pages/UniversePage.tsx`、`App.tsx`、`components/layout/AppSidebar.tsx`、`i18n/*` | 位面管理页 + 诏令结果赞踩入口 |

---

# Phase 1a — 位面基建（git-for-behavior，独立可交付）

## Task 1: Storage —`universes` 表、`memorials.universe_id`、CRUD

**Files:**
- Modify: `src/tianshu/storage.py`（`init_db` 的 executescript 块；`_migrate` 列表；新增 CRUD 方法；`save_memorial`/`update_memorial`/`_row_to_memorial`）

- [ ] **Step 1: 在 executescript 中建 `universes` 表**

在 `src/tianshu/storage.py` `init_db()` 主 `executescript`（约 67–490 行）内，紧接 `skill_metrics` 表（约 273 行）之后插入：

```sql
                CREATE TABLE IF NOT EXISTS universes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    parent_universe_id TEXT,
                    status TEXT NOT NULL DEFAULT 'challenger',
                    origin TEXT NOT NULL DEFAULT 'manual_branch',
                    mutation_reason TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    fitness_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_universe_single_champion
                    ON universes(status) WHERE status = 'champion';
```

> partial unique index 保证同一时刻仅一个 `champion`（在役=冠军，单真相源）。

- [ ] **Step 2: 在 `_migrate` 列表加 `memorials.universe_id`**

在 `src/tianshu/storage.py` `_migrate()`（约 501 行起）的 `migrations` 列表末尾加一项：

```python
            # 2026-06-07: 平行位面 — memorial 归因到所在位面
            "ALTER TABLE memorials ADD COLUMN universe_id TEXT",
```

（迁移循环已容忍 `duplicate column name`，无需额外处理。）

- [ ] **Step 3: 新增 Universe CRUD 方法**

在 `src/tianshu/storage.py` 任意 CRUD 区域（如 `last_activity_at` 之后，约 2254 行后）加入：

```python
    # --- Universes (平行位面) ---

    def save_universe(self, uni: dict) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO universes
                   (id, name, parent_universe_id, status, origin,
                    mutation_reason, description, fitness_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uni["id"], uni["name"], uni.get("parent_universe_id"),
                    uni["status"], uni["origin"], uni.get("mutation_reason"),
                    uni.get("description", ""),
                    json.dumps(uni.get("fitness", {}), ensure_ascii=False),
                    uni["created_at"],
                ),
            )

    def get_universe(self, universe_id: str) -> dict | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM universes WHERE id = ?", (universe_id,)
        ).fetchone()
        return self._row_to_universe(row) if row else None

    def list_universes(self, *, include_archived: bool = True) -> list[dict]:
        assert self._conn is not None
        sql = "SELECT * FROM universes"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY created_at DESC"
        return [self._row_to_universe(r) for r in self._conn.execute(sql).fetchall()]

    def get_champion_universe(self) -> dict | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM universes WHERE status = 'champion'"
        ).fetchone()
        return self._row_to_universe(row) if row else None

    def set_universe_status(self, universe_id: str, status: str) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.execute(
                "UPDATE universes SET status = ? WHERE id = ?", (status, universe_id)
            )

    def update_universe_fitness(self, universe_id: str, fitness: dict) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.execute(
                "UPDATE universes SET fitness_json = ? WHERE id = ?",
                (json.dumps(fitness, ensure_ascii=False), universe_id),
            )

    @staticmethod
    def _row_to_universe(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "parent_universe_id": row["parent_universe_id"],
            "status": row["status"],
            "origin": row["origin"],
            "mutation_reason": row["mutation_reason"],
            "description": row["description"],
            "fitness": json.loads(row["fitness_json"] or "{}"),
            "created_at": row["created_at"],
        }
```

确认文件顶部已 `import json` 与 `import sqlite3`（storage.py 已有）。

- [ ] **Step 4: memorial 读写带 `universe_id`**

在 `save_memorial`（约 932 行）的 `INSERT INTO memorials (...)` 列清单与 `VALUES` 占位中加入 `universe_id`，并在参数元组加 `memorial.universe_id`。
在 `update_memorial`（约 946 行）的 `UPDATE memorials SET ...` 中加入 `universe_id = ?`，参数加 `memorial.universe_id`。
在 `_row_to_memorial`（约 2449 行）构造 `Memorial(...)` 时加入：

```python
            universe_id=row["universe_id"] if "universe_id" in row.keys() else None,
```

> `row.keys()` 守卫兼容尚未迁移的旧库快照。

- [ ] **Step 5: 运行可见性验证**

Run:
```bash
.venv/bin/python -c "from tianshu.storage import Storage; import tempfile,os; p=os.path.join(tempfile.mkdtemp(),'t.db'); s=Storage(p); s.init_db(); s.save_universe({'id':'u1','name':'genesis','status':'champion','origin':'genesis','created_at':'2026-06-07T00:00:00+00:00'}); print(s.get_champion_universe()); print('OK')"
```
Expected: 打印 genesis 位面 dict + `OK`，无异常。

- [ ] **Step 6: Commit**

```bash
git add src/tianshu/storage.py
git commit -m "feat(universe): universes 表 + memorials.universe_id + CRUD"
```

---

## Task 2: `universe/model.py` — 数据契约

**Files:**
- Create: `src/tianshu/universe/__init__.py`
- Create: `src/tianshu/universe/model.py`

- [ ] **Step 1: 写 `__init__.py`**

```python
"""平行位面（parallel universe）— 行为配置的自进化分叉。"""
```

- [ ] **Step 2: 写 `model.py`**

```python
"""位面数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from ulid import ULID


class UniverseStatus(str, Enum):
    CHAMPION = "champion"      # 在役（唯一）
    CHALLENGER = "challenger"  # 候选
    ARCHIVED = "archived"      # 已归档（可恢复）


class UniverseOrigin(str, Enum):
    GENESIS = "genesis"            # 首次启用时捕获的初始位面
    MANUAL_BRANCH = "manual_branch"  # 人工分支
    MUTATION = "mutation"          # 演化引擎变异产生（1b）
    CODE_VARIANT = "code_variant"  # 预留：第二步代码变体位面


@dataclass(frozen=True)
class Universe:
    name: str
    status: UniverseStatus = UniverseStatus.CHALLENGER
    origin: UniverseOrigin = UniverseOrigin.MANUAL_BRANCH
    id: str = field(default_factory=lambda: str(ULID()))
    parent_universe_id: str | None = None
    mutation_reason: str | None = None
    description: str = ""
    fitness: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "parent_universe_id": self.parent_universe_id,
            "status": self.status.value,
            "origin": self.origin.value,
            "mutation_reason": self.mutation_reason,
            "description": self.description,
            "fitness": self.fitness,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Universe":
        return cls(
            id=row["id"],
            name=row["name"],
            parent_universe_id=row.get("parent_universe_id"),
            status=UniverseStatus(row["status"]),
            origin=UniverseOrigin(row["origin"]),
            mutation_reason=row.get("mutation_reason"),
            description=row.get("description", ""),
            fitness=row.get("fitness", {}),
            created_at=row["created_at"],
        )
```

- [ ] **Step 3: 验证导入**

Run: `.venv/bin/python -c "from tianshu.universe.model import Universe, UniverseStatus; u=Universe(name='x'); print(u.id, u.status.value); print(Universe.from_row(u.to_row()).name)"`
Expected: 打印一个 ULID + `challenger` + `x`。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/universe/__init__.py src/tianshu/universe/model.py
git commit -m "feat(universe): Universe 数据契约与枚举"
```

---

## Task 3: `universe/store.py` — 行为快照落盘/还原

**职责**：位面行为状态 = ① 人格 runtime 目录（SOUL/ROLE）② 技能 user 目录 ③ config 快照 JSON。Store 负责把"当前运行态"拷成一个位面目录，以及把某位面目录还原回去由 manager 调用。

**Files:**
- Create: `src/tianshu/universe/store.py`

- [ ] **Step 1: 写 `store.py`**

```python
"""位面行为快照的落盘与还原（全量拷贝，v1 简单安全）。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_PERSONAS = "personas"
_SKILLS = "skills"
_MANIFEST = "manifest.json"


class UniverseStore:
    """每个位面落在 ``{root}/{universe_id}/``：personas/ + skills/ + manifest.json。

    personas/ 与 skills/ 是当前 runtime 行为目录的全量拷贝；
    manifest.json 存 config 类快照（agent/LLM/providers/policy 等，JSON 可序列化）。
    """

    def __init__(
        self,
        root: Path,
        live_personas_dir: Path,
        live_skills_dir: Path,
    ) -> None:
        self._root = Path(root).expanduser()
        self._live_personas = Path(live_personas_dir).expanduser()
        self._live_skills = Path(live_skills_dir).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    def universe_dir(self, universe_id: str) -> Path:
        return self._root / universe_id

    def personas_dir(self, universe_id: str) -> Path:
        return self.universe_dir(universe_id) / _PERSONAS

    def skills_dir(self, universe_id: str) -> Path:
        return self.universe_dir(universe_id) / _SKILLS

    def exists(self, universe_id: str) -> bool:
        return (self.universe_dir(universe_id) / _MANIFEST).exists()

    def snapshot_live(self, universe_id: str, config_snapshot: dict) -> None:
        """把当前 live runtime 行为目录 + config 快照拷入新位面目录。"""
        dst = self.universe_dir(universe_id)
        dst.mkdir(parents=True, exist_ok=True)
        self._copy_tree(self._live_personas, dst / _PERSONAS)
        self._copy_tree(self._live_skills, dst / _SKILLS)
        self.write_manifest(universe_id, config_snapshot)

    def branch_from(self, parent_id: str, child_id: str) -> None:
        """从父位面目录全量拷贝出子位面目录（含 manifest）。"""
        src = self.universe_dir(parent_id)
        if not (src / _MANIFEST).exists():
            raise FileNotFoundError(f"parent universe dir missing: {src}")
        dst = self.universe_dir(child_id)
        if dst.exists():
            raise FileExistsError(f"universe dir already exists: {dst}")
        shutil.copytree(src, dst)

    def restore_to_live(self, universe_id: str) -> dict:
        """把某位面目录还原到 live runtime（覆盖 personas/ skills/），返回其 config 快照。"""
        src = self.universe_dir(universe_id)
        if not (src / _MANIFEST).exists():
            raise FileNotFoundError(f"universe dir missing: {src}")
        self._copy_tree(src / _PERSONAS, self._live_personas)
        self._copy_tree(src / _SKILLS, self._live_skills)
        return self.read_manifest(universe_id)

    def write_manifest(self, universe_id: str, config_snapshot: dict) -> None:
        path = self.universe_dir(universe_id) / _MANIFEST
        path.write_text(
            json.dumps(config_snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read_manifest(self, universe_id: str) -> dict:
        path = self.universe_dir(universe_id) / _MANIFEST
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _copy_tree(src: Path, dst: Path) -> None:
        """全量替换 dst 为 src 的内容（src 缺失则清空 dst）。"""
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: 验证**

Run:
```bash
.venv/bin/python - <<'PY'
import tempfile, os
from pathlib import Path
from tianshu.universe.store import UniverseStore
d = Path(tempfile.mkdtemp())
(p := d/"live_personas"/"bingbu").mkdir(parents=True); (p/"SOUL.md").write_text("soul")
(s := d/"live_skills").mkdir()
st = UniverseStore(d/"universes", d/"live_personas", d/"live_skills")
st.snapshot_live("u1", {"agent": {"x": 1}})
st.branch_from("u1", "u2")
assert (st.personas_dir("u2")/"bingbu"/"SOUL.md").read_text() == "soul"
assert st.read_manifest("u2") == {"agent": {"x": 1}}
print("OK")
PY
```
Expected: `OK`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/universe/store.py
git commit -m "feat(universe): UniverseStore 行为快照落盘/还原"
```

---

## Task 4: loaders 可重定向

**Files:**
- Modify: `src/tianshu/persona/loader.py`（加 `repoint_runtime`）
- Modify: `src/tianshu/skills/loader.py`（加 `repoint_user_dir`）

- [ ] **Step 1: PersonaLoader.repoint_runtime**

在 `src/tianshu/persona/loader.py` `runtime_dir` property（约 48–50 行）之后加入：

```python
    def repoint_runtime(self, new_runtime_dir: Path) -> None:
        """切换 runtime 人格根目录（位面切换时调用）并重载内存中的人格。"""
        self._runtime_dir = Path(new_runtime_dir).expanduser()
        self._personas.clear()
        self.load_all()
```

- [ ] **Step 2: SkillsLoader.repoint_user_dir**

先确认 `src/tianshu/skills/loader.py` 缓存清理方法名（约 64–68 行清 `_l1_cache`/`_l2_stats`/`_l2_metadata`）。在 `__init__` 之后加入：

```python
    def repoint_user_dir(self, new_user_dir) -> None:
        """切换 user 技能根目录（位面切换时调用）并失效所有缓存。"""
        from pathlib import Path
        self._user_dir = Path(new_user_dir).expanduser()
        self._l1_cache.clear()
        self._l2_stats.clear()
        self._l2_metadata = None
```

> 若该 loader 已有统一的缓存失效私有方法（如 `_invalidate_caches`），改为调用它而非手动清三个字段——以源码实际为准。

- [ ] **Step 3: 验证导入**

Run: `.venv/bin/python -c "from tianshu.persona.loader import PersonaLoader; from tianshu.skills.loader import SkillsLoader; print(hasattr(PersonaLoader,'repoint_runtime'), hasattr(SkillsLoader,'repoint_user_dir'))"`
Expected: `True True`。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/persona/loader.py src/tianshu/skills/loader.py
git commit -m "feat(universe): loader 根目录可重定向"
```

---

## Task 5: `memorials.universe_id` 模型字段 + ConfigManager 配置项

**Files:**
- Modify: `src/tianshu/models/memorial.py`
- Modify: `src/tianshu/config_manager.py`

- [ ] **Step 1: Memorial 加字段**

在 `src/tianshu/models/memorial.py` `Memorial` 类（`reasoning_content` 之后，约 56 行）加入：

```python
    # 2026-06-07: 平行位面 — 本 memorial 执行所属位面（开始时固化，不随后续切换改变）
    universe_id: str | None = None
```

- [ ] **Step 2: AgentConfigState 加 universe 配置**

在 `src/tianshu/config_manager.py` `AgentConfigState`（约 27–43 行）末尾加入（1a 只用 `parallel_universe_enabled`，其余供 1b）：

```python
    # 平行位面（parallel universe）
    parallel_universe_enabled: bool = False
    universe_explore_ratio: float = 0.1
    universe_min_samples: int = 20
    universe_promote_margin: float = 0.05
    universe_auto_promote: bool = False
    universe_evolver_idle_hours: int = 2
    universe_challenger_fail_limit: int = 5
    # fitness 权重（success/cost/audit/retry/feedback）
    universe_fitness_weights: tuple[float, ...] = (0.4, 0.15, 0.2, 0.1, 0.15)
```

> 沿用 curator 风格：读取处一律用 `getattr(cfg, "...", 默认)`，无需改 `update_agent_config`。

- [ ] **Step 3: 验证**

Run: `.venv/bin/python -c "from tianshu.config_manager import AgentConfigState; c=AgentConfigState(); print(c.parallel_universe_enabled, c.universe_explore_ratio)"`
Expected: `False 0.1`。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/models/memorial.py src/tianshu/config_manager.py
git commit -m "feat(universe): memorial.universe_id 字段 + 位面配置项"
```

---

## Task 6: `universe/manager.py` — genesis/branch/switch/rollback/diff/archive

**职责**：协调 Store（落盘）+ Storage（元数据）+ loaders/config（运行态重定向）。`config_apply` 是注入的回调，把 manifest 里的 config 快照应用回运行态（在 app.py 装配，见 Task 9）。

**Files:**
- Create: `src/tianshu/universe/manager.py`

- [ ] **Step 1: 写 `manager.py`**

```python
"""位面管理：genesis / branch / switch / rollback / diff / archive。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from tianshu.universe.model import Universe, UniverseOrigin, UniverseStatus
from tianshu.universe.store import UniverseStore

logger = logging.getLogger(__name__)


class UniverseManager:
    def __init__(
        self,
        storage: Any,
        store: UniverseStore,
        persona_loader: Any,
        skills_loader: Any,
        config_snapshot: Callable[[], dict],
        config_apply: Callable[[dict], None],
        event_bus: Any | None = None,
    ) -> None:
        self._storage = storage
        self._store = store
        self._personas = persona_loader
        self._skills = skills_loader
        self._config_snapshot = config_snapshot  # () -> dict
        self._config_apply = config_apply        # (dict) -> None
        self._bus = event_bus

    def attach_event_bus(self, bus: Any) -> None:
        self._bus = bus

    # --- queries ---

    def champion(self) -> dict | None:
        return self._storage.get_champion_universe()

    def champion_id(self) -> str | None:
        champ = self.champion()
        return champ["id"] if champ else None

    def list(self, *, include_archived: bool = True) -> list[dict]:
        return self._storage.list_universes(include_archived=include_archived)

    # --- genesis ---

    def ensure_genesis(self) -> dict:
        """首次启用：把当前运行态捕获为 genesis 位面并设为冠军；已存在则原样返回。"""
        champ = self.champion()
        if champ:
            return champ
        uni = Universe(
            name="创世位面",
            status=UniverseStatus.CHAMPION,
            origin=UniverseOrigin.GENESIS,
            description="首次启用平行位面时捕获的初始行为配置",
        )
        self._store.snapshot_live(uni.id, self._config_snapshot())
        self._storage.save_universe(uni.to_row())
        logger.info("Genesis universe created: %s", uni.id)
        self._emit("universe.created", {"universe_id": uni.id, "origin": "genesis"})
        return self._storage.get_universe(uni.id)

    # --- branch ---

    def branch(
        self,
        parent_id: str,
        name: str,
        *,
        origin: UniverseOrigin = UniverseOrigin.MANUAL_BRANCH,
        mutation_reason: str | None = None,
        description: str = "",
    ) -> dict:
        parent = self._storage.get_universe(parent_id)
        if not parent:
            raise ValueError(f"parent universe not found: {parent_id}")
        child = Universe(
            name=name,
            status=UniverseStatus.CHALLENGER,
            origin=origin,
            parent_universe_id=parent_id,
            mutation_reason=mutation_reason,
            description=description,
        )
        self._store.branch_from(parent_id, child.id)
        self._storage.save_universe(child.to_row())
        self._emit("universe.created", {
            "universe_id": child.id, "parent": parent_id, "origin": origin.value,
        })
        return self._storage.get_universe(child.id)

    # --- switch / rollback ---

    def switch(self, universe_id: str) -> dict:
        """把目标位面置为冠军、原冠军降级，并重定向运行态。"""
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if target["status"] == UniverseStatus.ARCHIVED.value:
            raise ValueError("cannot switch to an archived universe")
        if not self._store.exists(universe_id):
            raise FileNotFoundError(f"universe dir missing: {universe_id}")

        prev = self.champion()
        # 还原运行态目录 + 取回 config 快照
        manifest = self._store.restore_to_live(universe_id)
        self._personas.repoint_runtime(self._personas.runtime_dir)
        self._skills.repoint_user_dir(self._skills_user_dir())
        self._config_apply(manifest)

        # 翻转状态（先降原冠军，避免 partial-unique 冲突）
        if prev and prev["id"] != universe_id:
            self._storage.set_universe_status(prev["id"], UniverseStatus.CHALLENGER.value)
        self._storage.set_universe_status(universe_id, UniverseStatus.CHAMPION.value)
        self._emit("universe.switched", {
            "from": prev["id"] if prev else None, "to": universe_id,
        })
        return self._storage.get_universe(universe_id)

    # rollback 语义等同 switch 到历史位面
    rollback = switch

    # --- archive ---

    def archive(self, universe_id: str) -> dict:
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if target["status"] == UniverseStatus.CHAMPION.value:
            raise ValueError("cannot archive the champion (switch away first)")
        self._storage.set_universe_status(universe_id, UniverseStatus.ARCHIVED.value)
        self._emit("universe.archived", {"universe_id": universe_id})
        return self._storage.get_universe(universe_id)

    # --- diff ---

    def diff(self, a_id: str, b_id: str) -> dict:
        """对比两位面行为配置：人格文本、技能集、config 快照。"""
        return {
            "personas": self._diff_dir(
                self._store.personas_dir(a_id), self._store.personas_dir(b_id)
            ),
            "skills": self._diff_dir(
                self._store.skills_dir(a_id), self._store.skills_dir(b_id)
            ),
            "config": self._diff_config(
                self._store.read_manifest(a_id), self._store.read_manifest(b_id)
            ),
        }

    @staticmethod
    def _diff_dir(a: Path, b: Path) -> dict:
        def files(root: Path) -> dict[str, str]:
            out: dict[str, str] = {}
            if root.exists():
                for f in sorted(root.rglob("*")):
                    if f.is_file():
                        out[str(f.relative_to(root))] = f.read_text(
                            encoding="utf-8", errors="replace"
                        )
            return out
        fa, fb = files(a), files(b)
        keys = set(fa) | set(fb)
        return {
            "only_in_a": sorted(set(fa) - set(fb)),
            "only_in_b": sorted(set(fb) - set(fa)),
            "changed": sorted(k for k in keys if k in fa and k in fb and fa[k] != fb[k]),
        }

    @staticmethod
    def _diff_config(a: dict, b: dict) -> dict:
        keys = set(a) | set(b)
        return {k: {"a": a.get(k), "b": b.get(k)} for k in sorted(keys) if a.get(k) != b.get(k)}

    # --- helpers ---

    def _skills_user_dir(self) -> Path:
        return Path(self._skills._user_dir)  # noqa: SLF001 — loader 未暴露 property

    def _emit(self, event_type: str, payload: dict) -> None:
        if not self._bus:
            return
        from tianshu.models.events import make_event
        self._bus.fire(make_event(
            event_type=event_type, edict_id=None, memorial_id=None,
            producer="universe_manager", payload=payload,
        ))
```

> `repoint_runtime(self._personas.runtime_dir)` 看似不变根目录——因为人格/技能 runtime 目录是固定的（`~/.tianshu/personas`、`~/.tianshu/skills`），`restore_to_live` 已把目标位面内容覆盖进去；repoint 的作用是**重载内存中的人格 + 失效技能缓存**。这与 §5.2 "重新指向 loader + 清缓存"一致。

- [ ] **Step 2: 验证**（用真实 loader 的最小桩）

Run:
```bash
.venv/bin/python - <<'PY'
import tempfile, os
from pathlib import Path
from tianshu.storage import Storage
from tianshu.universe.store import UniverseStore
from tianshu.universe.manager import UniverseManager

d = Path(tempfile.mkdtemp())
(p := d/"personas"/"bingbu").mkdir(parents=True); (p/"SOUL.md").write_text("v1")
(d/"skills").mkdir()
db = str(d/"t.db"); s = Storage(db); s.init_db()
store = UniverseStore(d/"universes", d/"personas", d/"skills")

class FakePersona:
    runtime_dir = d/"personas"
    def repoint_runtime(self, x): pass
class FakeSkills:
    _user_dir = d/"skills"
    def repoint_user_dir(self, x): pass

cfg = {"agent": {"x": 1}}
mgr = UniverseManager(s, store, FakePersona(), FakeSkills(),
                      config_snapshot=lambda: cfg, config_apply=lambda m: None)
g = mgr.ensure_genesis(); print("genesis", g["id"], g["status"])
(p/"SOUL.md").write_text("v2")           # 改 live
ch = mgr.branch(g["id"], "实验位面")        # 分支当前(=v2)
print("branch", ch["status"])
mgr.switch(ch["id"])
print("champion now", mgr.champion_id() == ch["id"])
print("diff", mgr.diff(g["id"], ch["id"])["personas"]["changed"])
PY
```
Expected: 打印 genesis/branch 状态、`champion now True`、`diff ['bingbu/SOUL.md']`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/universe/manager.py
git commit -m "feat(universe): UniverseManager genesis/branch/switch/diff/archive"
```

---

## Task 7: executor — 诏令执行开始固化 `universe_id`

**Files:**
- Modify: `src/tianshu/executor/*`（memorial 进入执行/RUNNING 的落点）

- [ ] **Step 1: 定位落点**

Run: `grep -rn "TaskStatus.RUNNING\|started_at = \|\.started_at\b" src/tianshu/executor/`
找到 memorial 被置为执行中（设 `started_at`/`status=RUNNING`）并随后 `update_memorial`/`save_memorial` 的位置。

- [ ] **Step 2: 在该落点固化 universe_id**

在 memorial 即将首次以"执行中"持久化处，紧邻设置 `started_at` 的代码加入（`self._universe_manager` 由 Task 9 注入；用 `getattr` 守卫以兼容未注入场景）：

```python
        # 平行位面：执行开始时固化所属位面（不随后续切换改变）
        mgr = getattr(self, "_universe_manager", None)
        if mgr is not None and memorial.universe_id is None:
            memorial.universe_id = mgr.champion_id()
```

> 探索路由（把部分诏令分给候选位面）在 1b Task 14 覆盖；1a 这里只归因到冠军。

- [ ] **Step 3: 给 executor 加注入点**

在 executor 类中加一个 setter（仿 `set_persona_loader`）：

```python
    def set_universe_manager(self, manager) -> None:
        self._universe_manager = manager
```

- [ ] **Step 4: 验证**

Run: `.venv/bin/python -c "import tianshu.executor; print('import ok')"`（确保无语法错误；行为验证在 Task 11 集成测试）
Expected: `import ok`。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/executor/
git commit -m "feat(universe): 诏令执行开始固化 universe_id"
```

---

## Task 8: gateway — `/universes` 端点组

**Files:**
- Modify: `src/tianshu/gateway/api.py`

- [ ] **Step 1: 加端点组**

在 `src/tianshu/gateway/api.py` Scheduler 端点（约 615 行）之后插入。`ApiResponse` 已在文件内使用：

```python
# --- Universe (平行位面) endpoints ---


@gateway_router.get("/universes")
async def list_universes(request: Request):
    mgr = request.app.state.universe_manager
    return ApiResponse(success=True, data=mgr.list())


@gateway_router.get("/universes/{universe_id}")
async def get_universe(universe_id: str, request: Request):
    storage: Storage = request.app.state.storage
    uni = storage.get_universe(universe_id)
    if not uni:
        raise HTTPException(status_code=404, detail="universe not found")
    return ApiResponse(success=True, data=uni)


@gateway_router.post("/universes/{universe_id}/branch", response_model=ApiResponse)
async def branch_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    body = await request.json()
    name = (body or {}).get("name") or "新位面"
    try:
        uni = mgr.branch(universe_id, name, description=(body or {}).get("description", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=uni)


@gateway_router.post("/universes/{universe_id}/switch", response_model=ApiResponse)
async def switch_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    try:
        uni = mgr.switch(universe_id)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=uni)


@gateway_router.post("/universes/{universe_id}/archive", response_model=ApiResponse)
async def archive_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    try:
        uni = mgr.archive(universe_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=uni)


@gateway_router.get("/universes/diff")
async def diff_universes(request: Request, a: str = Query(...), b: str = Query(...)):
    mgr = request.app.state.universe_manager
    return ApiResponse(success=True, data=mgr.diff(a, b))
```

> 注意：`/universes/diff` 与 `/universes/{universe_id}` 路径冲突风险——FastAPI 按声明顺序匹配，故把 `diff` 放在 `{universe_id}` **之前** 声明，或将 diff 改为 `/universes/_diff`。本计划采用后者更稳：把上面 `@gateway_router.get("/universes/diff")` 改为 `@gateway_router.get("/universes/_diff")`。

- [ ] **Step 2: 验证**

Run: `.venv/bin/python -c "import tianshu.gateway.api; print('ok')"`
Expected: `ok`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/gateway/api.py
git commit -m "feat(universe): /universes API 端点组"
```

---

## Task 9: app.py — 装配 UniverseManager + genesis 初始化

**Files:**
- Modify: `src/tianshu/app.py`

- [ ] **Step 1: import**

在 `src/tianshu/app.py` 顶部 import 区（约 51 行 `from tianshu.skills.curator import SkillCurator` 附近）加入：

```python
from tianshu.universe.store import UniverseStore
from tianshu.universe.manager import UniverseManager
```

- [ ] **Step 2: 构造 config 快照/应用回调 + UniverseManager**

在 `skill_curator` 装配之后（约 608 行 `register_system_jobs` 之前），插入。`config_manager` 已在 lifespan 中实例化：

```python
    # --- 平行位面（parallel universe）---
    def _universe_config_snapshot() -> dict:
        ac = config_manager.agent_config
        from dataclasses import asdict
        return {"agent_config": asdict(ac)}

    def _universe_config_apply(manifest: dict) -> None:
        # v1：config 快照以 agent_config 为主，逐字段应用回 ConfigManager。
        ac = manifest.get("agent_config") or {}
        if ac:
            config_manager.update_agent_config(**{
                k: ac[k] for k in ("agent_max_iterations", "agent_timeout_seconds",
                                   "skills_char_budget") if k in ac
            })

    universe_store = UniverseStore(
        root=Path("~/.tianshu/universes").expanduser(),
        live_personas_dir=persona_loader.runtime_dir,
        live_skills_dir=skills._user_dir,
    )
    universe_manager = UniverseManager(
        storage=storage,
        store=universe_store,
        persona_loader=persona_loader,
        skills_loader=skills,
        config_snapshot=_universe_config_snapshot,
        config_apply=_universe_config_apply,
        event_bus=event_bus,
    )
    if config_manager.agent_config.parallel_universe_enabled:
        universe_manager.ensure_genesis()
    executor.set_universe_manager(universe_manager)
    app.state.universe_manager = universe_manager
```

> `Path` 已在 app.py 导入（确认；否则加 `from pathlib import Path`）。`skills` 是 SkillsLoader 实例（app.py:148/290）。

- [ ] **Step 3: 运行可见性验证（启动应用）**

Run: `.venv/bin/python -c "from tianshu.app import create_app; app=create_app(); print('app ok')"`（若 create_app 名称不同，以实际入口为准）
Expected: 无异常，打印 `app ok`。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/app.py
git commit -m "feat(universe): app 装配 UniverseManager + genesis 初始化"
```

---

## Task 10: web — 位面管理页

**Files:**
- Create: `web/src/api/universe.ts`
- Modify: `web/src/api/types.ts`
- Create: `web/src/pages/UniversePage.tsx`
- Modify: `web/src/App.tsx`、`web/src/components/layout/AppSidebar.tsx`、`web/src/i18n/*`

- [ ] **Step 1: types**

在 `web/src/api/types.ts` 加入：

```typescript
export interface Universe {
  id: string;
  name: string;
  parent_universe_id: string | null;
  status: "champion" | "challenger" | "archived";
  origin: "genesis" | "manual_branch" | "mutation" | "code_variant";
  mutation_reason: string | null;
  description: string;
  fitness: Record<string, number>;
  created_at: string;
}
```

- [ ] **Step 2: api/universe.ts**

```typescript
import apiClient from "./client";
import type { ApiResponse, Universe } from "./types";

export async function listUniverses(): Promise<ApiResponse<Universe[]>> {
  const { data } = await apiClient.get<ApiResponse<Universe[]>>("/universes");
  return data;
}

export async function branchUniverse(
  id: string, name: string, description = "",
): Promise<ApiResponse<Universe>> {
  const { data } = await apiClient.post<ApiResponse<Universe>>(
    `/universes/${id}/branch`, { name, description },
  );
  return data;
}

export async function switchUniverse(id: string): Promise<ApiResponse<Universe>> {
  const { data } = await apiClient.post<ApiResponse<Universe>>(`/universes/${id}/switch`, {});
  return data;
}

export async function archiveUniverse(id: string): Promise<ApiResponse<Universe>> {
  const { data } = await apiClient.post<ApiResponse<Universe>>(`/universes/${id}/archive`, {});
  return data;
}

export async function diffUniverses(
  a: string, b: string,
): Promise<ApiResponse<{ personas: unknown; skills: unknown; config: unknown }>> {
  const { data } = await apiClient.get<ApiResponse<{ personas: unknown; skills: unknown; config: unknown }>>(
    `/universes/_diff`, { params: { a, b } },
  );
  return data;
}
```

- [ ] **Step 3: UniversePage.tsx**

```tsx
import { useEffect, useState } from "react";
import { Card, Table, Tag, Button, Space, Input, Modal, message } from "antd";
import {
  listUniverses, branchUniverse, switchUniverse, archiveUniverse,
} from "../api/universe";
import type { Universe } from "../api/types";

const STATUS_COLOR: Record<string, string> = {
  champion: "gold", challenger: "blue", archived: "default",
};

export default function UniversePage() {
  const [rows, setRows] = useState<Universe[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const res = await listUniverses();
      if (res.success && res.data) setRows(res.data);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  const onBranch = (u: Universe) => {
    let name = "";
    Modal.confirm({
      title: `从「${u.name}」分支新位面`,
      content: <Input placeholder="位面名称" onChange={(e) => { name = e.target.value; }} />,
      onOk: async () => {
        const res = await branchUniverse(u.id, name || "新位面");
        if (res.success) { message.success("已分支"); void load(); }
      },
    });
  };
  const onSwitch = async (u: Universe) => {
    const res = await switchUniverse(u.id);
    if (res.success) { message.success(`已切换到「${u.name}」`); void load(); }
  };
  const onArchive = async (u: Universe) => {
    const res = await archiveUniverse(u.id);
    if (res.success) { message.success("已归档"); void load(); }
  };

  return (
    <Card title="位面管理" loading={loading}>
      <Table<Universe>
        rowKey="id"
        dataSource={rows}
        pagination={false}
        columns={[
          { title: "名称", dataIndex: "name" },
          {
            title: "状态", dataIndex: "status",
            render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
          },
          { title: "来源", dataIndex: "origin" },
          {
            title: "适应度", dataIndex: "fitness",
            render: (f: Record<string, number>) =>
              f && typeof f.score === "number" ? f.score.toFixed(3) : "—",
          },
          { title: "创建时间", dataIndex: "created_at" },
          {
            title: "操作",
            render: (_: unknown, u: Universe) => (
              <Space>
                <Button size="small" onClick={() => onBranch(u)}>分支</Button>
                {u.status !== "champion" && (
                  <Button size="small" type="primary" onClick={() => onSwitch(u)}>
                    切换/回滚
                  </Button>
                )}
                {u.status === "challenger" && (
                  <Button size="small" danger onClick={() => onArchive(u)}>归档</Button>
                )}
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
```

- [ ] **Step 4: 路由 + 侧边栏 + i18n**

在 `web/src/App.tsx`：import 区加 `import UniversePage from "./pages/UniversePage";`；在 `/scheduler` 路由（约 57 行）旁加 `<Route path="/universes" element={<UniversePage />} />`。

在 `web/src/components/layout/AppSidebar.tsx`：仿现有 `/scheduler` 菜单项（key/icon/label 三元组结构，约 62 行），新增一项 `{ key: "/universes", icon: <DeploymentUnitOutlined />, label: t("nav.universe") }`（从 `@ant-design/icons` import `DeploymentUnitOutlined`）。

在 i18n 资源（`web/src/i18n/` 内每个 locale 的 nav 段）加 `universe` 键：中文 `"位面"`、英文 `"Universes"`、`zh-classic` `"位面"`（沿用古风）。

- [ ] **Step 5: 验证**

Run: `cd web && npm run build`
Expected: 构建通过，无类型错误。

- [ ] **Step 6: Commit**

```bash
git add web/src
git commit -m "feat(universe): 位面管理页 + API client + 路由/侧边栏"
```

---

## Task 11: Phase 1a 测试集（功能后补，目标 80%）

**Files:**
- Create: `tests/universe/test_store.py`、`tests/universe/test_manager.py`、`tests/universe/test_storage_universe.py`、`tests/universe/test_universe_api.py`

- [ ] **Step 1: Store 单元测试**

```python
import shutil
from pathlib import Path
import pytest
from tianshu.universe.store import UniverseStore


@pytest.fixture
def store(tmp_path: Path) -> UniverseStore:
    (tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (tmp_path / "personas" / "bingbu" / "SOUL.md").write_text("soul-v1")
    (tmp_path / "skills").mkdir()
    return UniverseStore(tmp_path / "universes", tmp_path / "personas", tmp_path / "skills")


def test_snapshot_and_read_manifest(store: UniverseStore):
    store.snapshot_live("u1", {"agent_config": {"x": 1}})
    assert store.exists("u1")
    assert store.read_manifest("u1") == {"agent_config": {"x": 1}}
    assert (store.personas_dir("u1") / "bingbu" / "SOUL.md").read_text() == "soul-v1"


def test_branch_is_full_copy(store: UniverseStore):
    store.snapshot_live("u1", {"a": 1})
    store.branch_from("u1", "u2")
    assert (store.personas_dir("u2") / "bingbu" / "SOUL.md").read_text() == "soul-v1"


def test_branch_missing_parent_raises(store: UniverseStore):
    with pytest.raises(FileNotFoundError):
        store.branch_from("nope", "u2")


def test_restore_overwrites_live(store: UniverseStore):
    store.snapshot_live("u1", {"a": 1})
    (store._live_personas / "bingbu" / "SOUL.md").write_text("soul-v2")  # noqa: SLF001
    manifest = store.restore_to_live("u1")
    assert manifest == {"a": 1}
    assert (store._live_personas / "bingbu" / "SOUL.md").read_text() == "soul-v1"  # noqa: SLF001
```

- [ ] **Step 2: Manager 单元测试**（含切换、单冠军不变量、diff、归档守卫）

```python
from pathlib import Path
import pytest
from tianshu.storage import Storage
from tianshu.universe.store import UniverseStore
from tianshu.universe.manager import UniverseManager


class _FakePersona:
    def __init__(self, d: Path): self.runtime_dir = d
    def repoint_runtime(self, _): pass


class _FakeSkills:
    def __init__(self, d: Path): self._user_dir = d
    def repoint_user_dir(self, _): pass


@pytest.fixture
def mgr(tmp_path: Path) -> UniverseManager:
    (p := tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (p / "SOUL.md").write_text("v1")
    (tmp_path / "skills").mkdir()
    s = Storage(str(tmp_path / "t.db")); s.init_db()
    store = UniverseStore(tmp_path / "universes", tmp_path / "personas", tmp_path / "skills")
    cfg = {"agent_config": {}}
    return UniverseManager(
        s, store, _FakePersona(tmp_path / "personas"), _FakeSkills(tmp_path / "skills"),
        config_snapshot=lambda: cfg, config_apply=lambda m: None,
    )


def test_ensure_genesis_is_champion(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    assert g["status"] == "champion"
    assert mgr.ensure_genesis()["id"] == g["id"]  # idempotent


def test_branch_then_switch_keeps_single_champion(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")
    assert ch["status"] == "challenger"
    mgr.switch(ch["id"])
    champs = [u for u in mgr.list() if u["status"] == "champion"]
    assert len(champs) == 1 and champs[0]["id"] == ch["id"]


def test_cannot_archive_champion(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    with pytest.raises(ValueError):
        mgr.archive(g["id"])


def test_diff_detects_persona_change(mgr: UniverseManager, tmp_path: Path):
    g = mgr.ensure_genesis()
    (tmp_path / "personas" / "bingbu" / "SOUL.md").write_text("v2")
    ch = mgr.branch(g["id"], "exp")
    assert "bingbu/SOUL.md" in mgr.diff(g["id"], ch["id"])["personas"]["changed"]
```

- [ ] **Step 3: Storage 位面 CRUD 测试（无 FK 依赖，完全具体）**

```python
from tianshu.storage import Storage


def test_universe_crud_and_single_champion(tmp_path):
    s = Storage(str(tmp_path / "t.db")); s.init_db()
    s.save_universe({"id": "u1", "name": "genesis", "status": "champion",
                     "origin": "genesis", "created_at": "2026-06-07T00:00:00+00:00"})
    assert s.get_champion_universe()["id"] == "u1"
    s.save_universe({"id": "u2", "name": "exp", "status": "challenger",
                     "origin": "manual_branch", "created_at": "2026-06-07T01:00:00+00:00"})
    assert len(s.list_universes()) == 2
    # 切换：先降原冠军再升新冠军，partial-unique 不冲突
    s.set_universe_status("u1", "challenger")
    s.set_universe_status("u2", "champion")
    assert s.get_champion_universe()["id"] == "u2"
    s.update_universe_fitness("u2", {"score": 0.9, "samples": 30})
    assert s.get_universe("u2")["fitness"]["score"] == 0.9
```

- [ ] **Step 4: memorial.universe_id roundtrip（复用既有 fixture）**

复用 `tests/conftest.py` 中已有的 edict/memorial 工厂（搜索 `def memorial`/`make_memorial`/edict fixture）：建一个前置 edict，构造 `Memorial(edict_id=..., universe_id="u1")`，`s.save_memorial(m)` 后断言 `s.get_memorial(m.id).universe_id == "u1"`。无现成 fixture 时，先 `s.save_edict(edict)` 建满足 FK 的 edict 再写 memorial。

- [ ] **Step 5: API 测试**（FastAPI TestClient）

仿 `tests/` 既有 gateway 测试风格，覆盖：`GET /universes` 返回列表；`POST /universes/{id}/branch` 创建候选；`POST /universes/{id}/switch` 翻转冠军；归档冠军返回 400。

- [ ] **Step 6: 跑测试 + 覆盖率**

Run: `.venv/bin/python -m pytest tests/universe/ -v --cov=src/tianshu/universe --cov-report=term-missing`
Expected: 全绿，`universe/` 覆盖率 ≥ 80%。

- [ ] **Step 7: Commit**

```bash
git add tests/universe/
git commit -m "test(universe): Phase 1a 单元/集成测试"
```

---

# Phase 1b — 自进化选优（基建之上）

## Task 12: `universe/fitness.py` — 适应度聚合

**Files:**
- Create: `src/tianshu/universe/fitness.py`
- Modify: `src/tianshu/storage.py`（显式反馈存储 + 按 universe 聚合 memorial 的查询）

- [ ] **Step 1: Storage 加显式反馈 + 聚合查询**

在 `src/tianshu/storage.py` Universe CRUD 区追加：

```python
    def add_universe_feedback(self, universe_id: str, memorial_id: str, score: int) -> None:
        """score: +1 赞 / -1 踩。复用 events 表存证，避免新表。"""
        from tianshu.models.events import make_event
        ev = make_event(
            event_type="universe.feedback", edict_id=None, memorial_id=memorial_id,
            producer="user", payload={"universe_id": universe_id, "score": score},
        )
        self.save_event(ev)  # 以实际 event 持久化方法名为准

    def universe_memorial_stats(self, universe_id: str) -> dict:
        """聚合某位面下 memorial 的成功/失败/重试/成本/审计。"""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT status, attempt, usage_json, audit_json FROM memorials "
            "WHERE universe_id = ?", (universe_id,)
        ).fetchall()
        total = len(rows)
        success = sum(1 for r in rows if r["status"] in ("completed", "approved"))
        retries = sum(max(0, (r["attempt"] or 1) - 1) for r in rows)
        audited = [r for r in rows if r["audit_json"]]
        audit_pass = sum(
            1 for r in audited if '"passed": true' in (r["audit_json"] or "").lower()
        )
        cost = 0.0
        for r in rows:
            try:
                cost += json.loads(r["usage_json"] or "{}").get("cost_cny", 0.0) or 0.0
            except (ValueError, TypeError):
                pass
        return {
            "total": total, "success": success, "retries": retries,
            "audited": len(audited), "audit_pass": audit_pass, "cost": cost,
        }
```

> 显式反馈用 `events` 表（type=`universe.feedback`）存证即可，无需新表（YAGNI）。聚合反馈分时按 universe_id 过滤 events——若 events 查询不便，1b 起步可只用隐式指标，反馈权重置 0，留 §10 注。

- [ ] **Step 2: fitness.py**

```python
"""位面适应度：隐式指标 + 显式反馈 → 综合分（越高越好）。"""

from __future__ import annotations


def _safe_ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def compute_fitness(
    stats: dict,
    feedback_score: float = 0.0,
    *,
    weights: tuple[float, ...] = (0.4, 0.15, 0.2, 0.1, 0.15),
) -> dict:
    """weights = (success, cost, audit, retry, feedback)。

    各分项归一到 [0,1]，综合分加权求和。cost/retry 为"越低越好"，取反向分。
    """
    w_succ, w_cost, w_audit, w_retry, w_fb = weights
    total = stats.get("total", 0)
    success_rate = _safe_ratio(stats.get("success", 0), total)
    audit_rate = _safe_ratio(stats.get("audit_pass", 0), stats.get("audited", 0))
    # 反向分：平均重试越多分越低（>=2 次重试视为 0 分）
    avg_retries = _safe_ratio(stats.get("retries", 0), total)
    retry_score = max(0.0, 1.0 - avg_retries / 2.0)
    # 反向分：平均成本，缺乏绝对基线时用"有成功即满分、全失败减分"的弱代理
    avg_cost = _safe_ratio(int(stats.get("cost", 0) * 1000), total)  # 仅作占位刻度
    cost_score = 1.0 / (1.0 + avg_cost) if avg_cost > 0 else 1.0
    # 反馈归一：feedback_score ∈ 任意整数 → squash 到 [0,1]
    fb_norm = 0.5 + 0.5 * (feedback_score / (1 + abs(feedback_score)))

    score = (
        w_succ * success_rate + w_cost * cost_score + w_audit * audit_rate
        + w_retry * retry_score + w_fb * fb_norm
    )
    return {
        "score": round(score, 4),
        "samples": total,
        "success_rate": round(success_rate, 4),
        "audit_rate": round(audit_rate, 4),
        "retry_score": round(retry_score, 4),
        "cost_score": round(cost_score, 4),
        "feedback": feedback_score,
    }
```

- [ ] **Step 3: 验证**

Run: `.venv/bin/python -c "from tianshu.universe.fitness import compute_fitness; print(compute_fitness({'total':10,'success':9,'audited':8,'audit_pass':7,'retries':2,'cost':0.5}, 3))"`
Expected: 打印含 `score`/`samples`/`success_rate` 的 dict，score 在 (0,1)。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/universe/fitness.py src/tianshu/storage.py
git commit -m "feat(universe): 适应度聚合 fitness + 显式反馈存储"
```

---

## Task 13: bus 订阅 — memorial 完成更新 fitness

**Files:**
- Modify: `src/tianshu/app.py`（事件订阅链，约 336–346 行风格）

- [ ] **Step 1: 加 fitness 更新 handler**

在 `src/tianshu/app.py` Task 9 装配 `universe_manager` 之后、事件订阅链处加入一个闭包 handler 并订阅 `execution.completed` / `execution.failed` / `audit.completed`：

```python
    from tianshu.universe.fitness import compute_fitness

    async def _update_universe_fitness(event) -> None:
        if not config_manager.agent_config.parallel_universe_enabled:
            return
        mem_id = event.memorial_id
        if not mem_id:
            return
        mem = storage.get_memorial(mem_id)
        uid = getattr(mem, "universe_id", None) if mem else None
        if not uid:
            return
        stats = storage.universe_memorial_stats(uid)
        weights = config_manager.agent_config.universe_fitness_weights
        fitness = compute_fitness(stats, weights=weights)
        storage.update_universe_fitness(uid, fitness)

    event_bus.subscribe("execution.completed", _update_universe_fitness, priority=250)
    event_bus.subscribe("audit.completed", _update_universe_fitness, priority=250)
    event_bus.subscribe("execution.failed", _update_universe_fitness, priority=250)
```

> 订阅 API 名以实际为准（app.py 现有 `event_bus.subscribe(...)` 用法，约 336 行）。priority 取 250（在 memory_manager 的 200 之后）。

- [ ] **Step 2: 验证**

Run: `.venv/bin/python -c "from tianshu.app import create_app; create_app(); print('ok')"`
Expected: `ok`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/app.py
git commit -m "feat(universe): memorial 完成事件回流更新位面适应度"
```

---

## Task 14: executor — 探索路由（小流量分给候选位面）

**Files:**
- Modify: `src/tianshu/executor/*`（Task 7 的 universe_id 固化落点）

- [ ] **Step 1: 改写固化逻辑为"探索路由"**

把 Task 7 Step 2 的固化代码替换为带探索分流的版本。用 ULID 末位十六进制做无随机依赖的确定性分流（`Date.now`/`random` 在某些约束下不可用，且便于复现）：

```python
        # 平行位面：执行开始时决定本诏令所属位面（冠军 or 小流量探索候选）
        mgr = getattr(self, "_universe_manager", None)
        if mgr is not None and memorial.universe_id is None:
            memorial.universe_id = mgr.route_for_memorial(memorial.id)
```

- [ ] **Step 2: UniverseManager 加 route_for_memorial**

在 `src/tianshu/universe/manager.py` 加入（用 memorial.id 末字符的十六进制值 % 100 与 explore_ratio 比较，确定性、可复现、无随机源）：

```python
    def route_for_memorial(self, memorial_id: str) -> str | None:
        """返回本 memorial 应归属的位面：默认冠军；按 explore_ratio 概率分给在线候选。"""
        champ_id = self.champion_id()
        cfg = self._agent_config()
        if not getattr(cfg, "parallel_universe_enabled", False):
            return champ_id
        ratio = getattr(cfg, "universe_explore_ratio", 0.1)
        challengers = [
            u for u in self.list(include_archived=False)
            if u["status"] == "challenger"
        ]
        if not challengers or ratio <= 0:
            return champ_id
        bucket = int(memorial_id[-2:], 16) % 100 if memorial_id else 0
        if bucket < int(ratio * 100):
            # 轮流分给候选（用 memorial 哈希选一个，确定性）
            idx = int(memorial_id[-4:-2] or "0", 16) % len(challengers)
            return challengers[idx]["id"]
        return champ_id
```

并在 `UniverseManager.__init__` 增加 `agent_config: Callable[[], Any]` 注入（返回 `config_manager.agent_config`），存为 `self._agent_config`；在 app.py Task 9 构造处补 `agent_config=lambda: config_manager.agent_config`。

> 注意：系统/定时诏令默认不参与探索（§7）。若 memorial 来自系统 cron（如 `profile.daily_synthesis`），在固化前判断并跳过路由直接归冠军——以 executor 现有"是否系统诏令"标识为准（grep `system\|cron` in executor）。

- [ ] **Step 3: 验证**

Run:
```bash
.venv/bin/python - <<'PY'
# route_for_memorial 在 disabled 时恒返回冠军
from unittest.mock import MagicMock
from tianshu.universe.manager import UniverseManager
m = UniverseManager.__new__(UniverseManager)
m._storage = MagicMock(); m._storage.get_champion_universe.return_value = {"id": "champ"}
m._agent_config = lambda: type("C", (), {"parallel_universe_enabled": False})()
print(m.route_for_memorial("0123456789ABCDEF") == "champ")
PY
```
Expected: `True`。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/executor/ src/tianshu/universe/manager.py
git commit -m "feat(universe): 诏令探索路由（小流量分给候选位面）"
```

---

## Task 15: `universe/evolver.py` — 演化引擎

**职责**：照搬 `SkillCurator` 骨架（gate: idle+lock → 采信号 → LLM 变异 → 分支候选 → 熔断 → 晋升推荐）。一次只产一个候选、动一处变异。

**Files:**
- Create: `src/tianshu/universe/evolver.py`

- [ ] **Step 1: 写 `evolver.py`**

```python
"""UniverseEvolver（演化）— 从冠军位面变异出候选并据适应度择优。

骨架对齐 SkillCurator：gate(idle + lock) → 采信号 → ONE LLM 变异 →
分支候选位面 → 熔断下线劣质候选 → 晋升推荐（默认人工确认）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from tianshu.universe.model import UniverseOrigin, UniverseStatus

logger = logging.getLogger(__name__)

_LOCK_KEY = "__universe_evolver__"

_SYSTEM = (
    "你是天枢的「演化」官，负责让宫殿的行为配置随使用越来越贴合主上。"
    "给定冠军位面的行为概要与各位面适应度，你只提出【一处】定向变异，"
    "用以分支出一个候选位面去试验。严禁臆造，只输出 JSON 对象。"
)

_USER = """\
冠军位面适应度：{champion_fitness}
各候选位面适应度：{challenger_fitness}
冠军行为概要（人格/技能/策略要点）：
{summary}

请提出【一处】可能提升贴合度的定向变异，输出 JSON：
{{"target": "persona:bingbu/ROLE.md | policy | config | skillset",
  "reason": "为何这样改可能更好",
  "name": "候选位面名称（简短中文）"}}
若当前无明确可改之处，输出 {{"target": null, "reason": "...", "name": null}}。"""


@dataclass
class EvolveResult:
    skipped: str | None = None
    created_challenger: str | None = None
    mutation_reason: str | None = None
    retired: list[str] = field(default_factory=list)
    promotion_recommended: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skipped": self.skipped,
            "created_challenger": self.created_challenger,
            "mutation_reason": self.mutation_reason,
            "retired": self.retired,
            "promotion_recommended": self.promotion_recommended,
            "errors": self.errors,
        }


class UniverseEvolver:
    def __init__(
        self,
        llm_client: Any,
        manager: Any,
        storage: Any,
        config_manager: Any,
    ) -> None:
        self._llm = llm_client
        self._mgr = manager
        self._storage = storage
        self._config = config_manager
        self._bus: Any | None = None

    def attach_event_bus(self, bus: Any) -> None:
        self._bus = bus

    # --- gate ---

    def _idle_ok(self, idle_hours: int) -> bool:
        from tianshu.skills.curator import _age_hours  # 复用同款 idle 判断
        last = self._storage.last_activity_at()
        age = _age_hours(last)
        return age is None or age >= idle_hours

    # --- orchestration ---

    async def run(self, trigger_source: str = "manual") -> EvolveResult:
        cfg = self._config.agent_config
        if not getattr(cfg, "parallel_universe_enabled", False):
            return EvolveResult(skipped="disabled")
        if not self._idle_ok(getattr(cfg, "universe_evolver_idle_hours", 2)):
            return EvolveResult(skipped="not_idle")
        if not self._storage.try_acquire_synthesis_lock(_LOCK_KEY):
            return EvolveResult(skipped="lock_held")

        result = EvolveResult()
        try:
            champ = self._mgr.champion()
            if not champ:
                return EvolveResult(skipped="no_champion")

            # 1) 熔断：连续失败超阈值的候选下线
            result.retired = self._retire_failing_challengers(cfg)

            # 2) 晋升推荐：候选稳定超过冠军 → 推荐（默认人工确认）/ 自动晋升
            result.promotion_recommended = await self._maybe_promote(champ, cfg)

            # 3) 变异：产出一个新候选
            mutation = await self._propose_mutation(champ)
            if mutation and mutation.get("target"):
                child = self._mgr.branch(
                    champ["id"], mutation.get("name") or "演化候选",
                    origin=UniverseOrigin.MUTATION,
                    mutation_reason=mutation.get("reason"),
                    description=f"target={mutation.get('target')}",
                )
                # NOTE: v1 仅记录变异意图于 mutation_reason；实际改写候选目录的
                # SOUL/ROLE/policy 由后续增量实现（见计划 §限制）。
                result.created_challenger = child["id"]
                result.mutation_reason = mutation.get("reason")

            await self._emit("universe.evolved", result.to_dict())
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("[EVOLVER] run failed")
            result.errors.append(str(e))
            return result
        finally:
            self._storage.release_synthesis_lock(_LOCK_KEY)

    # --- steps ---

    def _retire_failing_challengers(self, cfg: Any) -> list[str]:
        limit = getattr(cfg, "universe_challenger_fail_limit", 5)
        retired: list[str] = []
        for u in self._mgr.list(include_archived=False):
            if u["status"] != UniverseStatus.CHALLENGER.value:
                continue
            stats = self._storage.universe_memorial_stats(u["id"])
            fails = stats["total"] - stats["success"]
            if stats["total"] >= limit and fails >= limit:
                self._mgr.archive(u["id"])
                retired.append(u["id"])
        return retired

    async def _maybe_promote(self, champ: dict, cfg: Any) -> str | None:
        min_samples = getattr(cfg, "universe_min_samples", 20)
        margin = getattr(cfg, "universe_promote_margin", 0.05)
        auto = getattr(cfg, "universe_auto_promote", False)
        champ_score = (champ.get("fitness") or {}).get("score", 0.0)
        best: tuple[str, float] | None = None
        for u in self._mgr.list(include_archived=False):
            if u["status"] != UniverseStatus.CHALLENGER.value:
                continue
            f = u.get("fitness") or {}
            if f.get("samples", 0) < min_samples:
                continue
            score = f.get("score", 0.0)
            if score >= champ_score + margin and (best is None or score > best[1]):
                best = (u["id"], score)
        if not best:
            return None
        winner_id = best[0]
        if auto:
            self._mgr.switch(winner_id)
            await self._emit("universe.promoted", {"universe_id": winner_id, "auto": True})
        else:
            await self._emit("universe.promotion_recommended", {
                "universe_id": winner_id, "score": best[1], "champion_score": champ_score,
            })
        return winner_id

    async def _propose_mutation(self, champ: dict) -> dict:
        challengers = [
            u for u in self._mgr.list(include_archived=False)
            if u["status"] == UniverseStatus.CHALLENGER.value
        ]
        prompt = _USER.format(
            champion_fitness=json.dumps(champ.get("fitness", {}), ensure_ascii=False),
            challenger_fitness=json.dumps(
                [c.get("fitness", {}) for c in challengers], ensure_ascii=False),
            summary=self._champion_summary(champ),
        )
        for _ in range(3):
            try:
                resp = await self._llm.chat(messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ])
                text = (getattr(resp, "content", None) or "").strip()
                if text.startswith("```") and "\n" in text:
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                prompt += "\n\n上次输出非合法 JSON，严格只输出 JSON。"
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1)
        return {}

    def _champion_summary(self, champ: dict) -> str:
        # v1：用 manifest + 人格文件名清单做轻量概要（避免塞入全文）。
        store = self._mgr._store  # noqa: SLF001
        pdir = store.personas_dir(champ["id"])
        personas = sorted(p.name for p in pdir.glob("*")) if pdir.exists() else []
        return f"人格: {personas}; config: {list(store.read_manifest(champ['id']).keys())}"

    async def _emit(self, event_type: str, payload: dict) -> None:
        if not self._bus:
            return
        from tianshu.models.events import make_event
        self._bus.fire(make_event(
            event_type=event_type, edict_id=None, memorial_id=None,
            producer="universe_evolver", payload=payload,
        ))
```

> **本计划的明确限制（写入 §限制并 log）**：1b 起步的演化引擎完成"采信号→提变异意图→分支候选→熔断→晋升推荐/自动晋升"的**闭环骨架**，但"把变异意图实际改写进候选位面的 SOUL/ROLE/policy 文件"留作 1b 的增量后续（需要对每类 target 的安全改写器）。候选位面先以"冠军全量拷贝 + 记录 mutation_reason"存在，承接探索流量做对照基线；改写器落地前，候选≈冠军，晋升无害。这是有意的分步，避免一次引入自动改人格文件的风险面。

- [ ] **Step 2: 验证**

Run: `.venv/bin/python -c "from tianshu.universe.evolver import UniverseEvolver, EvolveResult; print(EvolveResult(skipped='x').to_dict()['skipped'])"`
Expected: `x`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/universe/evolver.py
git commit -m "feat(universe): UniverseEvolver 演化引擎（变异/熔断/晋升推荐）"
```

---

## Task 16: scheduler + app.py — 注册演化 cron

**Files:**
- Modify: `src/tianshu/scheduler/scheduler.py`（`register_system_jobs` 增参）
- Modify: `src/tianshu/app.py`（构造 evolver + 传参）

- [ ] **Step 1: register_system_jobs 增参**

在 `src/tianshu/scheduler/scheduler.py` `register_system_jobs`（约 84 行）签名增 `universe_evolver: Any = None`，并在方法体 `skill_curator` 注册块之后加：

```python
        if universe_evolver is not None:
            async def _fire_evolve() -> None:
                asyncio.create_task(universe_evolver.run(trigger_source="cron"))

            self._system_jobs.append(
                {"cron": "0 5 * * *", "name": "universe.daily_evolve", "fn": _fire_evolve}
            )
            logger.info("Registered system job: universe.daily_evolve (0 5 * * *)")
```

- [ ] **Step 2: app.py 构造 evolver + 传参**

在 `src/tianshu/app.py` Task 9 装配处之后、`register_system_jobs` 调用（约 608 行）之前加：

```python
    from tianshu.universe.evolver import UniverseEvolver
    universe_evolver = UniverseEvolver(
        llm_client=llm_client,         # 以 app.py 中 LLMClient 实例名为准
        manager=universe_manager,
        storage=storage,
        config_manager=config_manager,
    )
    universe_evolver.attach_event_bus(event_bus)
    app.state.universe_evolver = universe_evolver
```

把 `scheduler.register_system_jobs(profile_trigger, skill_curator=skill_curator)` 改为：

```python
    scheduler.register_system_jobs(
        profile_trigger, skill_curator=skill_curator, universe_evolver=universe_evolver,
    )
```

> `llm_client` 用 app.py 中既有 LLMClient 实例（grep `LLMClient(` / `llm_client` in app.py）。

- [ ] **Step 3: 验证**

Run: `.venv/bin/python -c "from tianshu.app import create_app; create_app(); print('ok')"`
Expected: `ok`。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/scheduler/scheduler.py src/tianshu/app.py
git commit -m "feat(universe): 注册演化 cron（universe.daily_evolve）"
```

---

## Task 17: gateway + web — 显式反馈 + 适应度展示 + 手动演化

**Files:**
- Modify: `src/tianshu/gateway/api.py`、`web/src/api/universe.ts`、`web/src/pages/UniversePage.tsx`、`web/src/pages/EdictDetailPage.tsx`

- [ ] **Step 1: 反馈 + 手动演化端点**

在 `src/tianshu/gateway/api.py` 位面端点组追加：

```python
@gateway_router.post("/universes/feedback", response_model=ApiResponse)
async def universe_feedback(request: Request):
    storage: Storage = request.app.state.storage
    body = await request.json()
    mem = storage.get_memorial(body["memorial_id"])
    uid = getattr(mem, "universe_id", None) if mem else None
    if uid:
        storage.add_universe_feedback(uid, body["memorial_id"], int(body["score"]))
    return ApiResponse(success=True, data={"universe_id": uid})


@gateway_router.post("/universes/evolve", response_model=ApiResponse)
async def trigger_evolve(request: Request):
    evolver = request.app.state.universe_evolver
    result = await evolver.run(trigger_source="manual")
    return ApiResponse(success=True, data=result.to_dict())
```

- [ ] **Step 2: web — 反馈按钮 + 演化触发**

在 `web/src/api/universe.ts` 加 `submitUniverseFeedback(memorialId, score)` 与 `triggerEvolve()`（仿 Task 10 风格，POST `/universes/feedback`、`/universes/evolve`）。
在 `web/src/pages/EdictDetailPage.tsx` 诏令结果区加一对赞/踩按钮，调用 `submitUniverseFeedback(memorialId, +1/-1)`。
在 `web/src/pages/UniversePage.tsx` 顶部加「手动演化」按钮调用 `triggerEvolve()`；适应度列已在 Task 10 渲染 `fitness.score`。

- [ ] **Step 3: 验证**

Run: `.venv/bin/python -c "import tianshu.gateway.api; print('ok')" && cd web && npm run build`
Expected: `ok` + 前端构建通过。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/gateway/api.py web/src
git commit -m "feat(universe): 显式反馈 + 手动演化触发 + 适应度展示"
```

---

## Task 18: Phase 1b 测试集（功能后补，目标 80%）

**Files:**
- Create: `tests/universe/test_fitness.py`、`tests/universe/test_routing.py`、`tests/universe/test_evolver.py`

- [ ] **Step 1: fitness 单元测试**

```python
from tianshu.universe.fitness import compute_fitness


def test_all_success_scores_high():
    f = compute_fitness({"total": 10, "success": 10, "audited": 10, "audit_pass": 10,
                         "retries": 0, "cost": 0.0}, 5)
    assert f["score"] > 0.8 and f["samples"] == 10


def test_all_fail_scores_low():
    f = compute_fitness({"total": 10, "success": 0, "audited": 10, "audit_pass": 0,
                         "retries": 20, "cost": 5.0}, -5)
    assert f["score"] < 0.3


def test_zero_samples_no_crash():
    f = compute_fitness({"total": 0, "success": 0, "audited": 0, "audit_pass": 0,
                         "retries": 0, "cost": 0.0})
    assert f["samples"] == 0
```

- [ ] **Step 2: 路由测试**（确定性分流）

```python
from unittest.mock import MagicMock
from tianshu.universe.manager import UniverseManager


def _mgr(enabled, ratio, challengers):
    m = UniverseManager.__new__(UniverseManager)
    m._storage = MagicMock()
    m._storage.get_champion_universe.return_value = {"id": "champ"}
    m._storage.list_universes.return_value = (
        [{"id": "champ", "status": "champion"}]
        + [{"id": f"c{i}", "status": "challenger"} for i in range(challengers)]
    )
    m._agent_config = lambda: type("C", (), {
        "parallel_universe_enabled": enabled, "universe_explore_ratio": ratio})()
    return m


def test_disabled_always_champion():
    m = _mgr(False, 0.5, 2)
    assert m.route_for_memorial("00000000000000FF") == "champ"


def test_no_challengers_returns_champion():
    m = _mgr(True, 0.5, 0)
    assert m.route_for_memorial("00000000000000FF") == "champ"


def test_low_bucket_routes_to_challenger():
    m = _mgr(True, 1.0, 2)  # ratio=1.0 → 全部走候选
    assert m.route_for_memorial("00000000000000AA").startswith("c")
```

- [ ] **Step 3: evolver 测试**（mock LLM/manager/storage）

覆盖：`parallel_universe_enabled=False` → `skipped="disabled"`；候选 total≥limit 且 fails≥limit → 被 `archive`；候选 fitness.samples<min_samples → 不推荐；候选 score≥champ+margin 且样本足 → 触发 `universe.promotion_recommended`（auto=False 不切换）。用 `MagicMock` 注入 manager/storage，`AsyncMock` 注入 llm。

- [ ] **Step 4: 跑测试 + 覆盖率**

Run: `.venv/bin/python -m pytest tests/universe/ -v --cov=src/tianshu/universe --cov-report=term-missing`
Expected: 全绿，`universe/` 覆盖率 ≥ 80%。

- [ ] **Step 5: Commit**

```bash
git add tests/universe/
git commit -m "test(universe): Phase 1b fitness/routing/evolver 测试"
```

---

## §限制（有意的分步，写入实现说明）

- **演化变异改写器**：1b 完成演化闭环骨架（采信号→变异意图→分支候选→熔断→晋升推荐/自动晋升）。把变异意图**实际改写进候选位面文件**（按 target 类型的安全改写器：persona ROLE/SOUL、policy、config、skillset）留作 1b 增量后续。改写器落地前候选≈冠军，承接探索流量做对照基线，晋升无害。
- **cost fitness 分项**：缺绝对基线，v1 用弱代理刻度；后续可接 `cost_ledger` 真实成本做归一。
- **影子重放评估**：不做（§4 非目标），仅小流量真实探索。

## §第二步扩展点（代码变体位面，本计划不实现）

- `UniverseOrigin.CODE_VARIANT` 枚举已预留。
- `manifest.json` 预留"代码层"段（v1 为空）。
- `UniverseManager.switch` 以"还原一组可重定向运行态"为抽象，未来代码变体作为新重定向维度接入，不改 1a/1b 契约。
