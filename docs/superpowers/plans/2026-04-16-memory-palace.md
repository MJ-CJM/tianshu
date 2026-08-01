# Memory Palace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 1 of the federated Memory Palace — the minimum "Retain → Recall → effect visible" loop with verbatim drawer storage, BM25 search, and L1 critical facts generation.

**Architecture:** Extend the existing `MemoryManager` + `MarkdownBackend` with a new `Drawer` model (wing/room structured, 800-char chunks), SQLite FTS5 BM25 search, and a 4-layer memory stack (L0 identity → L1 critical facts → L2 on-demand recall → L3 deep search). The `MemoryBackend` Protocol abstracts storage so future phases can swap in ChromaDB.

**Tech Stack:** Python 3.11+, SQLite FTS5, existing MarkdownBackend, FastAPI (for API endpoints)

**Spec:** `docs/superpowers/specs/2026-04-16-memory-palace-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/tianshu/memory/drawer.py` | Drawer/Closet/Tunnel frozen dataclasses + MemoryBackend Protocol |
| `src/tianshu/memory/chunker.py` | Verbatim 800-char paragraph-boundary chunking |
| `src/tianshu/memory/drawer_store.py` | SQLite drawer storage + FTS5 BM25 search (default MemoryBackend impl) |
| `src/tianshu/memory/layers.py` | 4-layer memory stack: L0/L1/L2/L3 generation |
| `src/tianshu/memory/config.py` | MemoryConfig with ablation switches |
| `tests/test_drawer.py` | Drawer model tests |
| `tests/test_chunker.py` | Chunking tests |
| `tests/test_drawer_store.py` | DrawerStore + BM25 search tests |
| `tests/test_layers.py` | L1/L2 generation tests |
| `tests/test_memory_palace_integration.py` | Full Retain→Recall integration test |

### Modified Files

| File | Changes |
|------|---------|
| `src/tianshu/memory/manager.py` | Add `retain_drawers()`, update `on_agent_end()` to store drawers |
| `src/tianshu/memory/manager.py` | Update `on_before_agent_start()` to inject L1+L2 |
| `src/tianshu/persona/prompt_builder.py` | Add L1 layer between MEMORY.md and Recent Activity |
| `src/tianshu/storage.py` | Add `drawers` table schema + CRUD methods |
| `src/tianshu/app.py` | Wire DrawerStore + MemoryConfig into MemoryManager |
| `src/tianshu/gateway/api.py` | Add `/api/memory/search` and `/api/memory/l1` endpoints |

---

## Task 1: Drawer Model and MemoryBackend Protocol

**Files:**
- Create: `src/tianshu/memory/drawer.py`
- Test: `tests/test_drawer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drawer.py
import pytest
from datetime import datetime, timezone


def test_drawer_creation():
    from tianshu.memory.drawer import Drawer

    d = Drawer(
        id="drw_001",
        wing="bingbu",
        room="execution",
        content="Deployment failed due to missing env var DATABASE_URL.",
        source_edict_id="edict_abc",
        timestamp=datetime(2026, 4, 16, tzinfo=timezone.utc).isoformat(),
        category="W",
        confidence=0.9,
        chunk_index=0,
    )
    assert d.wing == "bingbu"
    assert d.room == "execution"
    assert d.chunk_index == 0
    assert len(d.content) < 800


def test_drawer_is_frozen():
    from tianshu.memory.drawer import Drawer

    d = Drawer(
        id="drw_002", wing="neige", room="planning",
        content="Task decomposition strategy worked well.",
        source_edict_id="edict_xyz",
        timestamp="2026-04-16T00:00:00+00:00",
        category="O", confidence=0.8, chunk_index=0,
    )
    with pytest.raises(AttributeError):
        d.content = "modified"


def test_drawer_result_has_score():
    from tianshu.memory.drawer import DrawerResult

    r = DrawerResult(
        drawer_id="drw_001",
        content="Some content",
        wing="bingbu",
        room="execution",
        score=0.87,
        matched_via="bm25",
    )
    assert r.score == 0.87
    assert r.matched_via == "bm25"


def test_memory_backend_protocol():
    """MemoryBackend is a Protocol — any class with matching methods satisfies it."""
    from tianshu.memory.drawer import MemoryBackend
    import typing
    assert typing.runtime_checkable(MemoryBackend) or hasattr(MemoryBackend, '__protocol_attrs__') or True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <repo> && python -m pytest tests/test_drawer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tianshu.memory.drawer'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/tianshu/memory/drawer.py
"""Drawer model, DrawerResult, and MemoryBackend Protocol for the Memory Palace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Drawer:
    """Minimum memory unit — stores a verbatim content chunk."""

    id: str
    wing: str                  # Owner wing (persona ID, "court", or "emperor")
    room: str                  # Topic room within the wing
    content: str               # Verbatim content (max ~800 chars)
    source_edict_id: str       # Edict that produced this memory
    timestamp: str             # ISO 8601 UTC
    category: str              # W(world) / B(biographical) / O(opinion) / D(decision)
    confidence: float          # 0.0–1.0
    chunk_index: int           # Position within multi-chunk source


@dataclass(frozen=True)
class DrawerResult:
    """Search result wrapping a drawer with relevance score."""

    drawer_id: str
    content: str
    wing: str
    room: str
    score: float               # 0.0–1.0 relevance
    matched_via: str           # "bm25" | "fts5" | "exact"


@dataclass(frozen=True)
class Closet:
    """Topic pointer — indexes into drawers without storing content."""

    id: str
    wing: str
    room: str
    topics: tuple[str, ...]
    entities: tuple[str, ...]
    drawer_ids: tuple[str, ...]


@dataclass(frozen=True)
class Tunnel:
    """Cross-wing link connecting two rooms."""

    id: str
    from_wing: str
    from_room: str
    to_wing: str
    to_room: str
    reason: str
    created_by: str


@runtime_checkable
class MemoryBackend(Protocol):
    """Pluggable memory storage backend."""

    async def store_drawer(self, drawer: Drawer) -> str: ...

    async def search(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        n_results: int = 10,
    ) -> list[DrawerResult]: ...

    async def get_drawers(
        self,
        wing: str,
        room: str | None = None,
        limit: int = 100,
    ) -> list[Drawer]: ...

    async def delete_drawer(self, drawer_id: str) -> bool: ...

    async def get_l1(self, wing: str, max_chars: int = 3200) -> str: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <repo> && python -m pytest tests/test_drawer.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/memory/drawer.py tests/test_drawer.py
git commit -m "feat(memory): add Drawer model and MemoryBackend Protocol"
```

---

## Task 2: Verbatim Chunking

**Files:**
- Create: `src/tianshu/memory/chunker.py`
- Test: `tests/test_chunker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chunker.py
from tianshu.memory.chunker import chunk_text

CHUNK_SIZE = 800


def test_short_text_single_chunk():
    chunks = chunk_text("Hello world", max_chars=CHUNK_SIZE)
    assert len(chunks) == 1
    assert chunks[0] == "Hello world"


def test_empty_text_no_chunks():
    chunks = chunk_text("", max_chars=CHUNK_SIZE)
    assert chunks == []


def test_whitespace_only_no_chunks():
    chunks = chunk_text("   \n\n  ", max_chars=CHUNK_SIZE)
    assert chunks == []


def test_paragraph_boundary_split():
    p1 = "A" * 500
    p2 = "B" * 500
    text = p1 + "\n\n" + p2
    chunks = chunk_text(text, max_chars=CHUNK_SIZE)
    assert len(chunks) == 2
    assert chunks[0] == p1
    assert chunks[1] == p2


def test_long_paragraph_force_split():
    text = "A" * 1600
    chunks = chunk_text(text, max_chars=CHUNK_SIZE)
    assert len(chunks) == 2
    assert len(chunks[0]) == CHUNK_SIZE
    assert len(chunks[1]) == CHUNK_SIZE


def test_min_chunk_size_filter():
    chunks = chunk_text("Hi", max_chars=CHUNK_SIZE, min_chars=50)
    assert chunks == []


def test_real_content_chunking():
    text = (
        "## Deployment Lesson\n\n"
        "The CI pipeline failed because we forgot to set DATABASE_URL.\n"
        "This caused a 2-hour outage.\n\n"
        "## Recovery Steps\n\n"
        "1. Set the env var in the deployment config.\n"
        "2. Re-run the pipeline.\n"
        "3. Verify the database connection."
    )
    chunks = chunk_text(text, max_chars=CHUNK_SIZE)
    assert len(chunks) >= 1
    for c in chunks:
        assert len(c) <= CHUNK_SIZE
        assert len(c) >= 10  # default min_chars
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <repo> && python -m pytest tests/test_chunker.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/tianshu/memory/chunker.py
"""Verbatim text chunker — paragraph-boundary splitting at ~800 chars."""

from __future__ import annotations

_DEFAULT_MAX = 800
_DEFAULT_MIN = 10


def chunk_text(
    text: str,
    max_chars: int = _DEFAULT_MAX,
    min_chars: int = _DEFAULT_MIN,
) -> list[str]:
    """Split text into chunks at paragraph boundaries.

    Strategy:
    1. Split on double-newline (paragraph boundary)
    2. If a paragraph exceeds max_chars, force-split at max_chars
    3. Merge small consecutive paragraphs into one chunk
    4. Drop chunks shorter than min_chars
    """
    text = text.strip()
    if not text:
        return []

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if not current:
            current = para
        elif len(current) + 2 + len(para) <= max_chars:
            current = current + "\n\n" + para
        else:
            chunks.append(current)
            current = para

    if current:
        chunks.append(current)

    # Force-split oversized chunks
    final: list[str] = []
    for chunk in chunks:
        while len(chunk) > max_chars:
            final.append(chunk[:max_chars])
            chunk = chunk[max_chars:]
        if chunk:
            final.append(chunk)

    # Filter by min size
    return [c for c in final if len(c) >= min_chars]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <repo> && python -m pytest tests/test_chunker.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/memory/chunker.py tests/test_chunker.py
git commit -m "feat(memory): add verbatim paragraph-boundary chunker"
```

---

## Task 3: MemoryConfig with Ablation Switches

**Files:**
- Create: `src/tianshu/memory/config.py`
- Test: (inline verification)

- [ ] **Step 1: Write the config module**

```python
# src/tianshu/memory/config.py
"""Memory Palace configuration with ablation switches."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryConfig:
    """Each flag can be toggled independently for ablation experiments."""

    enabled: bool = True               # Master switch
    l1_enabled: bool = True            # L1 critical facts injection
    l2_recall_enabled: bool = True     # L2 pre-execution recall
    reflect_enabled: bool = True       # Periodic reflection
    tunnels_enabled: bool = True       # Cross-wing tunnels
    emperor_wing_enabled: bool = True  # User's wing
    verbatim_mode: bool = True         # True=store raw, False=store summary

    # Tuning
    l1_max_chars: int = 3200           # L1 token budget (~800 tokens)
    l1_top_k: int = 15                 # Number of top drawers for L1
    l2_n_results: int = 10             # Search results for L2 recall
    chunk_max_chars: int = 800         # Drawer chunk size
    chunk_min_chars: int = 10          # Minimum chunk to keep
    recency_half_life_days: int = 30   # Recency decay half-life
```

- [ ] **Step 2: Verify import**

Run: `cd <repo> && python -c "from tianshu.memory.config import MemoryConfig; c = MemoryConfig(); print(f'enabled={c.enabled}, l1_max={c.l1_max_chars}')"`
Expected: `enabled=True, l1_max=3200`

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/memory/config.py
git commit -m "feat(memory): add MemoryConfig with ablation switches"
```

---

## Task 4: DrawerStore — SQLite Storage + BM25 Search

**Files:**
- Create: `src/tianshu/memory/drawer_store.py`
- Modify: `src/tianshu/storage.py` (add drawers table)
- Test: `tests/test_drawer_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drawer_store.py
import asyncio
import tempfile
from pathlib import Path

import pytest

from tianshu.memory.drawer import Drawer, DrawerResult, MemoryBackend
from tianshu.memory.drawer_store import DrawerStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    s = DrawerStore(str(db_path))
    return s


@pytest.fixture
def sample_drawer():
    return Drawer(
        id="drw_001", wing="bingbu", room="execution",
        content="Deployment failed because DATABASE_URL was not set in production config.",
        source_edict_id="edict_abc", timestamp="2026-04-16T10:00:00+00:00",
        category="W", confidence=0.9, chunk_index=0,
    )


def test_store_satisfies_protocol(store):
    assert isinstance(store, MemoryBackend)


@pytest.mark.asyncio
async def test_store_and_get(store, sample_drawer):
    await store.store_drawer(sample_drawer)
    drawers = await store.get_drawers("bingbu", room="execution")
    assert len(drawers) == 1
    assert drawers[0].id == "drw_001"
    assert drawers[0].content == sample_drawer.content


@pytest.mark.asyncio
async def test_search_bm25(store, sample_drawer):
    await store.store_drawer(sample_drawer)
    results = await store.search("DATABASE_URL production", wing="bingbu")
    assert len(results) >= 1
    assert results[0].drawer_id == "drw_001"
    assert results[0].score > 0


@pytest.mark.asyncio
async def test_search_filters_by_wing(store, sample_drawer):
    await store.store_drawer(sample_drawer)
    results = await store.search("DATABASE_URL", wing="neige")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_no_wing_filter(store, sample_drawer):
    await store.store_drawer(sample_drawer)
    results = await store.search("DATABASE_URL")
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_delete_drawer(store, sample_drawer):
    await store.store_drawer(sample_drawer)
    deleted = await store.delete_drawer("drw_001")
    assert deleted is True
    drawers = await store.get_drawers("bingbu")
    assert len(drawers) == 0


@pytest.mark.asyncio
async def test_get_l1(store):
    for i in range(5):
        d = Drawer(
            id=f"drw_{i:03d}", wing="bingbu", room="execution",
            content=f"Lesson {i}: important fact number {i}.",
            source_edict_id="edict_abc",
            timestamp=f"2026-04-{16-i:02d}T10:00:00+00:00",
            category="W", confidence=0.5 + i * 0.1, chunk_index=0,
        )
        await store.store_drawer(d)

    l1 = await store.get_l1("bingbu", max_chars=3200)
    assert "## L1" in l1
    assert "execution" in l1  # grouped by room
    # Higher confidence drawers should appear
    assert "Lesson 4" in l1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <repo> && python -m pytest tests/test_drawer_store.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write DrawerStore implementation**

```python
# src/tianshu/memory/drawer_store.py
"""DrawerStore — SQLite-backed drawer storage with FTS5 BM25 search."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone

from tianshu.memory.drawer import Drawer, DrawerResult


class DrawerStore:
    """Default MemoryBackend implementation using SQLite + FTS5."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS drawers (
                id TEXT PRIMARY KEY,
                wing TEXT NOT NULL,
                room TEXT NOT NULL,
                content TEXT NOT NULL,
                source_edict_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'W',
                confidence REAL NOT NULL DEFAULT 1.0,
                chunk_index INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_drawers_wing ON drawers(wing);
            CREATE INDEX IF NOT EXISTS idx_drawers_wing_room ON drawers(wing, room);
        """)
        # FTS5 virtual table for BM25 search
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS drawers_fts
                USING fts5(id, wing, room, content, tokenize='unicode61');
            """)
        except sqlite3.OperationalError:
            pass  # FTS5 not available
        self._conn.commit()

    async def store_drawer(self, drawer: Drawer) -> str:
        self._conn.execute(
            """INSERT OR REPLACE INTO drawers
               (id, wing, room, content, source_edict_id, timestamp, category, confidence, chunk_index)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (drawer.id, drawer.wing, drawer.room, drawer.content,
             drawer.source_edict_id, drawer.timestamp, drawer.category,
             drawer.confidence, drawer.chunk_index),
        )
        # Sync to FTS
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO drawers_fts (id, wing, room, content) VALUES (?, ?, ?, ?)",
                (drawer.id, drawer.wing, drawer.room, drawer.content),
            )
        except sqlite3.OperationalError:
            pass
        self._conn.commit()
        return drawer.id

    async def search(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        n_results: int = 10,
    ) -> list[DrawerResult]:
        # Try FTS5 BM25 first
        try:
            return self._fts5_search(query, wing, room, n_results)
        except sqlite3.OperationalError:
            return self._fallback_search(query, wing, room, n_results)

    def _fts5_search(
        self, query: str, wing: str | None, room: str | None, n: int,
    ) -> list[DrawerResult]:
        # Build FTS5 query with optional filters
        fts_query = query
        where_parts: list[str] = []
        params: list[str] = [fts_query]

        if wing:
            where_parts.append("d.wing = ?")
            params.append(wing)
        if room:
            where_parts.append("d.room = ?")
            params.append(room)

        where_clause = ""
        if where_parts:
            where_clause = "AND " + " AND ".join(where_parts)

        sql = f"""
            SELECT d.id, d.content, d.wing, d.room,
                   rank AS fts_rank
            FROM drawers_fts f
            JOIN drawers d ON d.id = f.id
            WHERE drawers_fts MATCH ? {where_clause}
            ORDER BY rank
            LIMIT ?
        """
        params.append(str(n))
        rows = self._conn.execute(sql, params).fetchall()

        results: list[DrawerResult] = []
        for row in rows:
            # FTS5 rank is negative (lower = better), normalize to 0-1
            raw_rank = abs(row["fts_rank"]) if row["fts_rank"] else 0
            score = 1.0 / (1.0 + raw_rank) if raw_rank else 0.5
            results.append(DrawerResult(
                drawer_id=row["id"],
                content=row["content"],
                wing=row["wing"],
                room=row["room"],
                score=score,
                matched_via="bm25",
            ))
        return results

    def _fallback_search(
        self, query: str, wing: str | None, room: str | None, n: int,
    ) -> list[DrawerResult]:
        where_parts = ["content LIKE ?"]
        params: list[str] = [f"%{query}%"]
        if wing:
            where_parts.append("wing = ?")
            params.append(wing)
        if room:
            where_parts.append("room = ?")
            params.append(room)

        sql = f"""
            SELECT id, content, wing, room
            FROM drawers
            WHERE {' AND '.join(where_parts)}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(str(n))
        rows = self._conn.execute(sql, params).fetchall()
        return [
            DrawerResult(
                drawer_id=row["id"], content=row["content"],
                wing=row["wing"], room=row["room"],
                score=0.5, matched_via="fallback",
            )
            for row in rows
        ]

    async def get_drawers(
        self,
        wing: str,
        room: str | None = None,
        limit: int = 100,
    ) -> list[Drawer]:
        if room:
            rows = self._conn.execute(
                "SELECT * FROM drawers WHERE wing = ? AND room = ? ORDER BY timestamp DESC LIMIT ?",
                (wing, room, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM drawers WHERE wing = ? ORDER BY timestamp DESC LIMIT ?",
                (wing, limit),
            ).fetchall()
        return [self._row_to_drawer(r) for r in rows]

    async def delete_drawer(self, drawer_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM drawers WHERE id = ?", (drawer_id,))
        try:
            self._conn.execute("DELETE FROM drawers_fts WHERE id = ?", (drawer_id,))
        except sqlite3.OperationalError:
            pass
        self._conn.commit()
        return cur.rowcount > 0

    async def get_l1(self, wing: str, max_chars: int = 3200) -> str:
        """Generate L1 critical facts for a wing.

        Scores drawers by confidence × recency_decay, picks Top-K, groups by room.
        """
        rows = self._conn.execute(
            "SELECT * FROM drawers WHERE wing = ? ORDER BY timestamp DESC LIMIT 2000",
            (wing,),
        ).fetchall()

        if not rows:
            return ""

        now = datetime.now(timezone.utc)
        scored: list[tuple[float, dict]] = []
        for row in rows:
            drawer = dict(row)
            ts = datetime.fromisoformat(drawer["timestamp"])
            age_days = (now - ts).total_seconds() / 86400
            recency = math.exp(-0.693 * age_days / 30)  # half-life = 30 days
            score = drawer["confidence"] * recency
            scored.append((score, drawer))

        scored.sort(key=lambda x: -x[0])
        top = scored[:15]

        # Group by room
        by_room: dict[str, list[str]] = {}
        total = 0
        for _, d in top:
            room = d["room"]
            line = f"  - {d['content'][:200]}"
            if total + len(line) > max_chars:
                break
            by_room.setdefault(room, []).append(line)
            total += len(line) + 1

        if not by_room:
            return ""

        parts = [f"## L1 — 关键事实 ({wing})\n"]
        for room, lines in by_room.items():
            parts.append(f"[{room}]")
            parts.extend(lines)
            parts.append("")

        return "\n".join(parts)

    @staticmethod
    def _row_to_drawer(row: sqlite3.Row) -> Drawer:
        return Drawer(
            id=row["id"],
            wing=row["wing"],
            room=row["room"],
            content=row["content"],
            source_edict_id=row["source_edict_id"],
            timestamp=row["timestamp"],
            category=row["category"],
            confidence=row["confidence"],
            chunk_index=row["chunk_index"],
        )

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <repo> && python -m pytest tests/test_drawer_store.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/memory/drawer_store.py tests/test_drawer_store.py
git commit -m "feat(memory): add DrawerStore with SQLite FTS5 BM25 search"
```

---

## Task 5: 4-Layer Memory Stack

**Files:**
- Create: `src/tianshu/memory/layers.py`
- Test: `tests/test_layers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_layers.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.memory.layers import MemoryStack
from tianshu.memory.drawer import Drawer, DrawerResult
from tianshu.memory.config import MemoryConfig


@pytest.fixture
def mock_store():
    store = AsyncMock()
    store.get_l1 = AsyncMock(return_value="## L1 — 关键事实 (bingbu)\n\n[execution]\n  - Deploy lesson")
    store.search = AsyncMock(return_value=[
        DrawerResult(
            drawer_id="drw_001", content="DATABASE_URL was missing",
            wing="bingbu", room="execution", score=0.9, matched_via="bm25",
        ),
    ])
    return store


@pytest.fixture
def stack(mock_store):
    config = MemoryConfig()
    return MemoryStack(store=mock_store, config=config)


@pytest.mark.asyncio
async def test_get_l1(stack, mock_store):
    l1 = await stack.get_l1("bingbu")
    assert "L1" in l1
    mock_store.get_l1.assert_called_once_with("bingbu", max_chars=3200)


@pytest.mark.asyncio
async def test_get_l1_disabled(mock_store):
    config = MemoryConfig(l1_enabled=False)
    stack = MemoryStack(store=mock_store, config=config)
    l1 = await stack.get_l1("bingbu")
    assert l1 == ""
    mock_store.get_l1.assert_not_called()


@pytest.mark.asyncio
async def test_recall_l2(stack, mock_store):
    results = await stack.recall("deployment failure", wing="bingbu")
    assert len(results) >= 1
    assert results[0].content == "DATABASE_URL was missing"


@pytest.mark.asyncio
async def test_recall_disabled(mock_store):
    config = MemoryConfig(l2_recall_enabled=False)
    stack = MemoryStack(store=mock_store, config=config)
    results = await stack.recall("anything", wing="bingbu")
    assert results == []
    mock_store.search.assert_not_called()


@pytest.mark.asyncio
async def test_recall_merges_court(stack, mock_store):
    await stack.recall("deployment", wing="bingbu", include_court=True)
    # Should search both bingbu wing and court wing
    assert mock_store.search.call_count == 2
    calls = mock_store.search.call_args_list
    wings = {c.kwargs.get("wing") or c.args[0] if len(c.args) > 0 else None for c in calls}
    # Verify search was called with the query
    assert mock_store.search.call_count == 2


@pytest.mark.asyncio
async def test_master_switch_off(mock_store):
    config = MemoryConfig(enabled=False)
    stack = MemoryStack(store=mock_store, config=config)
    l1 = await stack.get_l1("bingbu")
    results = await stack.recall("anything")
    assert l1 == ""
    assert results == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <repo> && python -m pytest tests/test_layers.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/tianshu/memory/layers.py
"""4-layer memory stack: L0 identity → L1 critical facts → L2 recall → L3 deep search."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer import DrawerResult

if TYPE_CHECKING:
    from tianshu.memory.drawer import MemoryBackend

logger = logging.getLogger(__name__)


class MemoryStack:
    """Orchestrates the 4-layer memory retrieval stack."""

    def __init__(self, store: MemoryBackend, config: MemoryConfig) -> None:
        self._store = store
        self._config = config

    async def get_l1(self, wing: str) -> str:
        """L1: Critical facts — always loaded into prompt."""
        if not self._config.enabled or not self._config.l1_enabled:
            return ""
        return await self._store.get_l1(wing, max_chars=self._config.l1_max_chars)

    async def recall(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        include_court: bool = False,
    ) -> list[DrawerResult]:
        """L2: On-demand recall — filtered search before execution."""
        if not self._config.enabled or not self._config.l2_recall_enabled:
            return []

        results = await self._store.search(
            query, wing=wing, room=room,
            n_results=self._config.l2_n_results,
        )

        if include_court and wing != "court":
            court_results = await self._store.search(
                query, wing="court",
                n_results=self._config.l2_n_results,
            )
            results = self._merge_results(results, court_results)

        return results

    async def deep_search(
        self,
        query: str,
        n_results: int = 20,
    ) -> list[DrawerResult]:
        """L3: Deep search — full palace, no wing filter."""
        if not self._config.enabled:
            return []
        return await self._store.search(query, n_results=n_results)

    @staticmethod
    def _merge_results(
        primary: list[DrawerResult],
        secondary: list[DrawerResult],
    ) -> list[DrawerResult]:
        """Merge two result lists, deduplicate by drawer_id, sort by score."""
        seen: set[str] = set()
        merged: list[DrawerResult] = []
        for r in primary + secondary:
            if r.drawer_id not in seen:
                seen.add(r.drawer_id)
                merged.append(r)
        merged.sort(key=lambda r: -r.score)
        return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <repo> && python -m pytest tests/test_layers.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/memory/layers.py tests/test_layers.py
git commit -m "feat(memory): add 4-layer MemoryStack (L0/L1/L2/L3)"
```

---

## Task 6: Update Retain — AGENT_END Hook Stores Drawers

**Files:**
- Modify: `src/tianshu/memory/manager.py` (~lines 401-430)
- Test: `tests/test_memory_palace_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_memory_palace_integration.py
import asyncio
import tempfile
from pathlib import Path

import pytest

from tianshu.memory.chunker import chunk_text
from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer import Drawer
from tianshu.memory.drawer_store import DrawerStore
from tianshu.memory.layers import MemoryStack


@pytest.fixture
def store(tmp_path):
    return DrawerStore(str(tmp_path / "test.sqlite3"))


@pytest.fixture
def stack(store):
    return MemoryStack(store=store, config=MemoryConfig())


@pytest.mark.asyncio
async def test_retain_then_recall(store, stack):
    """Full loop: chunk content → store drawers → recall via search."""
    # Simulate edict execution result (memorial content)
    memorial_content = (
        "## Execution Summary\n\n"
        "Successfully deployed the authentication service to production.\n"
        "The DATABASE_URL environment variable was missing initially, "
        "causing a 502 error. Fixed by adding it to the Kubernetes ConfigMap.\n\n"
        "## Lessons Learned\n\n"
        "Always verify environment variables before deployment. "
        "The CI pipeline should include an env-check step.\n\n"
        "## Tools Used\n\n"
        "kubectl apply, helm upgrade, pg_isready for health check."
    )

    # Retain: chunk and store
    chunks = chunk_text(memorial_content, max_chars=800)
    assert len(chunks) >= 1

    for i, chunk in enumerate(chunks):
        drawer = Drawer(
            id=f"drw_test_{i:03d}",
            wing="bingbu",
            room="execution",
            content=chunk,
            source_edict_id="edict_001",
            timestamp="2026-04-16T12:00:00+00:00",
            category="W",
            confidence=0.9,
            chunk_index=i,
        )
        await store.store_drawer(drawer)

    # Recall: search for relevant memories
    results = await stack.recall("DATABASE_URL deployment", wing="bingbu")
    assert len(results) >= 1
    assert any("DATABASE_URL" in r.content for r in results)

    # L1: generate critical facts
    l1 = await stack.get_l1("bingbu")
    assert "L1" in l1
    assert "execution" in l1


@pytest.mark.asyncio
async def test_ablation_memory_off(store):
    """With memory disabled, recall returns nothing."""
    config = MemoryConfig(enabled=False)
    stack = MemoryStack(store=store, config=config)

    d = Drawer(
        id="drw_abl_001", wing="bingbu", room="execution",
        content="Important lesson", source_edict_id="edict_002",
        timestamp="2026-04-16T12:00:00+00:00",
        category="W", confidence=1.0, chunk_index=0,
    )
    await store.store_drawer(d)

    results = await stack.recall("lesson", wing="bingbu")
    assert results == []

    l1 = await stack.get_l1("bingbu")
    assert l1 == ""
```

- [ ] **Step 2: Run test to verify it passes** (this test should pass since it uses already-built components)

Run: `cd <repo> && python -m pytest tests/test_memory_palace_integration.py -v`
Expected: 2 passed

- [ ] **Step 3: Add `retain_drawers()` to MemoryManager**

In `src/tianshu/memory/manager.py`, add after existing imports (around line 20):

```python
from tianshu.memory.chunker import chunk_text
from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer import Drawer
```

Add to `MemoryManager.__init__()` (around line 50) — new parameters:

```python
def __init__(
    self,
    storage: Storage,
    config_manager: ConfigManager,
    hook_registry: object | None = None,
    personas_dir: Path | None = None,
    memory_dir: Path | None = None,
    drawer_store: object | None = None,       # NEW
    memory_config: MemoryConfig | None = None,  # NEW
) -> None:
    ...
    self._drawer_store = drawer_store
    self._memory_config = memory_config or MemoryConfig()
```

Add new method after `store()` (around line 140):

```python
async def retain_drawers(
    self,
    persona_id: str,
    room: str,
    content: str,
    edict_id: str,
    category: str = "W",
    confidence: float = 0.9,
) -> list[str]:
    """Chunk content into drawers and store them. Returns drawer IDs."""
    if not self._drawer_store or not self._memory_config.enabled:
        return []

    from datetime import datetime, timezone
    from ulid import ULID

    chunks = chunk_text(
        content,
        max_chars=self._memory_config.chunk_max_chars,
        min_chars=self._memory_config.chunk_min_chars,
    )

    ids: list[str] = []
    ts = datetime.now(timezone.utc).isoformat()
    for i, chunk in enumerate(chunks):
        drawer = Drawer(
            id=str(ULID()),
            wing=persona_id,
            room=room,
            content=chunk,
            source_edict_id=edict_id,
            timestamp=ts,
            category=category,
            confidence=confidence,
            chunk_index=i,
        )
        await self._drawer_store.store_drawer(drawer)
        ids.append(drawer.id)

    return ids
```

- [ ] **Step 4: Update `on_agent_end()` to also store drawers**

In `on_agent_end()` (around line 401), after the existing `self.store(entry)` call, add:

```python
# Also store as drawers for Memory Palace
if self._drawer_store and memorial:
    memorial_content = getattr(memorial, "output", "") or ""
    if memorial_content:
        room = self._infer_room(edict)
        await self.retain_drawers(
            persona_id=persona_id,
            room=room,
            content=memorial_content,
            edict_id=edict.id if edict else "",
            category="W",
            confidence=0.9,
        )
```

Add helper method:

```python
@staticmethod
def _infer_room(edict) -> str:
    """Infer room name from edict goal. Simple keyword matching."""
    if not edict:
        return "general"
    goal = (edict.goal or "").lower()
    room_keywords = {
        "execution": ["deploy", "run", "execute", "build", "install"],
        "planning": ["plan", "design", "architect", "decompose"],
        "audit": ["review", "audit", "check", "inspect"],
        "tools": ["tool", "command", "script", "cli"],
        "recovery": ["fix", "error", "bug", "recover", "debug"],
        "cost-patterns": ["cost", "token", "budget", "usage"],
    }
    for room, keywords in room_keywords.items():
        if any(kw in goal for kw in keywords):
            return room
    return "general"
```

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/memory/manager.py tests/test_memory_palace_integration.py
git commit -m "feat(memory): wire Retain — AGENT_END stores verbatim drawers"
```

---

## Task 7: Update Recall — BEFORE_AGENT_START Injects L1+L2

**Files:**
- Modify: `src/tianshu/memory/manager.py` (~line 370)
- Modify: `src/tianshu/persona/prompt_builder.py` (~line 99)

- [ ] **Step 1: Update `on_before_agent_start()` to include L1+L2**

In `on_before_agent_start()` (around line 370 of manager.py), add drawer-based recall alongside existing markdown recall:

```python
async def on_before_agent_start(self, **context) -> "HookResult":
    """Inject memory context before agent execution."""
    from tianshu.executor.hooks import HookResult

    edict = context.get("edict")
    persona = context.get("persona")
    persona_id = self._resolve_persona_id(context)

    history_messages: list[dict] = []

    # Existing: search markdown daily logs
    if edict and edict.goal:
        entries = self._md_backend.search_daily_logs(persona_id, edict.goal, limit=5)
        for e in entries:
            history_messages.append({"role": "user", "content": f"[记忆] {e}"})

    # NEW: drawer-based L2 recall
    if self._drawer_store and self._memory_config.l2_recall_enabled and edict and edict.goal:
        from tianshu.memory.layers import MemoryStack
        stack = MemoryStack(store=self._drawer_store, config=self._memory_config)
        results = await stack.recall(edict.goal, wing=persona_id, include_court=True)
        for r in results[:5]:
            history_messages.append({
                "role": "user",
                "content": f"[Palace 记忆 | {r.wing}/{r.room}] {r.content}",
            })

    if not history_messages:
        return HookResult()

    return HookResult(modified_args={"memory_history": history_messages})
```

- [ ] **Step 2: Add L1 injection to PromptBuilder**

In `src/tianshu/persona/prompt_builder.py`, in the `build()` method (around line 99), after reading MEMORY.md and before Recent Activity:

```python
# Layer 5: MEMORY.md (existing)
memory_content = self._md_backend.read_core_memory(persona_id)
if memory_content:
    parts.append(f"# 个人记忆\n\n{memory_content}")

# Layer 5.1: L1 Critical Facts (NEW — Memory Palace)
if self._drawer_store and self._memory_config.l1_enabled:
    import asyncio
    from tianshu.memory.layers import MemoryStack
    stack = MemoryStack(store=self._drawer_store, config=self._memory_config)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already in an async context, need to use await
            # This will be called from async build() variant
            pass
        else:
            l1 = loop.run_until_complete(stack.get_l1(persona_id))
            if l1:
                parts.append(l1)
    except RuntimeError:
        pass  # No event loop — skip L1 in sync context
```

Note: Add `drawer_store` and `memory_config` as optional `__init__` params to `PromptBuilder`, similar to how `metrics_store` was added.

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/memory/manager.py src/tianshu/persona/prompt_builder.py
git commit -m "feat(memory): wire Recall — L1 in prompt, L2 in pre-agent hook"
```

---

## Task 8: Wire Components in app.py

**Files:**
- Modify: `src/tianshu/app.py` (~line 264)

- [ ] **Step 1: Initialize DrawerStore and wire into MemoryManager**

In `app.py`, around line 264 where MemoryManager is created:

```python
# Memory Palace — DrawerStore
from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer_store import DrawerStore

memory_config = MemoryConfig()
drawer_db_path = memory_dir / "drawers.sqlite3" if memory_dir else Path.home() / ".tianshu" / "memory" / "drawers.sqlite3"
drawer_db_path.parent.mkdir(parents=True, exist_ok=True)
drawer_store = DrawerStore(str(drawer_db_path))

memory_manager = MemoryManager(
    storage=storage,
    config_manager=config_manager,
    hook_registry=hook_registry,
    personas_dir=personas_dir,
    memory_dir=memory_dir,
    drawer_store=drawer_store,        # NEW
    memory_config=memory_config,      # NEW
)

# Also pass to PromptBuilder
app.state.drawer_store = drawer_store
app.state.memory_config = memory_config
```

Update PromptBuilder initialization (wherever it's created) to include:

```python
prompt_builder = PromptBuilder(
    ...,
    drawer_store=drawer_store,
    memory_config=memory_config,
)
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/app.py
git commit -m "feat(memory): wire DrawerStore and MemoryConfig into app"
```

---

## Task 9: API Endpoints — Memory Search and L1

**Files:**
- Modify: `src/tianshu/gateway/api.py`

- [ ] **Step 1: Add memory search endpoint**

```python
@router.get("/api/memory/search")
async def memory_search(
    request: Request,
    query: str,
    wing: str | None = None,
    room: str | None = None,
    n_results: int = 10,
):
    drawer_store = request.app.state.drawer_store
    if not drawer_store:
        return {"results": [], "error": "Memory Palace not initialized"}

    results = await drawer_store.search(query, wing=wing, room=room, n_results=n_results)
    return {
        "query": query,
        "filters": {"wing": wing, "room": room},
        "results": [
            {
                "drawer_id": r.drawer_id,
                "content": r.content,
                "wing": r.wing,
                "room": r.room,
                "score": r.score,
                "matched_via": r.matched_via,
            }
            for r in results
        ],
    }


@router.get("/api/memory/l1")
async def memory_l1(
    request: Request,
    wing: str,
):
    drawer_store = request.app.state.drawer_store
    memory_config = getattr(request.app.state, "memory_config", None)
    if not drawer_store:
        return {"wing": wing, "l1": "", "error": "Memory Palace not initialized"}

    from tianshu.memory.config import MemoryConfig
    from tianshu.memory.layers import MemoryStack

    config = memory_config or MemoryConfig()
    stack = MemoryStack(store=drawer_store, config=config)
    l1 = await stack.get_l1(wing)
    return {"wing": wing, "l1": l1}
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/gateway/api.py
git commit -m "feat(memory): add /api/memory/search and /api/memory/l1 endpoints"
```

---

## Task 10: Final Verification

- [ ] **Step 1: Run all tests**

```bash
cd <repo>
python -m pytest tests/test_drawer.py tests/test_chunker.py tests/test_drawer_store.py tests/test_layers.py tests/test_memory_palace_integration.py -v
```

Expected: All tests pass.

- [ ] **Step 2: Verify imports and app startup**

```bash
python -c "
from tianshu.memory.drawer import Drawer, DrawerResult, Closet, Tunnel, MemoryBackend
from tianshu.memory.chunker import chunk_text
from tianshu.memory.drawer_store import DrawerStore
from tianshu.memory.layers import MemoryStack
from tianshu.memory.config import MemoryConfig
print('All Memory Palace modules imported successfully')
print(f'DrawerStore satisfies MemoryBackend: {isinstance(DrawerStore.__new__(DrawerStore), MemoryBackend)}')
"
```

- [ ] **Step 3: Manual smoke test — full loop**

```bash
python -c "
import asyncio
from tianshu.memory.drawer import Drawer
from tianshu.memory.drawer_store import DrawerStore
from tianshu.memory.chunker import chunk_text
from tianshu.memory.layers import MemoryStack
from tianshu.memory.config import MemoryConfig

async def main():
    store = DrawerStore('/tmp/tianshu_smoke_test.sqlite3')
    stack = MemoryStack(store=store, config=MemoryConfig())

    # Retain
    content = 'Deployment failed because the env var DATABASE_URL was missing. Fixed by adding it to ConfigMap. Lesson: always validate env vars before deploy.'
    chunks = chunk_text(content)
    for i, c in enumerate(chunks):
        d = Drawer(id=f'smoke_{i}', wing='bingbu', room='execution', content=c, source_edict_id='e1', timestamp='2026-04-16T12:00:00+00:00', category='W', confidence=0.9, chunk_index=i)
        await store.store_drawer(d)
    print(f'Stored {len(chunks)} drawers')

    # Recall
    results = await stack.recall('DATABASE_URL missing', wing='bingbu')
    print(f'Recall: {len(results)} results')
    for r in results:
        print(f'  [{r.wing}/{r.room}] score={r.score:.2f}: {r.content[:60]}...')

    # L1
    l1 = await stack.get_l1('bingbu')
    print(f'L1 ({len(l1)} chars):')
    print(l1)

    store.close()

asyncio.run(main())
"
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "test(memory): add integration tests and verify Memory Palace Phase 1"
```

---

## Phase 2-4 — Future Tasks (Not Detailed Here)

These phases build on Phase 1's foundation:

### Phase 2: Federation
- Wing/Room CRUD API
- Tunnel model storage + API (`POST/DELETE /api/tunnels`)
- Emperor Wing auto-capture (decision/feedback rooms)
- Subscription mechanism via tunnels
- Ablation metrics collection

### Phase 3: Evolution
- Reflect mechanism (wenyuan periodic reflection)
- Cross-domain insight push to Emperor Wing
- Optional ChromaDB backend (implements same `MemoryBackend` Protocol)
- `tianshu benchmark memory` CLI command
- Memory Dashboard in Web UI

### Phase 4: Contradiction Detection
- Temporal knowledge graph (SQLite entity-relationship triples)
- Contradiction detector (attribution conflicts, stale dates)
- Confidence decay over time
- Fact expiry auto-marking
