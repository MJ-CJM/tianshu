"""EdictBridge：续接 / 自动新建（X1） / EdictBusyError 单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.edict_bridge import EdictBridge, EdictBusyError
from tianshu.gateway.feishu.session_anchor import SessionAnchor
from tianshu.models.common import EdictStatus, TaskStatus


@pytest.fixture
def bridge(storage):
    bus = EventBus(storage=storage)
    anchor = SessionAnchor(storage)
    executor = MagicMock()
    executor.execute_edict = AsyncMock()
    executor.running_tasks = set()
    return (
        EdictBridge(
            storage=storage, event_bus=bus, executor=executor, anchor=anchor,
        ),
        bus,
        anchor,
    )


@pytest.mark.asyncio
async def test_create_new_when_no_anchor(bridge, storage):
    b, _, anchor = bridge
    eid = await b.continue_or_create(
        chat_id="oc_x", sender_open_id="ou_a", text="帮我查最近 3 天天气",
    )
    assert eid
    edict = storage.get_edict(eid)
    assert edict is not None
    assert edict.goal == "帮我查最近 3 天天气"
    assert edict.metadata.get("chat_id") == "oc_x"
    assert edict.metadata.get("feishu_user") == "ou_a"
    assert anchor.get("oc_x") == eid
    # 应自动创建一个 SUBMITTED memorial
    memorials = storage.list_memorials_by_edict(eid)
    assert len(memorials) == 1
    assert memorials[0].status == TaskStatus.SUBMITTED


@pytest.mark.asyncio
async def test_create_new_explicit(bridge, storage):
    b, _, anchor = bridge
    eid = await b.create_new(chat_id="oc_y", sender_open_id="ou_b", goal="部署流水线")
    edict = storage.get_edict(eid)
    assert edict.title.startswith("部署流水线")
    assert anchor.get("oc_y") == eid


@pytest.mark.asyncio
async def test_x1_auto_new_when_anchor_closed(bridge, storage):
    """X1: anchor 指向的 Edict 已 COMPLETED → 自动新建（无感）。"""
    b, _, anchor = bridge
    # 第一条：创建
    eid1 = await b.create_new(chat_id="oc_x", sender_open_id="ou_a", goal="任务一")
    # 把第一个 Edict 关掉
    storage.update_edict_status(eid1, EdictStatus.COMPLETED.value)
    # 第二条：anchor 仍指向 eid1，但已结案 → 应自动新建
    eid2 = await b.continue_or_create(chat_id="oc_x", sender_open_id="ou_a", text="任务二")
    assert eid2 != eid1
    assert anchor.get("oc_x") == eid2


@pytest.mark.asyncio
async def test_continue_or_create_with_active_anchor_follow_up(bridge, storage):
    """active Edict + 历史 memorial 全部 COMPLETED → 走 follow_up，复用同一 Edict。"""
    b, _, anchor = bridge
    eid = await b.create_new(chat_id="oc_x", sender_open_id="ou_a", goal="g")
    # 标历史 memorial 全部 COMPLETED
    memorials = storage.list_memorials_by_edict(eid)
    for m in memorials:
        m.status = TaskStatus.COMPLETED
        storage.update_memorial(m)
    eid2 = await b.continue_or_create(chat_id="oc_x", sender_open_id="ou_a", text="more")
    assert eid2 == eid
    assert anchor.get("oc_x") == eid
    # follow_up 会再加一个 SUBMITTED memorial
    memorials = storage.list_memorials_by_edict(eid)
    assert any(m.status == TaskStatus.SUBMITTED and m.instruction == "more" for m in memorials)


@pytest.mark.asyncio
async def test_continue_or_create_raises_when_active_memorial(bridge, storage):
    """有 active memorial（SUBMITTED/RUNNING）时应抛 EdictBusyError。"""
    b, _, _ = bridge
    await b.create_new(chat_id="oc_x", sender_open_id="ou_a", goal="g")
    # 不修改 memorial 状态（仍是 SUBMITTED）→ should raise
    with pytest.raises(EdictBusyError):
        await b.continue_or_create(chat_id="oc_x", sender_open_id="ou_a", text="more")


@pytest.mark.asyncio
async def test_create_new_emits_edict_submitted_event(bridge, storage):
    """create_new 应 fire edict.submitted 事件（含 chat_id）。"""
    b, bus, _ = bridge
    received: list = []

    async def handler(ev):
        received.append(ev)

    bus.on("edict.submitted", handler, priority=200)
    eid = await b.create_new(chat_id="oc_z", sender_open_id="ou_c", goal="x")
    # EventBus.fire 是同步派发到 handler（asyncio.create_task），等一拍
    import asyncio
    await asyncio.sleep(0.05)
    assert any(e.edict_id == eid for e in received)
