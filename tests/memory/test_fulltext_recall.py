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
    assert escape_fts5_query("部署(生产)?") == '"部署(生产)?"'
    assert escape_fts5_query("foo bar") == '"foo" "bar"'
    assert escape_fts5_query('say "hi"') == '"say" """hi"""'
    assert escape_fts5_query("   ") == ""


def test_fts_search_no_crash_on_special_chars(storage):
    # 改前：未转义的特殊字符会触发 FTS5 语法错误并被吞成空（静默零召回）
    assert fts_search(storage._conn, "如何部署(生产环境)? @x") == []


def test_fts_search_retrieves_after_escape(storage):
    # 有数据：含特殊字符的 query 经转义后仍能检索到条目（不只是"不崩溃"）
    e = MemoryEntry(persona_id="wym", category="observation", content="部署(生产环境)完成 deploy-pp")
    storage.save_memory_entry(e)
    ids = fts_search(storage._conn, "deploy-pp 部署", persona_id="wym")
    assert e.id in ids


def test_store_is_write_through(manager, storage):
    entry = MemoryEntry(persona_id="wym", category="observation", content="部署成功 deploy-xyz123")
    manager.store(entry)
    ids = fts_search(storage._conn, "deploy-xyz123", persona_id="wym")
    assert entry.id in ids


# 直接测 _recall_fulltext：公开入口 on_before_agent_start 需构造完整 hook 上下文，私有方法能更精确地断言召回行为
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


def test_recall_includes_department_scope(manager):
    manager.store(MemoryEntry(persona_id="_dept_neige", category="insight", content="内阁公文 dept-rule-3"))
    hits = manager._recall_fulltext("wym", "dept-rule-3", department="neige", limit=5)
    assert any("dept-rule-3" in h for h in hits)


def test_compact_preserves_other_sections(manager):
    import asyncio
    from unittest.mock import AsyncMock

    from tianshu.memory.models import CompactionResult
    # 先用 memory_write 写一个私有 section
    manager._md_backend.write_section("wym", "## 心学要旨", mode="set", content="知行合一")
    # 造 >5 条 daily，让 compact 不走 "Not enough entries" 的 early-return
    for i in range(6):
        manager.store(MemoryEntry(persona_id="wym", category="observation", content=f"任务事件 {i}"))
    # mock compactor，避免真调 LLM
    manager._compactor.compact = AsyncMock(
        return_value=CompactionResult(original_count=6, compacted_count=1, summary="压缩摘要X"),
    )
    asyncio.run(manager.compact("wym"))
    text = manager._md_backend.read_core_memory("wym")
    assert "## 心学要旨" in text and "知行合一" in text   # 其他 section 保留
    assert "## 历史摘要" in text and "压缩摘要X" in text   # 摘要写进专属 section


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
