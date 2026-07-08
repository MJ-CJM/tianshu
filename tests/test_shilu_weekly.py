"""实录馆·自动汇编(迭代 7「制度补全」)——本周敕令/执行/代批数据汇编成《实录》周报。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tianshu.models.common import TaskStatus
from tianshu.models.decree import Decree
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.notifier.digest import DigestGenerator


def test_weekly_compiles_window_stats(storage):
    storage.save_edict(Edict(id="e1", goal="本周事"))
    storage.save_memorial(Memorial(id="m1", edict_id="e1", status=TaskStatus.COMPLETED))
    storage.save_memorial(Memorial(id="m2", edict_id="e1", status=TaskStatus.FAILED))
    storage.save_decree(Decree(memorial_id="m1", action="approve", actor="silijian"))

    report = DigestGenerator(storage).generate_weekly(since_iso="1970-01-01T00:00:00+00:00")
    assert report["type"] == "digest.weekly"
    assert report["title"] == "实录馆·本周实录"
    stats = report["stats"]
    assert stats["edicts"] == 1
    assert stats["completed"] == 1 and stats["failed"] == 1
    assert stats["auto_approvals"] == 1
    assert "本周颁敕 1 道" in report["narrative"]
    assert "司礼监代批 1 笔" in report["narrative"]


def test_weekly_window_excludes_old_rows(storage):
    storage.save_edict(Edict(id="old", goal="上上周"))
    storage.save_memorial(Memorial(id="mo", edict_id="old", status=TaskStatus.COMPLETED))
    # 窗口起点=明天 → 既有行全部落在窗口外
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    stats = DigestGenerator(storage).generate_weekly(since_iso=tomorrow)["stats"]
    assert stats["edicts"] == 0 and stats["memorials_total"] == 0
