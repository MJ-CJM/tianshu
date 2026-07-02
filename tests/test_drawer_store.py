import pytest

from tianshu.memory.drawer import Drawer, MemoryBackend
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
