import pytest

from tianshu.memory.chunker import chunk_text
from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer import Drawer
from tianshu.memory.drawer_store import DrawerStore
from tianshu.memory.layers import MemoryStack


@pytest.fixture
def store(tmp_path):
    s = DrawerStore(str(tmp_path / "test.sqlite3"))
    yield s
    s.close()


@pytest.fixture
def stack(store):
    return MemoryStack(store=store, config=MemoryConfig())


@pytest.mark.asyncio
async def test_retain_then_recall(store, stack):
    """Full loop: chunk content -> store drawers -> recall via search."""
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
        id="drw_abl_001",
        wing="bingbu",
        room="execution",
        content="Important lesson",
        source_edict_id="edict_002",
        timestamp="2026-04-16T12:00:00+00:00",
        category="W",
        confidence=1.0,
        chunk_index=0,
    )
    await store.store_drawer(d)

    results = await stack.recall("lesson", wing="bingbu")
    assert results == []

    l1 = await stack.get_l1("bingbu")
    assert l1 == ""
