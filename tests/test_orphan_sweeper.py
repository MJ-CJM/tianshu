"""#1 Sweeper 孤儿任务回收 —— 回归测试（Multica 借鉴）。"""

from datetime import UTC, datetime, timedelta

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict, EdictRuntime
from tianshu.models.memorial import Memorial
from tianshu.scheduler.scheduler import Scheduler
from tianshu.storage import Storage


def _ago(seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    yield s
    s.close()


def _mem(s, edict, hb_ago=None, status=TaskStatus.RUNNING):
    m = Memorial(
        edict_id=edict.id,
        status=status,
        started_at=_ago(2000),
        last_heartbeat_at=_ago(hb_ago) if hb_ago is not None else None,
    )
    s.save_memorial(m)
    return m


def test_heartbeat_roundtrip(storage):
    e = Edict(goal="x")
    storage.save_edict(e)
    m = _mem(storage, e, hb_ago=100)
    assert storage.get_memorial(m.id).last_heartbeat_at is not None


def test_list_stale_excludes_fresh(storage):
    e = Edict(goal="x")
    storage.save_edict(e)
    stale = _mem(storage, e, hb_ago=1000)
    fresh = _mem(storage, e, hb_ago=5)
    ids = {m.id for m in storage.list_stale_memorials(idle_seconds=900)}
    assert stale.id in ids
    assert fresh.id not in ids


def test_append_event_heartbeat_active_only(storage):
    e = Edict(goal="x")
    storage.save_edict(e)
    running = _mem(storage, e, hb_ago=1000, status=TaskStatus.RUNNING)
    done = _mem(storage, e, hb_ago=1000, status=TaskStatus.COMPLETED)
    storage.append_event(e.id, running.id, "t", {})
    storage.append_event(e.id, done.id, "t", {})
    fresh = storage.get_memorial(running.id).last_heartbeat_at
    stale = storage.get_memorial(done.id).last_heartbeat_at
    assert (datetime.now(UTC) - fresh).total_seconds() < 30
    assert (datetime.now(UTC) - stale).total_seconds() > 900


async def test_recover_orphan_foreground_fails(storage):
    e = Edict(goal="fg")
    storage.save_edict(e)
    m = _mem(storage, e, hb_ago=1000)
    bus = EventBus()
    seen = []

    async def _cap(ev):
        seen.append(ev.memorial_id)

    bus.on("execution.failed", _cap)
    sched = Scheduler(bus, storage)
    await sched._recover_orphan(m)
    assert storage.get_memorial(m.id).status == TaskStatus.FAILED
    assert m.id in seen


async def test_recover_orphan_checkpointed_resumes(storage):
    e = Edict(goal="cp", acceptance=AcceptanceCriteria(), execution_profile="checkpointed")
    storage.save_edict(e)
    m = _mem(storage, e, hb_ago=1000)
    bus = EventBus()
    seen = []

    async def _cap(ev):
        seen.append(ev.memorial_id)

    bus.on("edict.resume", _cap)
    sched = Scheduler(bus, storage)
    await sched._recover_orphan(m)
    assert m.id in seen
    assert storage.get_memorial(m.id).status == TaskStatus.RUNNING  # resume 不改状态


async def test_recover_orphan_paused_skipped(storage):
    e = Edict(
        goal="p",
        acceptance=AcceptanceCriteria(),
        execution_profile="checkpointed",
        runtime=EdictRuntime(lifecycle_phase="paused"),
    )
    storage.save_edict(e)
    m = _mem(storage, e, hb_ago=1000)
    bus = EventBus()
    seen = []

    async def _cap(ev):
        seen.append(ev.memorial_id)

    bus.on("edict.resume", _cap)
    bus.on("execution.failed", _cap)
    sched = Scheduler(bus, storage)
    await sched._recover_orphan(m)
    assert seen == []
    assert storage.get_memorial(m.id).status == TaskStatus.RUNNING


async def test_recover_orphan_resume_refreshes_heartbeat(storage):
    """resume 后刷新心跳 → 不再出现在 stale 列表，防 sweeper 每周期重发 resume 风暴。"""
    e = Edict(goal="cp", acceptance=AcceptanceCriteria(), execution_profile="checkpointed")
    storage.save_edict(e)
    m = _mem(storage, e, hb_ago=1000)
    sched = Scheduler(EventBus(), storage)
    await sched._recover_orphan(m)
    hb = storage.get_memorial(m.id).last_heartbeat_at
    assert (datetime.now(UTC) - hb).total_seconds() < 30
    assert m.id not in {x.id for x in storage.list_stale_memorials(idle_seconds=30)}
