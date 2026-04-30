"""submit_edict tool 单元测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models.common import TaskStatus
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.submit_edict import register_submit_edict


@pytest.fixture
def setup(storage):
    bus = EventBus(storage=storage)
    registry = ToolRegistry()
    register_submit_edict(registry, storage=storage, event_bus=bus)
    return registry, storage, bus


@pytest.mark.asyncio
async def test_submit_edict_creates_edict_and_memorial(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="帮我整理飞书通知模板")
    assert result.is_error is False
    assert "已颁敕" in result.content
    edict_id = result.details["edict_id"]
    edict = storage.get_edict(edict_id)
    assert edict is not None
    assert edict.goal == "帮我整理飞书通知模板"
    assert edict.submitter == "assistant"
    memorials = storage.list_memorials_by_edict(edict_id)
    assert len(memorials) == 1
    assert memorials[0].status == TaskStatus.SUBMITTED


@pytest.mark.asyncio
async def test_submit_edict_fires_event(setup):
    registry, _, bus = setup
    received: list = []

    async def handler(ev):
        received.append(ev)

    bus.on("edict.submitted", handler, priority=200)
    _, func = registry._tools["submit_edict"]
    res = await func(goal="x", priority="urgent")
    import asyncio
    await asyncio.sleep(0.05)
    assert any(e.edict_id == res.details["edict_id"] for e in received)


@pytest.mark.asyncio
async def test_submit_edict_rejects_empty_goal(setup):
    registry, _, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="")
    assert result.is_error is True
    assert "goal 不能为空" in result.content


@pytest.mark.asyncio
async def test_submit_edict_rejects_invalid_priority(setup):
    registry, _, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", priority="critical")
    assert result.is_error is True
    assert "priority" in result.content


@pytest.mark.asyncio
async def test_submit_edict_validates_persona_when_loader_provided(storage):
    bus = EventBus(storage=storage)
    registry = ToolRegistry()
    loader = MagicMock()
    loader.get.return_value = None  # persona 不存在
    register_submit_edict(registry, storage=storage, event_bus=bus, persona_loader=loader)
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", assigned_persona_id="ghost_persona")
    assert result.is_error is True
    assert "ghost_persona" in result.content


@pytest.mark.asyncio
async def test_submit_edict_accepts_known_persona(storage):
    bus = EventBus(storage=storage)
    registry = ToolRegistry()
    loader = MagicMock()
    persona = MagicMock()
    persona.id = "bingbu"
    loader.get.return_value = persona
    register_submit_edict(registry, storage=storage, event_bus=bus, persona_loader=loader)
    _, func = registry._tools["submit_edict"]
    result = await func(goal="部署灰度", assigned_persona_id="bingbu")
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.assigned_persona_id == "bingbu"


@pytest.mark.asyncio
async def test_submit_edict_short_goal_used_as_title(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="部署网关")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.title == "部署网关"  # < 20 字符不截断


@pytest.mark.asyncio
async def test_submit_edict_long_goal_truncated_in_title(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    long_goal = "x" * 50
    result = await func(goal=long_goal)
    edict = storage.get_edict(result.details["edict_id"])
    assert len(edict.title) <= 21  # 20 + ellipsis
    assert edict.title.endswith("…")


@pytest.mark.asyncio
async def test_submit_edict_in_definition(setup):
    """ToolDefinition 字段（schema、tier）正确。"""
    registry, _, _ = setup
    defn = registry.get_definition("submit_edict")
    assert defn is not None
    assert defn.name == "submit_edict"
    schema = defn.parameters
    assert "goal" in schema["properties"]
    assert "execution_profile" in schema["properties"]
    assert schema["required"] == ["goal"]
    assert defn.tier == 2  # T2_NETWORK


@pytest.mark.asyncio
async def test_submit_edict_default_profile_foreground(setup):
    """不传 execution_profile 时默认走短任务（foreground）。"""
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.execution_profile == "foreground"
    assert "短任务" in result.content


@pytest.mark.asyncio
async def test_submit_edict_background_profile(setup):
    """长任务：execution_profile=background。"""
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="爬取站点", execution_profile="background")
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.execution_profile == "background"
    assert "长任务" in result.content


@pytest.mark.asyncio
async def test_submit_edict_checkpointed_profile(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", execution_profile="checkpointed")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.execution_profile == "checkpointed"


@pytest.mark.asyncio
async def test_submit_edict_rejects_invalid_profile(setup):
    registry, _, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", execution_profile="async")
    assert result.is_error is True
    assert "execution_profile" in result.content
