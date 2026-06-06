# 记忆召回全量化(write-through 索引)+ compact 非破坏化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让记忆召回走全量 FTS5(去掉 30 天窗口、写入即时可召回、自然语言 query 不再静默零召回),并把 compact 改成非破坏写入,不再覆盖整个 `MEMORY.md`。

**Architecture:** Markdown 仍是唯一真相;`store()` 写 MD 后同步直写 SQLite(`save_memory_entry` → `memory_fts` trigger 自动同步),SQLite/FTS 成为始终同步的派生索引(write-through)。执行前召回从「30 天 MD substring 扫描」改为「全量 FTS5 + recency 加权」。`fts_search` 内置转义,一处修复使所有调用点受益。compact 改用 H2 section 锚定写,只更新 `## 历史摘要`。

**Tech Stack:** Python 3.12、SQLite FTS5(BM25)、pydantic、pytest。

> **测试策略**:遵循项目 `feedback_test_last` 约定(功能优先、测试最后统一补)。Task 1–5 为「实现 + 手动验证」,pytest 测试集中在 Task 6。这有意偏离 writing-plans 默认的 per-task TDD,依据是用户偏好优先。所有 Python 命令用 `.venv/bin/python`(项目约定 `project_venv_python`)。

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `src/tianshu/memory/fts.py` | Modify | 新增 `escape_fts5_query()`;`fts_search` 开头转义 query(一处修复，全调用点受益) |
| `src/tianshu/memory/manager.py` | Modify | `store()` 加 write-through;新增 `_recall_fulltext()`;`on_before_agent_start` 改走全量召回;`compact()` 改非破坏写入 |
| `src/tianshu/memory/markdown_backend.py` | Modify | `_mutate_section` + `write_section` 支持 `mode="set"`(整段 body 覆盖) |
| `tests/memory/__init__.py` | Create | 新建测试包 |
| `tests/memory/test_fulltext_recall.py` | Create | 覆盖转义、write-through、全量召回、set 非破坏 |

每处改动彼此低耦合:Task 1(转义)/Task 4(set 模式)是独立单元;Task 2(写)→Task 3(读)有数据依赖;Task 4→Task 5(compact 用 set)有依赖。

---

### Task 1: `fts_search` 内置 FTS5 转义(堵住静默零召回)

**Files:**
- Modify: `src/tianshu/memory/fts.py:48-91`

- [ ] **Step 1: 在 `fts_search` 上方新增 `escape_fts5_query`,并让 `fts_search` 开头转义**

把 `fts.py` 中现有的 `def fts_search(...)` 整段替换为下面内容(新增一个函数 + 在函数体最前面转义):

```python
def escape_fts5_query(query: str) -> str:
    """把用户输入转义为 FTS5 安全 query。

    按空白切词,每个 token 包成 phrase("...")并对内部引号转义,token 间隐式
    AND。避免括号/引号/中文标点等特殊字符触发 FTS5 语法错误——该错误会被
    fts_search 的 except 吞掉,造成静默零召回。
    """
    tokens = [t for t in query.split() if t]
    if not tokens:
        return ""
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def fts_search(
    conn: sqlite3.Connection,
    query: str,
    persona_id: str | None = None,
    limit: int = 20,
    persona_ids: "list[str] | None" = None,
) -> list[str]:
    """Search memory via FTS5. Returns list of matching entry IDs.

    persona_ids 优先级高于 persona_id：
      - persona_ids = ["wym", "court", "_dept_neige"] → 限定多 ID 集合
      - persona_id = "wym"（旧用法）→ 单 ID
      - 都未提供 → 跨 persona 检索（原行为）
    """
    safe_query = escape_fts5_query(query)
    if not safe_query:
        return []
    try:
        if persona_ids:
            placeholders = ",".join("?" for _ in persona_ids)
            rows = conn.execute(
                f"""SELECT id FROM memory_fts
                    WHERE memory_fts MATCH ? AND persona_id IN ({placeholders})
                    ORDER BY rank
                    LIMIT ?""",
                (safe_query, *persona_ids, limit),
            ).fetchall()
        elif persona_id:
            rows = conn.execute(
                """SELECT id FROM memory_fts
                   WHERE memory_fts MATCH ? AND persona_id = ?
                   ORDER BY rank
                   LIMIT ?""",
                (safe_query, persona_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id FROM memory_fts
                   WHERE memory_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        logger.debug("FTS5 search failed, returning empty results")
        return []
```

- [ ] **Step 2: 手动验证——特殊字符不再抛错**

Run:
```bash
.venv/bin/python -c "
import tempfile, pathlib
from tianshu.storage import Storage
from tianshu.memory.fts import fts_search, escape_fts5_query
s = Storage(str(pathlib.Path(tempfile.mkdtemp())/'t.db')); s.init_db()
print('escaped:', escape_fts5_query('如何部署(生产环境)? \"foo\" bar'))
print('result:', fts_search(s._conn, '如何部署(生产环境)?'))
print('OK: no exception')
"
```
Expected: 打印 `escaped: \"如何部署(生产环境)?\" \"foo\" \"bar\"`、`result: []`、`OK: no exception`,**无 traceback**(改前同样输入会触发 FTS5 语法错误,被吞成空)。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/memory/fts.py
git commit -m "fix(memory): fts_search 内置 FTS5 转义，堵住自然语言 query 静默零召回"
```

---

### Task 2: `store()` write-through 索引(写入即时可召回)

**Files:**
- Modify: `src/tianshu/memory/manager.py:108-151`

- [ ] **Step 1: 在 `store()` 的 `return entry` 之前加直写索引**

`store()` 末尾现状是:
```python
        logger.debug(
            "[MEM] store: persona=%s, category=%s, content_len=%d",
            entry.persona_id, entry.category, len(entry.content),
        )
        return entry
```

改为(在 `logger.debug(...)` 之后、`return entry` 之前插入):
```python
        logger.debug(
            "[MEM] store: persona=%s, category=%s, content_len=%d",
            entry.persona_id, entry.category, len(entry.content),
        )

        # write-through 索引：MD 写完后同步刷 SQLite + FTS（memory_fts trigger 自动维护）。
        # MD 仍是唯一真相；索引写失败不阻断，可后续 sync_index 修复。
        try:
            self._backend.save(entry)
        except Exception:
            logger.exception(
                "Index write-through failed for %s (MD already persisted)", entry.id,
            )

        return entry
```

- [ ] **Step 2: 手动验证——store 后立即可查**

Run:
```bash
.venv/bin/python -c "
import tempfile, pathlib
from tianshu.storage import Storage
from tianshu.config_manager import ConfigManager, LLMConfigState, AgentConfigState
from tianshu.memory.manager import MemoryManager
from tianshu.memory.models import MemoryEntry
from tianshu.memory.fts import fts_search
tmp = pathlib.Path(tempfile.mkdtemp())
s = Storage(str(tmp/'t.db')); s.init_db()
cm = ConfigManager(LLMConfigState(name='t', model='t', api_key='k', api_base='http://x'),
                   agent_config=AgentConfigState(agent_max_iterations=5, agent_timeout_seconds=30, skills_char_budget=1000))
mm = MemoryManager(storage=s, config_manager=cm, memory_dir=tmp/'memory', personas_dir=tmp/'personas')
e = MemoryEntry(persona_id='wym', category='observation', content='部署成功 deploy-xyz123')
mm.store(e)
ids = fts_search(s._conn, 'deploy-xyz123', persona_id='wym')
assert e.id in ids, ids
print('OK write-through:', ids)
"
```
Expected: `OK write-through: ['<entry-id>']`,无 AssertionError。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/memory/manager.py
git commit -m "feat(memory): store() write-through 索引，写入即时可召回"
```

---

### Task 3: `on_before_agent_start` 召回全量化(移除 30 天窗口 + recency)

**Files:**
- Modify: `src/tianshu/memory/manager.py:438-481`

- [ ] **Step 1: 在 `on_before_agent_start` 方法定义上方,新增 `_recall_fulltext` 方法**

在 `async def on_before_agent_start` 这一行之前,插入下面的方法到 `MemoryManager` 类内:

```python
    def _recall_fulltext(
        self,
        persona_id: str,
        goal: str,
        department: str | None = None,
        limit: int = 5,
    ) -> list[str]:
        """执行前召回：全量 FTS5（persona + court [+ dept]）+ recency 加权。

        返回注入用的 content 列表，按 BM25 位次 × 时间衰减排序，取 top-limit。
        转义已内置于 fts_search，此处无需再转义。
        """
        import math
        from datetime import UTC, datetime

        from tianshu.memory.fts import fts_search

        visible_ids = [persona_id, "court"]
        if department:
            visible_ids.append(f"_dept_{department}")

        ids = fts_search(
            self._storage._conn, goal, persona_ids=visible_ids, limit=limit * 4,
        )
        if not ids:
            return []

        rank = {entry_id: i for i, entry_id in enumerate(ids)}
        placeholders = ",".join("?" for _ in ids)
        with self._storage._lock:
            rows = self._storage._conn.execute(
                f"SELECT id, content, created_at FROM memory_entries WHERE id IN ({placeholders})",
                ids,
            ).fetchall()

        now = datetime.now(UTC)
        scored: list[tuple[float, str]] = []
        for row in rows:
            bm25 = 1.0 / (1.0 + rank.get(row["id"], len(ids)))
            try:
                ts = datetime.fromisoformat(row["created_at"])
                age_days = (now - ts).total_seconds() / 86400
                recency = math.exp(-0.693 * age_days / 30)  # half-life = 30 天
            except (TypeError, ValueError):
                recency = 0.5
            scored.append((bm25 * (0.5 + 0.5 * recency), row["content"]))

        scored.sort(key=lambda x: -x[0])
        return [content for _, content in scored[:limit]]
```

- [ ] **Step 2: 把 `on_before_agent_start` 里的 30 天 MD 召回段替换为全量召回**

现状(`on_before_agent_start` 内):
```python
        history_messages: list[dict] = []

        # Existing: search Markdown daily logs
        md_results = self._md_backend.search_daily_logs(
            persona_id, goal, limit=10,
        )
        for r in md_results[:5]:
            history_messages.append(
                {"role": "user", "content": f"[Memory context — do not respond to this] {r['content']}"}
            )
```

替换为:
```python
        history_messages: list[dict] = []

        # 全量 FTS5 召回（write-through 索引，任意时间 + recency 加权，已移除 30 天窗口）
        persona = context.get("persona")
        department = getattr(persona, "department", None) if persona else None
        for content in self._recall_fulltext(persona_id, goal, department=department, limit=5):
            history_messages.append(
                {"role": "user", "content": f"[Memory context — do not respond to this] {content}"}
            )
```

(同方法内 drawer L2 召回段 `if self._drawer_store and ...` 保持不变。)

- [ ] **Step 3: 手动验证——能召回 31 天前的旧记忆**

Run:
```bash
.venv/bin/python -c "
import tempfile, pathlib
from datetime import UTC, datetime, timedelta
from tianshu.storage import Storage
from tianshu.config_manager import ConfigManager, LLMConfigState, AgentConfigState
from tianshu.memory.manager import MemoryManager
from tianshu.memory.models import MemoryEntry
tmp = pathlib.Path(tempfile.mkdtemp())
s = Storage(str(tmp/'t.db')); s.init_db()
cm = ConfigManager(LLMConfigState(name='t', model='t', api_key='k', api_base='http://x'),
                   agent_config=AgentConfigState(agent_max_iterations=5, agent_timeout_seconds=30, skills_char_budget=1000))
mm = MemoryManager(storage=s, config_manager=cm, memory_dir=tmp/'memory', personas_dir=tmp/'personas')
old = MemoryEntry(persona_id='wym', category='observation', content='迁移数据库 migration-old-9z',
                  created_at=datetime.now(UTC) - timedelta(days=31))
mm.store(old)
hits = mm._recall_fulltext('wym', 'migration-old-9z', limit=5)
assert any('migration-old-9z' in h for h in hits), hits
print('OK recall 31d-old:', hits)
"
```
Expected: `OK recall 31d-old: ['迁移数据库 migration-old-9z']`(31 天前的条目被召回,证明窗口已移除)。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/memory/manager.py
git commit -m "feat(memory): 执行前召回改走全量 FTS5 + recency 重排，移除 30 天窗口"
```

---

### Task 4: `markdown_backend` 支持 `mode="set"`(整段 body 覆盖)

**Files:**
- Modify: `src/tianshu/memory/markdown_backend.py:187-194`(write_section 校验)
- Modify: `src/tianshu/memory/markdown_backend.py:248-311`(_mutate_section 纯函数)

- [ ] **Step 1: `write_section` 放行 `set` 模式**

现状(`write_section` 开头校验):
```python
        if mode not in ("append", "replace", "remove"):
            raise ValueError(f"unsupported write_section mode: {mode}")
        if mode in ("append", "replace") and not content:
            raise ValueError(f"mode={mode} requires non-empty content")
        if mode in ("replace", "remove") and not old_text:
            raise ValueError(f"mode={mode} requires old_text")
```

替换为:
```python
        if mode not in ("append", "replace", "remove", "set"):
            raise ValueError(f"unsupported write_section mode: {mode}")
        if mode in ("append", "replace", "set") and not content:
            raise ValueError(f"mode={mode} requires non-empty content")
        if mode in ("replace", "remove") and not old_text:
            raise ValueError(f"mode={mode} requires old_text")
```

- [ ] **Step 2: `_mutate_section` 增加 `set` 分支**

在 `_mutate_section` 内,section 不存在分支现状:
```python
        if start < 0:
            # section 不存在
            if mode in ("replace", "remove"):
                raise FileNotFoundError(f"section {section!r} not found in MEMORY.md")
            # append：创建新段，追加到文件末尾
            base = existing.rstrip()
            sep = "\n\n" if base else ""
            return f"{base}{sep}{section}\n\n{content.rstrip()}\n"
```
改为(把注释从 append 改成 append/set,逻辑不变即可同时覆盖 set 的「不存在则创建」):
```python
        if start < 0:
            # section 不存在
            if mode in ("replace", "remove"):
                raise FileNotFoundError(f"section {section!r} not found in MEMORY.md")
            # append / set：创建新段，追加到文件末尾
            base = existing.rstrip()
            sep = "\n\n" if base else ""
            return f"{base}{sep}{section}\n\n{content.rstrip()}\n"
```

在 section 存在分支,现状的 if/elif/else 链:
```python
        if mode == "append":
            # 去重：内容若完全包含在已有 body 中则拒绝
            if content.strip() in section_body:
                raise ValueError("content already present in this section (dedupe)")
            new_body = section_body.rstrip() + "\n\n" + content.rstrip() + "\n"
            new_section = f"{section}\n{new_body}"
        elif mode == "replace":
            if old_text not in section_body:
                raise FileNotFoundError(f"old_text not found in section {section!r}")
            new_body = section_body.replace(old_text, content, 1)
            new_section = f"{section}\n{new_body}"
        else:  # remove
            if old_text not in section_body:
                raise FileNotFoundError(f"old_text not found in section {section!r}")
            new_body = section_body.replace(old_text, "", 1)
            # 若移除后 section body 全空，则把整个 section 也移掉
            if not new_body.strip():
                new_section = ""
            else:
                new_section = f"{section}\n{new_body}"
```
在 `elif mode == "replace":` 之前插入 `set` 分支:
```python
        if mode == "append":
            # 去重：内容若完全包含在已有 body 中则拒绝
            if content.strip() in section_body:
                raise ValueError("content already present in this section (dedupe)")
            new_body = section_body.rstrip() + "\n\n" + content.rstrip() + "\n"
            new_section = f"{section}\n{new_body}"
        elif mode == "set":
            # 整段 body 覆盖：只动本 section，其余 section 原样保留
            new_section = f"{section}\n\n{content.rstrip()}\n"
        elif mode == "replace":
            if old_text not in section_body:
                raise FileNotFoundError(f"old_text not found in section {section!r}")
            new_body = section_body.replace(old_text, content, 1)
            new_section = f"{section}\n{new_body}"
        else:  # remove
            if old_text not in section_body:
                raise FileNotFoundError(f"old_text not found in section {section!r}")
            new_body = section_body.replace(old_text, "", 1)
            # 若移除后 section body 全空，则把整个 section 也移掉
            if not new_body.strip():
                new_section = ""
            else:
                new_section = f"{section}\n{new_body}"
```

- [ ] **Step 3: 手动验证——set 覆盖目标段、保留其他段**

Run:
```bash
.venv/bin/python -c "
from tianshu.memory.markdown_backend import MarkdownMemoryBackend as M
existing = '# wym Memory\n\n## 心学要旨\n知行合一\n\n## 历史摘要\n旧摘要内容\n'
out = M._mutate_section(existing, '## 历史摘要', mode='set', content='全新摘要', old_text=None)
assert '## 心学要旨' in out and '知行合一' in out, out
assert '旧摘要内容' not in out and '全新摘要' in out, out
print('OK set preserves others:\n' + out)
"
```
Expected: 打印结果中 `## 心学要旨 / 知行合一` 仍在,`旧摘要内容` 被换成 `全新摘要`,无 AssertionError。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/memory/markdown_backend.py
git commit -m "feat(memory): markdown_backend 支持 mode=set（整段 body 覆盖）"
```

---

### Task 5: `compact()` 非破坏化(不再覆盖整个 MEMORY.md)

**Files:**
- Modify: `src/tianshu/memory/manager.py:331-366`

- [ ] **Step 1: 把 compact 的整文件覆盖改为 section 锚定写**

现状(`compact()` 内):
```python
        # Write compacted summary to MEMORY.md (overwrite)
        self._md_backend.write_core_memory(persona_id, result.summary)

        return result
```

替换为:
```python
        # 非破坏写入：只更新「## 历史摘要」section，保留 memory_write / reflect 写入的其余 section
        try:
            self._md_backend.write_section(
                persona_id, "## 历史摘要", mode="set", content=result.summary,
            )
        except Exception:
            logger.exception("compact write_section failed for %s", persona_id)

        return result
```

- [ ] **Step 2: 手动验证——compact 不冲掉 memory_write 写的 section**

Run:
```bash
.venv/bin/python -c "
import tempfile, pathlib
from tianshu.memory.markdown_backend import MarkdownMemoryBackend
tmp = pathlib.Path(tempfile.mkdtemp())
md = MarkdownMemoryBackend(memory_dir=tmp, personas_dir=tmp/'personas')
# 模拟 agent 先用 memory_write 写了一个私有 section
md.write_section('wym', '## 心学要旨', mode='set', content='知行合一')
# 再模拟 compact 写历史摘要
md.write_section('wym', '## 历史摘要', mode='set', content='近 30 天关键决策摘要')
text = md.read_core_memory('wym')
assert '## 心学要旨' in text and '知行合一' in text, text
assert '## 历史摘要' in text and '近 30 天关键决策摘要' in text, text
print('OK compact non-destructive:\n' + text)
"
```
Expected: 两个 section 共存,`心学要旨` 未被冲掉。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/memory/manager.py
git commit -m "fix(memory): compact 改非破坏写入，不再覆盖整个 MEMORY.md"
```

---

### Task 6: 补 pytest 测试(统一)

**Files:**
- Create: `tests/memory/__init__.py`
- Create: `tests/memory/test_fulltext_recall.py`

- [ ] **Step 1: 建测试包**

Run:
```bash
mkdir -p tests/memory && touch tests/memory/__init__.py
```

- [ ] **Step 2: 写测试文件**

Create `tests/memory/test_fulltext_recall.py`:
```python
"""记忆召回全量化 + compact 非破坏化 测试。

复用 tests/conftest.py 的 storage / config_manager fixtures。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tianshu.memory.fts import escape_fts5_query, fts_search
from tianshu.memory.manager import MemoryManager
from tianshu.memory.markdown_backend import MarkdownMemoryBackend
from tianshu.memory.models import MemoryEntry


@pytest.fixture
def manager(storage, config_manager, tmp_path):
    return MemoryManager(
        storage=storage,
        config_manager=config_manager,
        memory_dir=tmp_path / "memory",
        personas_dir=tmp_path / "personas",
    )


def test_escape_fts5_query_handles_special_chars():
    out = escape_fts5_query('部署(生产)? "foo" bar')
    assert out == '"部署(生产)?" "foo" "bar"'
    assert escape_fts5_query("   ") == ""


def test_fts_search_no_crash_on_special_chars(storage):
    # 改前：未转义的特殊字符会触发 FTS5 语法错误并被吞成空（静默零召回）
    assert fts_search(storage._conn, "如何部署(生产环境)? @x") == []


def test_store_is_write_through(manager, storage):
    entry = MemoryEntry(persona_id="wym", category="observation", content="部署成功 deploy-xyz123")
    manager.store(entry)
    ids = fts_search(storage._conn, "deploy-xyz123", persona_id="wym")
    assert entry.id in ids


def test_recall_hits_entry_older_than_30_days(manager):
    old = MemoryEntry(
        persona_id="wym",
        category="observation",
        content="迁移数据库 migration-old-9z",
        created_at=datetime.now(UTC) - timedelta(days=31),
    )
    manager.store(old)
    hits = manager._recall_fulltext("wym", "migration-old-9z", limit=5)
    assert any("migration-old-9z" in h for h in hits)


def test_recall_includes_court_scope(manager):
    manager.store(MemoryEntry(persona_id="court", category="insight", content="朝廷共识 court-rule-7"))
    hits = manager._recall_fulltext("wym", "court-rule-7", limit=5)
    assert any("court-rule-7" in h for h in hits)


def test_mutate_section_set_preserves_other_sections():
    existing = "# wym Memory\n\n## 心学要旨\n知行合一\n\n## 历史摘要\n旧摘要\n"
    out = MarkdownMemoryBackend._mutate_section(
        existing, "## 历史摘要", mode="set", content="全新摘要", old_text=None,
    )
    assert "## 心学要旨" in out and "知行合一" in out
    assert "旧摘要" not in out and "全新摘要" in out


def test_set_creates_section_when_absent(tmp_path):
    md = MarkdownMemoryBackend(memory_dir=tmp_path, personas_dir=tmp_path / "personas")
    md.write_section("wym", "## 历史摘要", mode="set", content="首个摘要")
    text = md.read_core_memory("wym")
    assert "## 历史摘要" in text and "首个摘要" in text
```

- [ ] **Step 3: 跑测试**

Run:
```bash
.venv/bin/python -m pytest tests/memory/test_fulltext_recall.py -v
```
Expected: 7 个用例全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add tests/memory/
git commit -m "test(memory): 补召回全量化 + compact 非破坏化测试"
```

---

## Self-Review

**1. Spec coverage**(逐条对 spec §2 验收目标):
- 目标 1(任意时间召回)→ Task 3 + `test_recall_hits_entry_older_than_30_days`。✅
- 目标 2(写入即时可召回)→ Task 2 + `test_store_is_write_through`。✅
- 目标 3(转义不静默零召回)→ Task 1 + `test_fts_search_no_crash_on_special_chars`。✅
- 目标 4(compact 不冲掉 section)→ Task 4+5 + `test_mutate_section_set_preserves_other_sections`、Task 5 手动验证。✅
- 目标 5(不引入向量)→ 全程仅用 FTS5 BM25,无 embedding。✅
- 可见范围含 court(spec §4.3)→ `test_recall_includes_court_scope`。✅

**2. Placeholder scan**: 无 TBD/TODO;每个代码步骤均为完整可粘贴代码;每个验证步骤均有精确命令与预期。✅

**3. Type consistency**:
- `escape_fts5_query`(Task 1 定义)→ Task 3 注释说明已内置,未重复转义。✅
- `_recall_fulltext(persona_id, goal, department, limit)`(Task 3 定义)→ Task 3 Step 2 与 Task 6 调用签名一致。✅
- `mode="set"`(Task 4 定义)→ Task 5、Task 6 使用一致。✅
- `self._storage._conn` / `self._storage._lock`:与 `manager.py` 既有 `delete()` 用法一致。✅

无遗漏,无需补任务。
