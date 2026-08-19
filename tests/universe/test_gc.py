"""位面快照目录 GC 的回收边界测试。"""

from __future__ import annotations

import os
import shutil
import time

import pytest

from tianshu.storage import Storage
from tianshu.universe.gc import UniverseGC
from tianshu.universe.store import UniverseStore

# 合法 ULID（26 字符 Crockford Base32），各用途一个
CHAMPION = "01KTJC9REQ8RN0FEC19QW40FEN"
CHALLENGER = "01KTJCYCAJYBTFNBQYD3HNJAV8"
ARCHIVED_OLD = "01KTJD72SK11WV2GZAHRN8GV09"
ARCHIVED_FRESH = "01KTJDM1XJ6Y0K590XV6SXHK6X"
ORPHAN = "01KTJH01ZYFAK8TSFZQGESAFHS"

_DAY = 86400


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "gc.db"))
    s.init_db()
    yield s
    s.close()


@pytest.fixture
def store(tmp_path):
    return UniverseStore(
        tmp_path / "universes",
        tmp_path / "live_personas",
        tmp_path / "live_skills",
    )


def _make_universe(universe_id: str, status: str) -> dict:
    return {
        "id": universe_id,
        "name": f"u-{status}",
        "status": status,
        "origin": "manual_branch",
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def _seed_dir(store: UniverseStore, universe_id: str, *, age_days: float = 0.0) -> None:
    """建出位面目录，并把 mtime 回拨到 age_days 天前。"""
    d = store.universe_dir(universe_id)
    (d / "skills").mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    if age_days:
        past = time.time() - age_days * _DAY
        os.utime(d, (past, past))


@pytest.fixture
def seeded(storage, store):
    """冠军 + 挑战者 + 两个归档（一旧一新）+ 一个孤儿 + worktrees。"""
    for uid, status in (
        (CHAMPION, "champion"),
        (CHALLENGER, "challenger"),
        (ARCHIVED_OLD, "archived"),
        (ARCHIVED_FRESH, "archived"),
    ):
        storage.save_universe(_make_universe(uid, status))

    _seed_dir(store, CHAMPION, age_days=999)
    _seed_dir(store, CHALLENGER, age_days=999)
    _seed_dir(store, ARCHIVED_OLD, age_days=90)
    _seed_dir(store, ARCHIVED_FRESH, age_days=1)
    _seed_dir(store, ORPHAN, age_days=0)  # 磁盘有、DB 无
    (store.root / "worktrees").mkdir(parents=True, exist_ok=True)
    return store


async def _run_gc(storage, store, *, retention_days: int = 30, bus=None) -> dict:
    gc = UniverseGC(storage, store, retention_days=retention_days, event_bus=bus)
    return await gc.run(trigger_source="test")


@pytest.mark.asyncio
async def test_reclaims_orphan_and_expired_archive(storage, seeded):
    result = await _run_gc(storage, seeded)

    assert set(result["reclaimed"]) == {ORPHAN, ARCHIVED_OLD}
    assert not seeded.universe_dir(ORPHAN).exists()
    assert not seeded.universe_dir(ARCHIVED_OLD).exists()


@pytest.mark.asyncio
async def test_keeps_champion_challenger_and_fresh_archive(storage, seeded):
    """活跃位面无论多老都不回收；未超期的归档也留着。"""
    await _run_gc(storage, seeded)

    assert seeded.universe_dir(CHAMPION).exists()
    assert seeded.universe_dir(CHALLENGER).exists()
    assert seeded.universe_dir(ARCHIVED_FRESH).exists()


@pytest.mark.asyncio
async def test_never_touches_worktrees(storage, seeded):
    """worktrees/ 是 git worktree 根，不是位面目录，必须原样保留。"""
    await _run_gc(storage, seeded)

    assert (seeded.root / "worktrees").is_dir()


@pytest.mark.asyncio
async def test_db_records_survive_disk_reclaim(storage, seeded):
    """只回收磁盘，DB 记录必须留存——证据链不可变。"""
    await _run_gc(storage, seeded)

    assert storage.get_universe(ARCHIVED_OLD) is not None
    assert len(storage.list_universes(include_archived=True)) == 4


@pytest.mark.asyncio
async def test_retention_zero_reclaims_all_archived(storage, seeded):
    result = await _run_gc(storage, seeded, retention_days=0)

    assert set(result["reclaimed"]) == {ORPHAN, ARCHIVED_OLD, ARCHIVED_FRESH}
    assert seeded.universe_dir(CHAMPION).exists()


@pytest.mark.asyncio
async def test_emits_event_with_reclaim_summary(storage, seeded):
    fired = []

    class _Bus:
        def fire(self, event):
            fired.append(event)

    await _run_gc(storage, seeded, bus=_Bus())

    assert len(fired) == 1
    payload = fired[0].payload
    assert set(payload["reclaimed"]) == {ORPHAN, ARCHIVED_OLD}
    assert payload["orphan_count"] == 1
    assert payload["expired_count"] == 1


@pytest.mark.asyncio
async def test_no_event_when_nothing_reclaimed(storage, store):
    fired = []

    class _Bus:
        def fire(self, event):
            fired.append(event)

    result = await _run_gc(storage, store, bus=_Bus())

    assert result["reclaimed"] == []
    assert fired == []


@pytest.mark.asyncio
async def test_missing_root_is_noop(storage, store):
    """根目录不存在（位面功能从未启用）时 GC 不应炸。"""
    shutil.rmtree(store.root)

    result = await _run_gc(storage, store)

    assert result["reclaimed"] == []
    assert result["kept"] == 0
