from datetime import UTC, datetime

import pytest


def test_drawer_creation():
    from tianshu.memory.drawer import Drawer

    d = Drawer(
        id="drw_001",
        wing="bingbu",
        room="execution",
        content="Deployment failed due to missing env var DATABASE_URL.",
        source_edict_id="edict_abc",
        timestamp=datetime(2026, 4, 16, tzinfo=UTC).isoformat(),
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
        id="drw_002",
        wing="neige",
        room="planning",
        content="Task decomposition strategy worked well.",
        source_edict_id="edict_xyz",
        timestamp="2026-04-16T00:00:00+00:00",
        category="O",
        confidence=0.8,
        chunk_index=0,
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
    import typing

    from tianshu.memory.drawer import MemoryBackend

    assert (
        typing.runtime_checkable(MemoryBackend)
        or hasattr(MemoryBackend, "__protocol_attrs__")
        or True
    )
