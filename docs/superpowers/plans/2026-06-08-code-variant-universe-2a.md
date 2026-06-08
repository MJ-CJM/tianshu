# 代码变体位面 · 增量 2a（代码变体基底）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一个位面能携带一份 git worktree 代码变体——支持从 champion 分支出代码变体位面（worktree 落盘 + code_ref 持久化）、git diff 看差异、归档回收 worktree、恢复重建。

**Architecture:** 新增 `CodeVariantStore`（git worktree 生命周期，与做文件拷贝的 `UniverseStore` 正交、单一职责）。`Universe` 模型与 `universes` 表加 `code_ref`。`UniverseManager` 增加代码变体路径：`branch_code_variant` / `code_diff`，并让 `archive`/`restore` 按 `code_ref` 分派到 worktree 的 gc/重建；`switch`（重定向+重启）属 2d Deployer，2a 对代码变体显式拦截报错。app 装配 `CodeVariantStore` 并注入 manager。

**Tech Stack:** Python 3.12 · git worktree（`subprocess`）· SQLite · pytest · frozen dataclass（`dataclasses.replace`）

**Scope（明确边界）：** 2a 只交付 substrate（model / storage / store / manager）+ app 接线，验收在 manager 层。**移出 2a 的**：沙箱运行器/评估/自动生成/晋升（2b/2c/2d）；代码变体的 API/UI/CLI 暴露（避免集成测试污染真实仓库，且与 2d 审核 UI 重叠，留待后续增量）；代码变体 `switch`/promotion（属 2d Deployer，本增量显式拦截）。

**依据 spec：** `docs/superpowers/specs/2026-06-08-code-variant-universe-design.md`（commit 14864ce），§3.1 / §5.1 / §5.2 / §5.8 / §6 / §9 / §13。

**测试取向：** 按 spec §9——git worktree 生命周期属 substrate，配基础单测（本计划 TDD 编排）。若按你"功能优先、测试最后补"偏好，可合并测试步骤，但建议保留 git substrate 的单测。

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `src/tianshu/universe/model.py` | 改 | `Universe` 加 `code_ref` 字段 + to_row/from_row |
| `src/tianshu/storage.py` | 改 | `universes` 表 DDL + 迁移 + save/row 转换支持 `code_ref` |
| `src/tianshu/universe/code_store.py` | 新建 | `CodeVariantStore`：git worktree branch/diff/gc/restore |
| `src/tianshu/universe/manager.py` | 改 | `code_store` 注入 + `branch_code_variant` + `code_diff` + archive/restore 分派 + switch 拦截 |
| `src/tianshu/app.py` | 改 | 装配 `CodeVariantStore` 并注入 `UniverseManager` |
| `tests/universe/test_model.py` | 新建 | `code_ref` 模型往返单测 |
| `tests/universe/test_storage_universe.py` | 改 | `code_ref` 存储往返单测 |
| `tests/universe/test_code_store.py` | 新建 | `CodeVariantStore` 生命周期单测（临时 git repo） |
| `tests/universe/test_manager_code.py` | 新建 | manager 代码变体路径单测（临时 git repo） |

---

## Task 1: `Universe` 模型加 `code_ref`

**Files:**
- Modify: `src/tianshu/universe/model.py`
- Test: `tests/universe/test_model.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/universe/test_model.py`：

```python
"""Tests for Universe model — code_ref roundtrip."""
from tianshu.universe.model import Universe, UniverseOrigin


def test_code_ref_roundtrip():
    u = Universe(name="cv", origin=UniverseOrigin.CODE_VARIANT, code_ref="universe/abc")
    row = u.to_row()
    assert row["code_ref"] == "universe/abc"
    back = Universe.from_row(row)
    assert back.code_ref == "universe/abc"
    assert back.origin == UniverseOrigin.CODE_VARIANT


def test_code_ref_defaults_none():
    u = Universe(name="data")
    assert u.code_ref is None
    assert u.to_row()["code_ref"] is None
    assert Universe.from_row(u.to_row()).code_ref is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/universe/test_model.py -v`
Expected: FAIL — `TypeError: Universe.__init__() got an unexpected keyword argument 'code_ref'`

- [ ] **Step 3: 实现**

`model.py` — 在 `mutation_reason` 字段后加 `code_ref`：

```python
    mutation_reason: str | None = None
    code_ref: str | None = None
    description: str = ""
```

`to_row()` — 在 `"mutation_reason"` 后加：

```python
            "mutation_reason": self.mutation_reason,
            "code_ref": self.code_ref,
            "description": self.description,
```

`from_row()` — 在 `mutation_reason=` 后加：

```python
            mutation_reason=row.get("mutation_reason"),
            code_ref=row.get("code_ref"),
            description=row.get("description", ""),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/universe/test_model.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/tianshu/universe/model.py tests/universe/test_model.py
git commit -m "feat(universe): Universe 模型加 code_ref 字段（2a 代码变体）"
```

---

## Task 2: `Storage` 持久化 `code_ref`

**Files:**
- Modify: `src/tianshu/storage.py`（DDL ~294-304、迁移 ~676、`save_universe` ~2340、`_row_to_universe` ~2437）
- Test: `tests/universe/test_storage_universe.py`

- [ ] **Step 1: 写失败测试**

在 `tests/universe/test_storage_universe.py` 末尾追加：

```python
def test_universe_code_ref_roundtrip(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe({
        "id": "cv1", "name": "code-variant",
        "status": "challenger", "origin": "code_variant",
        "code_ref": "universe/cv1",
        "created_at": "2026-06-08T00:00:00+00:00",
    })
    u = s.get_universe("cv1")
    assert u["code_ref"] == "universe/cv1"
    assert u["origin"] == "code_variant"


def test_universe_code_ref_defaults_none(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe({
        "id": "d1", "name": "data", "status": "challenger",
        "origin": "manual_branch", "created_at": "2026-06-08T00:00:00+00:00",
    })
    assert s.get_universe("d1")["code_ref"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/universe/test_storage_universe.py -v -k code_ref`
Expected: FAIL — `KeyError: 'code_ref'`（`_row_to_universe` 无该键）

- [ ] **Step 3: 实现**

(a) DDL — `storage.py` CREATE TABLE universes，在 `fitness_json` 行后、`created_at` 行前插入 `code_ref TEXT,`：

```sql
                    fitness_json TEXT NOT NULL DEFAULT '{}',
                    code_ref TEXT,
                    created_at TEXT NOT NULL
```

(b) 迁移 — 在 `_migrate()` 的 `migrations` 列表中 `idx_memorials_universe_id` 那条后追加：

```python
            # 2026-06-08: 代码变体位面 2a — worktree 分支引用
            "ALTER TABLE universes ADD COLUMN code_ref TEXT",
```

(c) `save_universe` — 列、占位符、值各加 `code_ref`：

```python
                """INSERT OR REPLACE INTO universes
                   (id, name, parent_universe_id, status, origin,
                    mutation_reason, description, fitness_json, code_ref, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uni["id"], uni["name"], uni.get("parent_universe_id"),
                    uni["status"], uni["origin"], uni.get("mutation_reason"),
                    uni.get("description", ""),
                    json.dumps(uni.get("fitness", {}), ensure_ascii=False),
                    uni.get("code_ref"),
                    uni["created_at"],
                ),
```

(d) `_row_to_universe` — 在 `"fitness"` 行后加 `"code_ref"`：

```python
            "fitness": json.loads(row["fitness_json"] or "{}"),
            "code_ref": row["code_ref"],
            "created_at": row["created_at"],
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/universe/test_storage_universe.py -v`
Expected: PASS（原有 + 2 新，全绿）

- [ ] **Step 5: 提交**

```bash
git add src/tianshu/storage.py tests/universe/test_storage_universe.py
git commit -m "feat(universe): universes 表持久化 code_ref（DDL+迁移+CRUD）"
```

---

## Task 3: `CodeVariantStore` — branch / exists

**Files:**
- Create: `src/tianshu/universe/code_store.py`
- Test: `tests/universe/test_code_store.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/universe/test_code_store.py`：

```python
"""Tests for CodeVariantStore — git worktree 生命周期。"""
import subprocess
from pathlib import Path

import pytest

from tianshu.universe.code_store import CodeVariantStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)


@pytest.fixture
def store(tmp_path: Path) -> CodeVariantStore:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    (repo / "src.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return CodeVariantStore(repo, tmp_path / "worktrees")


def test_branch_creates_worktree(store: CodeVariantStore):
    ref = store.branch_code_variant("u1")
    assert ref == "universe/u1"
    assert store.exists("u1")
    assert (store.worktree_dir("u1") / "src.txt").read_text() == "v1\n"


def test_branch_twice_raises(store: CodeVariantStore):
    store.branch_code_variant("u1")
    with pytest.raises(FileExistsError):
        store.branch_code_variant("u1")


def test_exists_false_before_branch(store: CodeVariantStore):
    assert not store.exists("ghost")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/universe/test_code_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tianshu.universe.code_store'`

- [ ] **Step 3: 实现**

新建 `src/tianshu/universe/code_store.py`（本 Task 先实现 `__init__` / 路径 / `_git` / `branch_code_variant` / `exists` / `_read_meta`，Task 4-5 续加 diff/gc/restore）：

```python
"""代码变体位面的 git worktree 生命周期（branch / diff / gc / restore）。

与 UniverseStore（行为配置的文件拷贝快照）正交：CodeVariantStore 只管"代码层"——
每个代码变体位面 = 主仓的一条分支 `universe/<id>` + 一份 worktree。
git 是代码的唯一真相源；fork 起点 start_ref 记在 sidecar，供 diff / restore 复用。
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_BRANCH_PREFIX = "universe/"
_META = "_meta"


class CodeVariantStore:
    def __init__(self, repo_root: Path, worktrees_root: Path) -> None:
        self._repo = Path(repo_root).expanduser().resolve()
        self._root = Path(worktrees_root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / _META).mkdir(parents=True, exist_ok=True)

    # --- paths ---

    def worktree_dir(self, universe_id: str) -> Path:
        return self._root / universe_id

    def branch_name(self, universe_id: str) -> str:
        return f"{_BRANCH_PREFIX}{universe_id}"

    def exists(self, universe_id: str) -> bool:
        return self.worktree_dir(universe_id).is_dir()

    def _meta_path(self, universe_id: str) -> Path:
        return self._root / _META / f"{universe_id}.json"

    # --- git helper ---

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self._repo),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    # --- lifecycle ---

    def branch_code_variant(self, universe_id: str, *, start_ref: str = "HEAD") -> str:
        """从 start_ref 拉出新分支 + worktree，返回分支名（即 code_ref）。"""
        wt = self.worktree_dir(universe_id)
        if wt.exists():
            raise FileExistsError(f"worktree already exists: {wt}")
        branch = self.branch_name(universe_id)
        start_sha = self._git("rev-parse", "--verify", start_ref).strip()
        self._git("worktree", "add", "-b", branch, str(wt), start_sha)
        self._meta_path(universe_id).write_text(
            json.dumps({"branch": branch, "start_ref": start_sha}, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Code variant worktree created: %s @ %s", branch, start_sha[:8])
        return branch

    def _read_meta(self, universe_id: str) -> dict:
        p = self._meta_path(universe_id)
        if not p.exists():
            raise FileNotFoundError(f"code variant meta missing: {universe_id}")
        return json.loads(p.read_text(encoding="utf-8"))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/universe/test_code_store.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/tianshu/universe/code_store.py tests/universe/test_code_store.py
git commit -m "feat(universe): CodeVariantStore git worktree branch/exists（2a）"
```

---

## Task 4: `CodeVariantStore.diff`

**Files:**
- Modify: `src/tianshu/universe/code_store.py`
- Test: `tests/universe/test_code_store.py`

- [ ] **Step 1: 写失败测试**

在 `tests/universe/test_code_store.py` 末尾追加：

```python
def test_diff_shows_changes(store: CodeVariantStore):
    store.branch_code_variant("u1")
    wt = store.worktree_dir("u1")
    (wt / "src.txt").write_text("v2\n")
    out = store.diff("u1")
    assert "-v1" in out and "+v2" in out


def test_diff_empty_when_unchanged(store: CodeVariantStore):
    store.branch_code_variant("u1")
    assert store.diff("u1").strip() == ""


def test_diff_missing_raises(store: CodeVariantStore):
    with pytest.raises(FileNotFoundError):
        store.diff("never")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/universe/test_code_store.py -v -k diff`
Expected: FAIL — `AttributeError: 'CodeVariantStore' object has no attribute 'diff'`

- [ ] **Step 3: 实现**

在 `code_store.py` 的 `branch_code_variant` 之后、`_read_meta` 之前插入：

```python
    def diff(self, universe_id: str) -> str:
        """返回变体 worktree 相对 fork 起点的 git diff（含已提交与未提交改动）。"""
        meta = self._read_meta(universe_id)
        wt = self.worktree_dir(universe_id)
        if not wt.is_dir():
            raise FileNotFoundError(f"worktree missing: {wt}")
        return self._git("diff", meta["start_ref"], cwd=wt)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/universe/test_code_store.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/tianshu/universe/code_store.py tests/universe/test_code_store.py
git commit -m "feat(universe): CodeVariantStore.diff（相对 fork 起点）"
```

---

## Task 5: `CodeVariantStore` — gc / restore

**Files:**
- Modify: `src/tianshu/universe/code_store.py`
- Test: `tests/universe/test_code_store.py`

- [ ] **Step 1: 写失败测试**

在 `tests/universe/test_code_store.py` 末尾追加：

```python
def test_gc_removes_worktree_keeps_branch(store: CodeVariantStore):
    store.branch_code_variant("u1")
    store.gc_worktree("u1")
    assert not store.exists("u1")
    out = subprocess.run(
        ["git", "branch", "--list", "universe/u1"],
        cwd=str(store._repo), capture_output=True, text=True,  # noqa: SLF001
    ).stdout
    assert "universe/u1" in out


def test_restore_rebuilds_worktree_with_committed_work(store: CodeVariantStore):
    store.branch_code_variant("u1")
    wt = store.worktree_dir("u1")
    (wt / "src.txt").write_text("v2\n")
    subprocess.run(["git", "add", "-A"], cwd=str(wt), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.test", "-c", "user.name=t",
         "commit", "-q", "-m", "edit"],
        cwd=str(wt), check=True,
    )
    store.gc_worktree("u1")
    store.restore_worktree("u1")
    assert store.exists("u1")
    assert (wt / "src.txt").read_text() == "v2\n"


def test_restore_noop_when_present(store: CodeVariantStore):
    store.branch_code_variant("u1")
    store.restore_worktree("u1")  # 已存在 → 不报错
    assert store.exists("u1")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/universe/test_code_store.py -v -k "gc or restore"`
Expected: FAIL — `AttributeError: 'CodeVariantStore' object has no attribute 'gc_worktree'`

- [ ] **Step 3: 实现**

在 `code_store.py` 的 `diff` 之后、`_read_meta` 之前插入：

```python
    def gc_worktree(self, universe_id: str) -> None:
        """删除 worktree 工作文件（保留分支/commit/meta，可 restore）。"""
        wt = self.worktree_dir(universe_id)
        if wt.is_dir():
            self._git("worktree", "remove", "--force", str(wt))

    def restore_worktree(self, universe_id: str) -> None:
        """从保留的分支重建 worktree（已存在则 no-op）。"""
        wt = self.worktree_dir(universe_id)
        if wt.is_dir():
            return
        branch = self._read_meta(universe_id)["branch"]
        self._git("worktree", "add", str(wt), branch)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/universe/test_code_store.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add src/tianshu/universe/code_store.py tests/universe/test_code_store.py
git commit -m "feat(universe): CodeVariantStore gc/restore worktree（保留分支可恢复）"
```

---

## Task 6: `UniverseManager` — 注入 code_store + `branch_code_variant`

**Files:**
- Modify: `src/tianshu/universe/manager.py`（import、`__init__`、新增方法）
- Test: `tests/universe/test_manager_code.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/universe/test_manager_code.py`：

```python
"""Tests for UniverseManager — 代码变体路径。"""
import subprocess
from pathlib import Path

import pytest

from tianshu.storage import Storage
from tianshu.universe.code_store import CodeVariantStore
from tianshu.universe.manager import UniverseManager
from tianshu.universe.store import UniverseStore


class _FakePersona:
    def __init__(self, d: Path):
        self.runtime_dir = d

    def repoint_runtime(self, _):
        pass


class _FakeSkills:
    def __init__(self, d: Path):
        self._d = d

    @property
    def user_dir(self):
        return self._d

    def repoint_user_dir(self, _):
        pass


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)


@pytest.fixture
def mgr(tmp_path: Path) -> UniverseManager:
    (p := tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (p / "SOUL.md").write_text("v1")
    (tmp_path / "skills").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    (repo / "src.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    store = UniverseStore(tmp_path / "universes", tmp_path / "personas", tmp_path / "skills")
    code_store = CodeVariantStore(repo, tmp_path / "worktrees")
    cfg = {"agent_config": {}}
    return UniverseManager(
        s, store, _FakePersona(tmp_path / "personas"), _FakeSkills(tmp_path / "skills"),
        config_snapshot=lambda: cfg, config_apply=lambda m: None, code_store=code_store,
    )


def test_branch_code_variant_creates_universe_and_worktree(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "perf-exp")
    assert cv["origin"] == "code_variant"
    assert cv["status"] == "challenger"
    assert cv["code_ref"] == f"universe/{cv['id']}"
    assert cv["parent_universe_id"] == g["id"]
    assert mgr._code_store.exists(cv["id"])  # noqa: SLF001


def test_branch_code_variant_nonexistent_parent_raises(mgr: UniverseManager):
    with pytest.raises(ValueError, match="not found"):
        mgr.branch_code_variant("ghost", "x")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/universe/test_manager_code.py -v`
Expected: FAIL — `TypeError: UniverseManager.__init__() got an unexpected keyword argument 'code_store'`

- [ ] **Step 3: 实现**

(a) `manager.py` 顶部 import 区加 `import dataclasses`：

```python
import dataclasses
import hashlib
import logging
```

(b) `__init__` 签名加 `code_store` 参数（放在 `agent_config` 之后），并保存：

```python
        event_bus: Any | None = None,
        agent_config: Callable[[], Any] | None = None,
        code_store: Any | None = None,
    ) -> None:
```

在 `self._agent_config = agent_config or (lambda: None)` 之后加：

```python
        self._code_store = code_store
```

(c) 在 `branch(...)` 方法之后插入新方法：

```python
    # --- code variant branch ---

    def branch_code_variant(
        self,
        parent_id: str,
        name: str,
        *,
        start_ref: str = "HEAD",
        description: str = "",
    ) -> dict:
        """从父位面分出一份代码变体（git worktree + 分支）；不复制行为配置数据层。"""
        if not self._code_store:
            raise RuntimeError("code variant store not configured")
        parent = self._storage.get_universe(parent_id)
        if not parent:
            raise ValueError(f"parent universe not found: {parent_id}")
        child = Universe(
            name=name,
            status=UniverseStatus.CHALLENGER,
            origin=UniverseOrigin.CODE_VARIANT,
            parent_universe_id=parent_id,
            description=description,
        )
        code_ref = self._code_store.branch_code_variant(child.id, start_ref=start_ref)
        child = dataclasses.replace(child, code_ref=code_ref)
        self._storage.save_universe(child.to_row())
        self._emit("universe.created", {
            "universe_id": child.id, "parent": parent_id,
            "origin": UniverseOrigin.CODE_VARIANT.value, "code_ref": code_ref,
        })
        return self._storage.get_universe(child.id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/universe/test_manager_code.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 跑既有 manager 测试确认未回归**

Run: `.venv/bin/python -m pytest tests/universe/test_manager.py -v`
Expected: PASS（全绿——`code_store` 为可选参数，既有用例不传，行为不变）

- [ ] **Step 6: 提交**

```bash
git add src/tianshu/universe/manager.py tests/universe/test_manager_code.py
git commit -m "feat(universe): UniverseManager.branch_code_variant + 注入 code_store（2a）"
```

---

## Task 7: `UniverseManager` — code_diff + archive/restore 分派 + switch 拦截

**Files:**
- Modify: `src/tianshu/universe/manager.py`（`switch`、`archive`、`restore`、新增 `code_diff`）
- Test: `tests/universe/test_manager_code.py`

- [ ] **Step 1: 写失败测试**

在 `tests/universe/test_manager_code.py` 末尾追加：

```python
def test_code_diff_returns_git_diff(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")
    wt = mgr._code_store.worktree_dir(cv["id"])  # noqa: SLF001
    (wt / "src.txt").write_text("v2\n")
    assert "+v2" in mgr.code_diff(cv["id"])


def test_code_diff_on_data_universe_raises(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    with pytest.raises(ValueError, match="code variant"):
        mgr.code_diff(g["id"])


def test_switch_to_code_variant_raises(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")
    with pytest.raises(ValueError, match="Deployer"):
        mgr.switch(cv["id"])


def test_archive_code_variant_gcs_worktree(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")
    assert mgr._code_store.exists(cv["id"])  # noqa: SLF001
    mgr.archive(cv["id"])
    assert not mgr._code_store.exists(cv["id"])  # noqa: SLF001


def test_restore_code_variant_rebuilds_worktree(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")
    mgr.archive(cv["id"])
    mgr.restore(cv["id"])
    assert mgr._code_store.exists(cv["id"])  # noqa: SLF001
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/universe/test_manager_code.py -v -k "code_diff or switch_to_code or archive_code or restore_code"`
Expected: FAIL — `AttributeError: 'UniverseManager' object has no attribute 'code_diff'`（且 switch 拦截、archive/restore gc 尚未生效）

- [ ] **Step 3: 实现**

(a) `switch(...)` — 在 `if not target:` 抛错之后、`if target["status"] == ...ARCHIVED...` 之前插入拦截：

```python
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if target.get("code_ref"):
            raise ValueError(
                "code variant switch/promotion requires the Deployer "
                "(Phase 2 increment 2d)"
            )
        if target["status"] == UniverseStatus.ARCHIVED.value:
            raise ValueError("cannot switch to an archived universe")
```

(b) `archive(...)` — 在 `set_universe_status(... ARCHIVED ...)` 之后、`self._emit(...)` 之前插入 gc：

```python
        self._storage.set_universe_status(universe_id, UniverseStatus.ARCHIVED.value)
        if target.get("code_ref") and self._code_store:
            self._code_store.gc_worktree(universe_id)
        self._emit("universe.archived", {"universe_id": universe_id})
```

(c) `restore(...)` — 在 `set_universe_status(... CHALLENGER ...)` 之后、`self._emit(...)` 之前插入重建：

```python
        self._storage.set_universe_status(universe_id, UniverseStatus.CHALLENGER.value)
        if target.get("code_ref") and self._code_store:
            self._code_store.restore_worktree(universe_id)
        self._emit("universe.restored", {"universe_id": universe_id})
```

(d) 新增 `code_diff` — 在 `diff(...)` 方法之后插入：

```python
    def code_diff(self, universe_id: str) -> str:
        """返回代码变体位面相对其 fork 起点的 git diff。"""
        if not self._code_store:
            raise RuntimeError("code variant store not configured")
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if not target.get("code_ref"):
            raise ValueError("not a code variant universe")
        return self._code_store.diff(universe_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/universe/test_manager_code.py tests/universe/test_manager.py -v`
Expected: PASS（新代码变体用例 + 既有数据位面用例全绿）

- [ ] **Step 5: 提交**

```bash
git add src/tianshu/universe/manager.py tests/universe/test_manager_code.py
git commit -m "feat(universe): manager code_diff + archive/restore 分派 worktree + switch 拦截代码变体（待 2d）"
```

---

## Task 8: app 装配 `CodeVariantStore`

**Files:**
- Modify: `src/tianshu/app.py`（import ~52、universe 装配块 ~645-659）

- [ ] **Step 1: 实现 import**

`app.py` 在 `from tianshu.universe.store import UniverseStore` 之后加：

```python
from tianshu.universe.store import UniverseStore
from tianshu.universe.code_store import CodeVariantStore
from tianshu.universe.manager import UniverseManager
```

- [ ] **Step 2: 实现装配**

在 `universe_store = UniverseStore(...)` 块之后、`universe_manager = UniverseManager(...)` 之前插入：

```python
    code_variant_store = CodeVariantStore(
        repo_root=Path(__file__).resolve().parents[2],
        worktrees_root=Path("~/.tianshu/universes/worktrees").expanduser(),
    )
```

并在 `UniverseManager(...)` 调用里、`agent_config=...` 之后加一行：

```python
        agent_config=lambda: config_manager.agent_config,
        code_store=code_variant_store,
    )
```

- [ ] **Step 3: 冒烟——确认 app 装配不破坏启动**

Run: `.venv/bin/python -c "from tianshu.app import create_app; app = create_app(); print('app ok')"`
Expected: 输出 `app ok`（构造 `CodeVariantStore` 仅 mkdir worktrees 目录，不触发 git，启动安全）

- [ ] **Step 4: 跑既有网关启动测试确认未回归**

Run: `.venv/bin/python -m pytest tests/test_gateway_extended.py -v`
Expected: PASS（lifespan 装配新组件后正常启动）

- [ ] **Step 5: 提交**

```bash
git add src/tianshu/app.py
git commit -m "feat(universe): app 装配 CodeVariantStore 并注入 UniverseManager（2a）"
```

---

## Task 9: 全量回归 + 验收

**Files:** 无（验证任务）

- [ ] **Step 1: 跑 universe 全量单测**

Run: `.venv/bin/python -m pytest tests/universe/ -v`
Expected: PASS（含 test_model / test_storage_universe / test_code_store / test_manager / test_manager_code 全绿）

- [ ] **Step 2: 跑全量回归**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS（基线 1169 + 新增用例全绿，0 failed）

- [ ] **Step 3: 验收对照 spec §3.1**

逐条确认（均由上面单测覆盖）：
- 从 champion 分支出代码变体位面（worktree 落盘 + code_ref 持久化）→ `test_branch_code_variant_creates_universe_and_worktree`
- git diff 看差异 → `test_code_diff_returns_git_diff` / `test_diff_shows_changes`
- 归档回收 worktree → `test_archive_code_variant_gcs_worktree` / `test_gc_removes_worktree_keeps_branch`
- 恢复重建 → `test_restore_code_variant_rebuilds_worktree` / `test_restore_rebuilds_worktree_with_committed_work`
- switch/promotion 显式拦截（属 2d）→ `test_switch_to_code_variant_raises`

- [ ] **Step 4: 确认无孤儿/越界改动**

审查本计划产生的 8 个提交（Task 1–8）的改动文件集（不要用 `main...HEAD`——feat_phase8 上有大量既存提交会混入）：

Run: `git log --oneline --stat -8`
Expected: 触及的文件仅限计划列出的 9 个（5 源文件 + 4 测试文件），无其它无关改动。

---

## Self-Review（计划自检结论）

- **Spec 覆盖**：§3.1 四条验收 → Task 6/7/9 全覆盖；§5.1 表示与存储 → Task 1/2/3；§5.2 生命周期（branch/diff/archive/restore；switch 属 2d 拦截）→ Task 3/4/5/7；§13 衔接（CODE_VARIANT 枚举、manifest 代码段以 sidecar 形式落地、可重定向抽象不破契约）→ Task 6/7。沙箱/评估/生成/晋升/API/UI 明确移出 2a（见 Scope）。
- **占位符**：无 TBD/TODO；每个代码步骤均含完整代码与确切命令、预期输出。
- **类型一致性**：`code_ref`（`str | None`）贯穿 model/storage/manager 一致；`branch_code_variant` 返回分支名（= code_ref）；`CodeVariantStore` 方法名 `branch_code_variant`/`diff`/`gc_worktree`/`restore_worktree`/`exists`/`worktree_dir` 在 store 与 manager/测试间一致；manager `code_store` 可选参数默认 `None`，既有 fixture 不传保持向后兼容。
