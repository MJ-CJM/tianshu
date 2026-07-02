"""#2 并发去重 + schedule_run 台账 —— 回归测试（Multica 借鉴）。"""
import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict, EdictSchedule
from tianshu.models.memorial import Memorial
from tianshu.scheduler.scheduler import Scheduler
from tianshu.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    return s


def test_has_unfinished_memorials(storage):
    e = Edict(goal="x")
    storage.save_edict(e)
    assert storage.has_unfinished_memorials(e.id) is False
    storage.save_memorial(Memorial(edict_id=e.id, status=TaskStatus.RUNNING))
    assert storage.has_unfinished_memorials(e.id) is True


def test_terminal_memorial_not_unfinished(storage):
    e = Edict(goal="x")
    storage.save_edict(e)
    storage.save_memorial(Memorial(edict_id=e.id, status=TaskStatus.COMPLETED))
    assert storage.has_unfinished_memorials(e.id) is False


def test_skip_for_concurrency(storage):
    sched = Scheduler(EventBus(), storage)
    e_skip = Edict(goal="s", schedule=EdictSchedule(
        type="cron", cron="* * * * *", concurrency_policy="skip"))
    e_allow = Edict(goal="a", schedule=EdictSchedule(
        type="cron", cron="* * * * *", concurrency_policy="allow"))
    storage.save_edict(e_skip)
    storage.save_edict(e_allow)
    storage.save_memorial(Memorial(edict_id=e_skip.id, status=TaskStatus.RUNNING))
    storage.save_memorial(Memorial(edict_id=e_allow.id, status=TaskStatus.RUNNING))
    assert sched._skip_for_concurrency(e_skip) is True
    assert sched._skip_for_concurrency(e_allow) is False


async def test_fire_scheduled_records_fired(storage):
    bus = EventBus()
    fired = []

    async def _cap(ev):
        fired.append(ev.edict_id)

    bus.on("edict.scheduled", _cap)
    sched = Scheduler(bus, storage)
    e = Edict(goal="x", schedule=EdictSchedule(type="cron", cron="* * * * *"))
    storage.save_edict(e)
    await sched._fire_scheduled(e, "cron")
    assert fired == [e.id]
    assert storage.list_schedule_runs(source=e.id)[0]["status"] == "fired"


async def test_fire_scheduled_skips_when_unfinished(storage):
    bus = EventBus()
    fired = []

    async def _cap(ev):
        fired.append(ev.edict_id)

    bus.on("edict.scheduled", _cap)
    sched = Scheduler(bus, storage)
    e = Edict(goal="x", schedule=EdictSchedule(
        type="cron", cron="* * * * *", concurrency_policy="skip"))
    storage.save_edict(e)
    storage.save_memorial(Memorial(edict_id=e.id, status=TaskStatus.RUNNING))
    await sched._fire_scheduled(e, "cron")
    assert fired == []
    assert storage.list_schedule_runs(source=e.id)[0]["status"] == "skipped"


def test_system_job_ledger_and_dedup(storage):
    assert storage.has_running_system_job("job.x") is False
    rid = storage.create_schedule_run(source="job.x", kind="system", status="running")
    assert storage.has_running_system_job("job.x") is True
    storage.finish_schedule_run(rid, "completed")
    assert storage.has_running_system_job("job.x") is False
    run = storage.list_schedule_runs(source="job.x")[0]
    assert run["status"] == "completed"
    assert run["finished_at"]
