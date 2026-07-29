"""敕令删除与演进记录的清理边界（v22）。"""

from __future__ import annotations

from tianshu.models import Edict, Memorial, TaskStatus


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

    storage.delete_edict(edict.id)

    assert storage.get_edict(edict.id) is None
    remaining = storage._conn.execute(
        "SELECT COUNT(*) FROM run_evolution_assignments WHERE memorial_id = ?",
        (memorial.id,),
    ).fetchone()[0]
    assert remaining == 0


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
            "INSERT INTO evidence_bundles "
            "(bundle_id, schema_version, edict_id, memorial_id, status, body_json, "
            " content_hash, version, created_at, closed_at) "
            "VALUES (?, '1.0', ?, ?, 'closed', '{}', ?, 1, "
            " '2026-07-29T00:00:00+00:00', '2026-07-29T00:00:00+00:00')",
            (f"bundle-{memorial.id}", edict.id, memorial.id, "c" * 64),
        )

    with pytest.raises(sqlite3.IntegrityError):
        storage.delete_edict(edict.id)

    storage.archive_edict(edict.id)
    assert storage.get_edict(edict.id) is not None  # 详情仍可达
    listed, total = storage.list_edicts()
    assert all(e.id != edict.id for e in listed)  # 列表隐藏
