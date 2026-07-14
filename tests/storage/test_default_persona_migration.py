"""V6 `0006_seed_default_personas` 数据迁移行为。

契约（G1.5 Slice A / recon §5）：仅当 personas 表为空时 seed 恰好六个内建
部门；幂等；绝不覆盖/复制/复活用户行；不持久化 packaged 绝对路径。
"""

import sqlite3
from pathlib import Path

from tianshu.storage import Storage
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS

_SIX_IDS = {"bingbu", "ducha", "hubu", "neige", "tongzheng", "wenyuan"}


def _personas_rows(db: Path) -> dict[str, dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM personas")}
    conn.close()
    return rows


def test_migration_v6_is_frozen_seed_default_personas() -> None:
    assert (MIGRATIONS[5].version, MIGRATIONS[5].name) == (
        6,
        "0006_seed_default_personas",
    )


def test_fresh_db_seeds_exactly_six_departments(tmp_path: Path) -> None:
    db = tmp_path / "fresh.sqlite3"
    storage = Storage(str(db))
    storage.init_db()
    storage.close()

    rows = _personas_rows(db)
    assert set(rows) == _SIX_IDS
    for pid, row in rows.items():
        assert row["department"] == pid
        assert row["tools_allowed"] == "[]"
        assert row["tools_denied"] == "[]"
        assert row["skills_allowed"] == "[]"
        assert row["memory_global_read"] == 0
        # packaged 绝对路径不得入库
        assert row["soul_path"] is None
        assert row["role_path"] is None
    assert rows["bingbu"]["tool_tier_max"] == 2
    assert rows["neige"]["tool_tier_max"] == 1
    assert rows["neige"]["can_delegate"] == 1
    assert rows["neige"]["delegates_to"] == '["bingbu", "ducha", "wenyuan"]'
    for other in _SIX_IDS - {"bingbu", "neige"}:
        assert rows[other]["tool_tier_max"] == 1
        assert rows[other]["can_delegate"] == 0
        assert rows[other]["delegates_to"] == "[]"


def test_nonempty_personas_table_is_left_untouched(tmp_path: Path) -> None:
    db = tmp_path / "custom.sqlite3"
    conn = sqlite3.connect(db)
    # 构造 v1..v5 完成、personas 含一个用户自定义行的库
    apply_migrations(conn, MIGRATIONS[:5])
    conn.execute(
        "INSERT INTO personas (id, name, department, created_at, updated_at)"
        " VALUES ('my-agent', '自定义官员', 'custom', '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    storage = Storage(str(db))
    storage.init_db()
    storage.close()

    rows = _personas_rows(db)
    assert set(rows) == {"my-agent"}, "非空 personas 表不得注入默认部门"


def test_reapply_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "idem.sqlite3"
    storage = Storage(str(db))
    storage.init_db()
    storage.close()
    before = _personas_rows(db)

    reopened = Storage(str(db))
    reopened.init_db()
    reopened.close()
    assert _personas_rows(db) == before


def test_deleted_seeded_persona_is_not_resurrected(tmp_path: Path) -> None:
    db = tmp_path / "deleted.sqlite3"
    storage = Storage(str(db))
    storage.init_db()
    storage.delete_persona("hubu")
    storage.close()

    reopened = Storage(str(db))
    reopened.init_db()
    reopened.close()

    rows = _personas_rows(db)
    assert "hubu" not in rows, "v6 已入 ledger，用户删除的默认部门不得复活"
    assert set(rows) == _SIX_IDS - {"hubu"}
