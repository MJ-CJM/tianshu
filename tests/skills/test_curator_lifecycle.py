"""Tests for the skill curator lifecycle state machine (pure, no LLM)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.skills.curator_lifecycle import apply_automatic_transitions
from tianshu.skills.loader import SkillsLoader
from tianshu.skills.metrics import SkillMetricsStore
from tianshu.storage import Storage

_SKILL = "---\nname: {name}\ndescription: d\n---\n\nbody"


@pytest.fixture
def env(tmp_path: Path):
    db = Storage(str(tmp_path / "t.db"))
    db.init_db()
    ms = SkillMetricsStore(db._conn)
    loader = SkillsLoader(
        builtin_dir=tmp_path / "builtin", user_dir=tmp_path / "user",
    )
    (tmp_path / "builtin").mkdir()
    (tmp_path / "user").mkdir()
    yield loader, ms, db
    db.close()


def _add(loader, ms, name, *, created_by="agent", last_used_days_ago=None, pinned=False):
    loader.create_skill(name, _SKILL.format(name=name))
    ms.ensure_exists(name, created_by=created_by)
    if last_used_days_ago is not None:
        ts = (datetime.now(UTC) - timedelta(days=last_used_days_ago)).isoformat()
        ms._conn.execute(
            "UPDATE skill_metrics SET last_used_at = ? WHERE skill_name = ?",
            (ts, name),
        )
        ms._conn.commit()
    if pinned:
        ms.set_pinned(name, True)


def _live(loader, name) -> bool:
    return any(m["name"] == name for m in loader.list_all_metadata())


def test_archive_stale_reactivate(env):
    loader, ms, _ = env
    _add(loader, ms, "fresh", last_used_days_ago=1)
    _add(loader, ms, "middling", last_used_days_ago=45)
    _add(loader, ms, "ancient", last_used_days_ago=200)

    counts = apply_automatic_transitions(
        ms, loader, stale_after_days=30, archive_after_days=90,
    )

    assert counts["archived"] == 1
    assert counts["marked_stale"] == 1
    assert ms.get("fresh").state == "active"
    assert ms.get("middling").state == "stale"
    assert ms.get("ancient").state == "archived"
    # File for ancient moved out of the live library.
    assert _live(loader, "fresh")
    assert not _live(loader, "ancient")
    assert (loader._user_dir / ".archive" / "ancient" / "SKILL.md").is_file()


def test_reactivate_when_used_again(env):
    loader, ms, _ = env
    _add(loader, ms, "comeback", last_used_days_ago=1)
    ms.set_state("comeback", "stale")  # was stale from a prior cycle
    counts = apply_automatic_transitions(ms, loader, stale_after_days=30, archive_after_days=90)
    assert counts["reactivated"] == 1
    assert ms.get("comeback").state == "active"


def test_pinned_is_exempt(env):
    loader, ms, _ = env
    _add(loader, ms, "keepme", last_used_days_ago=500, pinned=True)
    counts = apply_automatic_transitions(ms, loader, stale_after_days=30, archive_after_days=90)
    assert counts["archived"] == 0
    assert ms.get("keepme").state == "active"
    assert _live(loader, "keepme")


def test_manual_skills_untouched(env):
    loader, ms, _ = env
    _add(loader, ms, "manual-old", created_by="manual", last_used_days_ago=500)
    counts = apply_automatic_transitions(ms, loader, stale_after_days=30, archive_after_days=90)
    assert counts["checked"] == 0  # manual skills are out of scope
    assert _live(loader, "manual-old")
