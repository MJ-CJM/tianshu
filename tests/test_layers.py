from unittest.mock import AsyncMock

import pytest

from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer import DrawerResult
from tianshu.memory.layers import MemoryStack


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


@pytest.mark.asyncio
async def test_master_switch_off(mock_store):
    config = MemoryConfig(enabled=False)
    stack = MemoryStack(store=mock_store, config=config)
    l1 = await stack.get_l1("bingbu")
    results = await stack.recall("anything")
    assert l1 == ""
    assert results == []
