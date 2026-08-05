"""记忆召回全量化 + compact 非破坏化 测试。

复用 tests/conftest.py 的 storage / config_manager fixtures。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tianshu.memory.access_control import MemoryAccessControl, MemoryAccessPolicy
from tianshu.memory.fts import escape_fts5_query, fts_search
from tianshu.memory.manager import MemoryManager
from tianshu.memory.markdown_backend import MarkdownMemoryBackend
from tianshu.memory.models import MemoryEntry
from tianshu.persona.model import AgentPersona
from tianshu.tools.memory_tools import _memory_write


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
    e = MemoryEntry(
        persona_id="wym", category="observation", content="部署(生产环境)完成 deploy-pp"
    )
    storage.save_memory_entry(e)
    ids = fts_search(storage._conn, "deploy-pp 部署", persona_id="wym")
    assert e.id in ids


def test_store_is_write_through(manager, storage):
    entry = MemoryEntry(persona_id="wym", category="observation", content="部署成功 deploy-xyz123")
    manager.store(entry)
    ids = fts_search(storage._conn, "deploy-xyz123", persona_id="wym")
    assert entry.id in ids


def test_store_does_not_index_when_markdown_write_fails(manager, storage, monkeypatch):
    entry = MemoryEntry(persona_id="wym", category="observation", content="must-not-index")

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(manager._md_backend, "append_daily_log", fail_write)

    with pytest.raises(OSError, match="disk full"):
        manager.store(entry)

    assert storage.list_memory_by_persona("wym") == []


def test_delete_uses_stable_id_for_similar_entries(manager, storage):
    created_at = datetime(2026, 7, 31, 8, 30, tzinfo=UTC)
    first = MemoryEntry(
        id="memory-first",
        persona_id="wym",
        content="发布前检查",
        created_at=created_at,
    )
    second = MemoryEntry(
        id="memory-second",
        persona_id="wym",
        content="发布前检查已完成",
        created_at=created_at,
    )
    manager.store(first)
    manager.store(second)

    assert manager.delete(first.id) is True

    log_text = (manager.memory_dir / "wym" / "2026-07-31.md").read_text(encoding="utf-8")
    assert "[id:memory-first]" not in log_text
    assert "[id:memory-second]" in log_text
    assert [entry.id for entry in storage.list_memory_by_persona("wym")] == [second.id]


def test_sync_index_preserves_stable_entry_id(manager, storage):
    entry = MemoryEntry(id="memory-stable", persona_id="wym", content="可重建索引")
    manager.store(entry)

    assert manager.sync_index("wym") == 1
    rebuilt = storage.list_memory_by_persona("wym")
    assert len(rebuilt) == 1
    assert rebuilt[0].id == entry.id


def test_sync_index_preserves_multiline_content(manager, storage):
    content = "第一行\n第二行\n\n第四行"
    entry = MemoryEntry(id="memory-multiline", persona_id="wym", content=content)
    manager.store(entry)

    assert manager.sync_index("wym") == 1
    rebuilt = storage.list_memory_by_persona("wym")
    assert len(rebuilt) == 1
    assert rebuilt[0].id == entry.id
    assert rebuilt[0].content == content


def test_memory_access_policy_survives_restart(storage):
    access = MemoryAccessControl(storage)
    access.set_policy(
        MemoryAccessPolicy(
            "custom",
            can_read=["court"],
            can_write=["wenyuan"],
            share_level="shared",
        )
    )

    restored = MemoryAccessControl(storage).get_policy("custom")

    assert restored.can_read == ["court"]
    assert restored.can_write == ["wenyuan"]
    assert restored.share_level == "shared"


@pytest.mark.asyncio
async def test_memory_write_replace_and_remove_refresh_index(storage, tmp_path):
    from tianshu.kernel.ambient import bind_persona

    md = MarkdownMemoryBackend(memory_dir=tmp_path / "memory", personas_dir=tmp_path)
    persona = AgentPersona(
        id="wym",
        name="王阳明",
        department="neige",
        soul_path=tmp_path / "SOUL.md",
        role_path=tmp_path / "ROLE.md",
        memory_path=tmp_path / "MEMORY.md",
    )
    event_bus = AsyncMock()

    with bind_persona(persona):
        added = await _memory_write(
            md,
            event_bus,
            storage,
            action="add",
            scope="self",
            section="发布经验",
            content="旧的发布步骤",
        )
        replaced = await _memory_write(
            md,
            event_bus,
            storage,
            action="replace",
            scope="self",
            section="发布经验",
            old_text="旧的",
            content="新的",
        )

    assert added.is_error is False
    assert replaced.is_error is False
    indexed = storage.list_memory_by_persona("wym")
    assert [entry.content for entry in indexed] == ["新的发布步骤"]

    with bind_persona(persona):
        removed = await _memory_write(
            md,
            event_bus,
            storage,
            action="remove",
            scope="self",
            section="发布经验",
            old_text="新的发布步骤",
        )

    assert removed.is_error is False
    assert storage.list_memory_by_persona("wym") == []


@pytest.mark.asyncio
async def test_memory_write_only_refreshes_the_target_section_projection(storage, tmp_path):
    from tianshu.kernel.ambient import bind_persona

    md = MarkdownMemoryBackend(memory_dir=tmp_path / "memory", personas_dir=tmp_path)
    persona = AgentPersona(
        id="wym",
        name="王阳明",
        department="neige",
        soul_path=tmp_path / "SOUL.md",
        role_path=tmp_path / "ROLE.md",
        memory_path=tmp_path / "MEMORY.md",
    )

    with bind_persona(persona):
        for section in ("发布经验", "复盘经验"):
            result = await _memory_write(
                md,
                None,
                storage,
                action="add",
                scope="self",
                section=section,
                content="共同短语：先验证",
            )
            assert result.is_error is False
        replaced = await _memory_write(
            md,
            None,
            storage,
            action="replace",
            scope="self",
            section="发布经验",
            old_text="先验证",
            content="先灰度",
        )

    assert replaced.is_error is False
    indexed = sorted(entry.content for entry in storage.list_memory_by_persona("wym"))
    assert indexed == ["共同短语：先灰度", "共同短语：先验证"]


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
    manager.store(
        MemoryEntry(persona_id="court", category="insight", content="朝廷共识 court-rule-7")
    )
    hits = manager._recall_fulltext("wym", "court-rule-7", limit=5)
    assert any("court-rule-7" in h for h in hits)


def test_recall_includes_department_scope(manager):
    manager.store(
        MemoryEntry(persona_id="_dept_neige", category="insight", content="内阁公文 dept-rule-3")
    )
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
        manager.store(
            MemoryEntry(persona_id="wym", category="observation", content=f"任务事件 {i}")
        )
    # mock compactor，避免真调 LLM
    manager._compactor.compact = AsyncMock(
        return_value=CompactionResult(original_count=6, compacted_count=1, summary="压缩摘要X"),
    )
    asyncio.run(manager.compact("wym"))
    text = manager._md_backend.read_core_memory("wym")
    assert "## 心学要旨" in text and "知行合一" in text  # 其他 section 保留
    assert "## 历史摘要" in text and "压缩摘要X" in text  # 摘要写进专属 section


def test_mutate_section_set_preserves_other_sections():
    existing = "# wym Memory\n\n## 心学要旨\n知行合一\n\n## 历史摘要\n旧摘要\n"
    out = MarkdownMemoryBackend._mutate_section(
        existing,
        "## 历史摘要",
        mode="set",
        content="全新摘要",
        old_text=None,
    )
    assert "## 心学要旨" in out and "知行合一" in out
    assert "旧摘要" not in out and "全新摘要" in out


def test_set_creates_section_when_absent(tmp_path):
    md = MarkdownMemoryBackend(memory_dir=tmp_path, personas_dir=tmp_path / "personas")
    md.write_section("wym", "## 历史摘要", mode="set", content="首个摘要")
    text = md.read_core_memory("wym")
    assert "## 历史摘要" in text and "首个摘要" in text


@pytest.mark.asyncio
async def test_memory_write_is_dedupe_idempotent_and_declares_semantics(storage, tmp_path):
    """memory_write 重放同一条内容会被去重，故必须声明 PROVIDER_IDEMPOTENT。

    回归（2026-08-05）：漏声明时 managed_tools 兜底成 OPAQUE_CLI
    （`semantics or OPAQUE_CLI`），改走"结果不确定→挂起转人工审批"路径，
    agent 在对话里调用可能直接报错（王阳明："记忆写入工具此刻暂未响应"）。
    """
    from tianshu.bus.event_bus import EventBus
    from tianshu.models.side_effect import SideEffectSemantics
    from tianshu.tools.memory_tools import register_memory_tools
    from tianshu.tools.registry import ToolRegistry

    md = MarkdownMemoryBackend(memory_dir=tmp_path / "memory", personas_dir=tmp_path)
    args = dict(action="add", scope="court", section="用户偏好", content="称呼用「佳民」")
    first = await _memory_write(md, None, storage, **args)
    assert first.is_error is False
    # 重放不追加第二遍 —— 这正是 PROVIDER_IDEMPOTENT 的前提
    replay = await _memory_write(md, None, storage, **args)
    assert replay.is_error is True
    assert "dedupe" in replay.content

    registry = ToolRegistry()
    register_memory_tools(
        registry,
        storage=storage,
        event_bus=EventBus(),
        memory_dir=tmp_path / "memory",
        personas_dir=tmp_path,
    )
    defn = registry.get_definition("memory_write")
    assert defn.side_effect is True
    assert defn.managed_effect_semantics is SideEffectSemantics.PROVIDER_IDEMPOTENT
