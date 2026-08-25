"""敕令删除与演进记录的清理边界（v22）。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.storage import EdictArchiveConflict


def test_delete_edict_cleans_legacy_evolution_assignment(storage):
    """candidate_id NULL 的 legacy 分流占位随敕令删除；真实验记录仍受保护。"""
    edict = Edict(goal="cleanup-me")
    memorial = Memorial(edict_id=edict.id, status=TaskStatus.COMPLETED, result="ok")
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    with storage._lock, storage._conn:
        storage._conn.execute(
            "INSERT INTO run_evolution_assignments "
            "(assignment_id, memorial_id, candidate_id, routing_version, bucket, "
            " champion_ref_json, selected_ref_json, overlay_digest, assignment_json, "
            " assignment_hash, created_at) "
            "VALUES (?, ?, NULL, 1, 0, '{}', '{}', ?, '{}', ?, '2026-07-29T00:00:00+00:00')",
            (f"assignment:{memorial.id}", memorial.id, "a" * 64, "b" * 64),
        )
        storage._conn.execute(
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
            ("c" * 64, "2026-08-25T00:00:00+00:00"),
        )
        storage._conn.execute(
            "INSERT INTO run_system_bindings VALUES (?, ?, ?, '[]', ?)",
            (memorial.id, "attempt-1", "c" * 64, "2026-08-25T00:00:00+00:00"),
        )
        storage._conn.execute(
            "INSERT INTO run_generation_bindings VALUES (?, ?, 'bound', '[]', ?)",
            (memorial.id, "attempt-1", "2026-08-25T00:00:00+00:00"),
        )

    storage.delete_edict(edict.id)

    assert storage.get_edict(edict.id) is None
    remaining = storage._conn.execute(
        "SELECT COUNT(*) FROM run_evolution_assignments WHERE memorial_id = ?",
        (memorial.id,),
    ).fetchone()[0]
    assert remaining == 0
    assert (
        storage._conn.execute(
            "SELECT COUNT(*) FROM run_system_bindings WHERE memorial_id = ?",
            (memorial.id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        storage._conn.execute(
            "SELECT COUNT(*) FROM run_generation_bindings WHERE memorial_id = ?",
            (memorial.id,),
        ).fetchone()[0]
        == 0
    )


def test_delete_falls_back_to_archive_when_evidence_retained(storage):
    """含 closed 证据包的敕令：物理删除被拦 → 归档（列表隐藏、证据保留）。"""
    import sqlite3

    import pytest

    edict = Edict(goal="evidence-bound")
    memorial = Memorial(edict_id=edict.id, status=TaskStatus.COMPLETED, result="ok")
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    with storage._lock, storage._conn:
        storage._conn.execute(
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
            ("d" * 64, "2026-08-25T00:00:00+00:00"),
        )
        storage._conn.execute(
            "INSERT INTO run_system_bindings VALUES (?, ?, ?, '[]', ?)",
            (memorial.id, "attempt-1", "d" * 64, "2026-08-25T00:00:00+00:00"),
        )
        storage._conn.execute(
            "INSERT INTO run_generation_bindings VALUES (?, ?, 'bound', '[]', ?)",
            (memorial.id, "attempt-1", "2026-08-25T00:00:00+00:00"),
        )
        storage._conn.execute(
            "INSERT INTO evidence_bundles "
            "(bundle_id, schema_version, edict_id, memorial_id, status, body_json, "
            " content_hash, version, created_at, closed_at) "
            "VALUES (?, '1.0', ?, ?, 'closed', '{}', ?, 1, "
            " '2026-07-29T00:00:00+00:00', '2026-07-29T00:00:00+00:00')",
            (f"bundle-{memorial.id}", edict.id, memorial.id, "c" * 64),
        )

    with pytest.raises(sqlite3.IntegrityError):
        storage.delete_edict(edict.id)

    assert (
        storage._conn.execute(
            "SELECT COUNT(*) FROM run_system_bindings WHERE memorial_id = ?",
            (memorial.id,),
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(
            "SELECT COUNT(*) FROM run_generation_bindings WHERE memorial_id = ?",
            (memorial.id,),
        ).fetchone()[0]
        == 1
    )

    storage.archive_edict(edict.id)
    assert storage.get_edict(edict.id) is not None  # 详情仍可达
    listed, total = storage.list_edicts()
    assert all(e.id != edict.id for e in listed)  # 列表隐藏


def test_tombstone_atomically_cancels_schedules_and_preserves_one_event(storage):
    edict = Edict(goal="scheduled archive")
    storage.save_edict(edict)
    storage.save_scheduler_job(
        "job-active",
        edict.id,
        "once",
        next_run=datetime.now(UTC) + timedelta(hours=1),
    )
    storage.save_scheduler_job(
        "job-paused",
        edict.id,
        "once",
        next_run=datetime.now(UTC) + timedelta(hours=2),
    )
    storage.set_scheduler_job_status("job-paused", "paused")

    assert set(storage.tombstone_edict(edict.id)) == {"job-active", "job-paused"}
    assert storage.tombstone_edict(edict.id) == []

    archived = storage.get_edict(edict.id)
    assert archived is not None
    assert archived.status.value == "cancelled"
    assert archived.runtime.lifecycle_phase == "complete"
    assert archived.metadata["archived_at"]
    assert storage.get_scheduler_job("job-active")["status"] == "cancelled"
    assert storage.get_scheduler_job("job-active")["next_run"] is None
    assert storage.get_scheduler_job("job-paused")["status"] == "cancelled"
    assert (
        storage._conn.execute(
            "SELECT COUNT(*) FROM events WHERE edict_id = ? AND event_type = 'edict.archived'",
            (edict.id,),
        ).fetchone()[0]
        == 1
    )


def test_tombstone_rechecks_unfinished_work_inside_transaction(storage):
    edict = Edict(goal="still auditing")
    storage.save_edict(edict)
    storage.save_memorial(
        Memorial(
            edict_id=edict.id,
            status=TaskStatus.AUDITING,
            instruction="reviewing",
        )
    )

    with pytest.raises(EdictArchiveConflict):
        storage.tombstone_edict(edict.id)

    current = storage.get_edict(edict.id)
    assert current is not None
    assert current.status.value == "open"
    assert "archived_at" not in current.metadata


def test_tombstone_rolls_back_every_projection_when_event_write_fails(storage):
    edict = Edict(goal="atomic archive")
    storage.save_edict(edict)
    storage.save_scheduler_job(
        "job-1",
        edict.id,
        "once",
        next_run=datetime.now(UTC) + timedelta(hours=1),
    )
    with storage._conn:
        storage._conn.execute(
            """
            CREATE TRIGGER reject_archive_event
            BEFORE INSERT ON events
            WHEN NEW.event_type = 'edict.archived'
            BEGIN
                SELECT RAISE(ABORT, 'injected archive event failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected archive event failure"):
        storage.tombstone_edict(edict.id)

    current = storage.get_edict(edict.id)
    assert current is not None
    assert current.status.value == "open"
    assert current.runtime.lifecycle_phase == "active"
    assert "archived_at" not in current.metadata
    assert storage.get_scheduler_job("job-1")["status"] == "active"
