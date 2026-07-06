"""storage/memory_repo.py 的 CRUD 与检索往返测试(真 :memory: Storage)。"""

from __future__ import annotations

import pytest

from tianshu.memory.models import MemoryEntry
from tianshu.storage import Storage


@pytest.fixture
def storage():
    s = Storage(db_path=":memory:")
    s.init_db()
    yield s
    s.close()


def test_memory_entry_save_and_list_roundtrip(storage):
    entry = MemoryEntry(persona_id="p-test", content="测试记忆内容")
    storage.save_memory_entry(entry)
    rows = storage.list_memory_by_persona("p-test")
    assert len(rows) == 1
    assert rows[0].id == entry.id
    assert rows[0].persona_id == "p-test"
    assert rows[0].content == "测试记忆内容"


def test_search_memory_hits_and_misses(storage):
    e1 = MemoryEntry(persona_id="p-test", content="张三喜欢下棋")
    e2 = MemoryEntry(persona_id="p-test", content="李四喜欢钓鱼")
    storage.save_memory_entry(e1)
    storage.save_memory_entry(e2)

    hits = storage.search_memory("p-test", query="下棋")
    assert len(hits) == 1
    assert hits[0].content == "张三喜欢下棋"

    misses = storage.search_memory("p-test", query="不存在的关键词")
    assert misses == []


def test_delete_memory_entry(storage):
    entry = MemoryEntry(persona_id="p-test", content="待删除条目")
    storage.save_memory_entry(entry)

    assert storage.delete_memory_entry(entry.id) is True
    assert storage.list_memory_by_persona("p-test") == []
    assert storage.delete_memory_entry("no-such-id") is False


def test_delete_memory_entries_batch(storage):
    entries = [MemoryEntry(persona_id="p-test", content=f"条目{i}") for i in range(3)]
    for e in entries:
        storage.save_memory_entry(e)

    deleted = storage.delete_memory_entries_batch([entries[0].id, entries[1].id])
    assert deleted == 2

    remaining = storage.list_memory_by_persona("p-test")
    assert len(remaining) == 1
    assert remaining[0].id == entries[2].id
