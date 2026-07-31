"""通知三级制 + 免打扰——urgent/low 立即外发，normal 在免打扰后补推。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tianshu.models import Memorial, TaskStatus
from tianshu.models.edict import Edict, EdictDispatch
from tianshu.notifier.notifier import Notifier


class _FakeChannel:
    def __init__(self):
        self.sent: list = []

    async def send(self, message, rendered):
        self.sent.append((message, rendered))
        return True


class _FakeRegistry:
    def __init__(self, channel):
        self._c = channel

    def get(self, name):
        return self._c


class TestQuietHours:
    def test_same_span(self):
        n = Notifier(storage=None, quiet_hours_start=23, quiet_hours_end=8)
        assert n._in_quiet_hours(2) and n._in_quiet_hours(23)
        assert not n._in_quiet_hours(12) and not n._in_quiet_hours(8)

    def test_disabled_when_equal(self):
        n = Notifier(storage=None, quiet_hours_start=0, quiet_hours_end=0)
        assert not n._in_quiet_hours(3)

    def test_daytime_span(self):
        n = Notifier(storage=None, quiet_hours_start=9, quiet_hours_end=17)
        assert n._in_quiet_hours(12) and not n._in_quiet_hours(20)


def _seed_edict(storage, priority: str):
    e = Edict(goal="notify me", priority=priority, dispatch=EdictDispatch(channels=["feishu"]))
    storage.save_edict(e)
    m = Memorial(edict_id=e.id, status=TaskStatus.COMPLETED, summary="done")
    storage.save_memorial(m)
    return e, m


class TestThreeTierDispatch:
    @pytest.fixture
    def setup(self, storage):
        ch = _FakeChannel()
        n = Notifier(
            storage=storage,
            channel_registry=_FakeRegistry(ch),
            quiet_hours_start=23,
            quiet_hours_end=8,
        )
        return n, ch

    async def _dispatch(self, n, e, m, hour):
        with patch.object(n, "_now_hour", return_value=hour):
            await n._dispatch_external(SimpleNamespace(edict_id=e.id), m, {"type": "x"})

    async def test_urgent_pierces_quiet(self, setup, storage):
        n, ch = setup
        e, m = _seed_edict(storage, "urgent")
        await self._dispatch(n, e, m, hour=2)  # 免打扰时段
        assert len(ch.sent) == 1  # 穿透,立即外发

    async def test_normal_deferred_in_quiet(self, setup, storage):
        n, ch = setup
        e, m = _seed_edict(storage, "normal")
        await self._dispatch(n, e, m, hour=2)  # 免打扰
        assert len(ch.sent) == 0  # 不即时外发
        assert len(storage.list_pending_notifications()) == 1  # 攒起来

    async def test_normal_immediate_in_daytime(self, setup, storage):
        n, ch = setup
        e, m = _seed_edict(storage, "normal")
        await self._dispatch(n, e, m, hour=14)  # 非免打扰
        assert len(ch.sent) == 1

    async def test_low_is_delivered_when_no_digest_pipeline_exists(self, setup, storage):
        n, ch = setup
        e, m = _seed_edict(storage, "low")
        await self._dispatch(n, e, m, hour=14)
        assert len(ch.sent) == 1
        assert len(storage.list_pending_notifications()) == 0

    async def test_flush_on_next_daytime_notification(self, setup, storage):
        n, ch = setup
        # 免打扰时攒一条 normal
        e1, m1 = _seed_edict(storage, "normal")
        await self._dispatch(n, e1, m1, hour=2)
        assert len(storage.list_pending_notifications()) == 1
        # 醒来后来一条新通知(白天)→ 先补推昨晚攒的 + 发新的
        e2, m2 = _seed_edict(storage, "urgent")
        await self._dispatch(n, e2, m2, hour=9)
        assert len(storage.list_pending_notifications()) == 0  # 攒的已补推
        assert len(ch.sent) == 2  # 补推 1 + 新 1
