"""archive_old_iterations 测试 —— 30 天归档逻辑。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tianshu.executor.orchestrator.archive import archive_old_iterations
from tianshu.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    if hasattr(s, "init_db"):
        s.init_db()
    return s


def _save_iter(s, id_: str, edict_id: str, iteration: int, finished_at: str):
    s.save_outer_loop_iteration({
        "id": id_, "edict_id": edict_id, "iteration": iteration, "level": "L0",
        "actor_output": f"data-{id_}", "checks_result": "{}", "critic_result": None,
        "cost_cny": 0.1, "started_at": finished_at, "finished_at": finished_at,
    })


@pytest.mark.unit
def test_archive_empty_table(storage):
    assert archive_old_iterations(storage, retention_days=30) == 0


@pytest.mark.unit
def test_archive_old_only(storage):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    _save_iter(storage, "old1", "e1", 0, old_ts)
    _save_iter(storage, "new1", "e1", 1, new_ts)

    n = archive_old_iterations(storage, retention_days=30)
    assert n == 1

    rows = storage.get_outer_loop_iterations("e1")
    old_row = next(r for r in rows if r["id"] == "old1")
    new_row = next(r for r in rows if r["id"] == "new1")
    assert old_row["actor_output"] is None
    assert old_row["archived_at"] is not None
    assert new_row["actor_output"] == "data-new1"
    assert new_row["archived_at"] is None


@pytest.mark.unit
def test_archive_idempotent(storage):
    """归档过的不再处理 —— 第二次跑返回 0。"""
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    _save_iter(storage, "old1", "e1", 0, old_ts)

    n1 = archive_old_iterations(storage, retention_days=30)
    assert n1 == 1

    n2 = archive_old_iterations(storage, retention_days=30)
    assert n2 == 0


@pytest.mark.unit
def test_archive_custom_retention(storage):
    """retention_days 可调，比如 7 天。"""
    ts_3d = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    ts_10d = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    _save_iter(storage, "i3", "e1", 0, ts_3d)
    _save_iter(storage, "i10", "e1", 1, ts_10d)

    # 7 天保留 → 10 天的归档，3 天的不动
    n = archive_old_iterations(storage, retention_days=7)
    assert n == 1

    rows = storage.get_outer_loop_iterations("e1")
    by_id = {r["id"]: r for r in rows}
    assert by_id["i10"]["archived_at"] is not None
    assert by_id["i3"]["archived_at"] is None
