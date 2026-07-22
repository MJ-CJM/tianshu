"""存量 DB 会话表 instance_id 迁移测试。

手搓一个迁移前 schema（anchor / pending / seen 表均无 instance_id），
塞几行数据，再用 Storage 打开并 init_db()（触发 _migrate）。验证：
- anchor 表 PK 变为 (instance_id, chat_id)，存量行 instance_id 回填 <channel>-default，
  current_edict_id 保留。
- pending / seen 表新增 instance_id 列，存量行回填 <channel>-default。
- 幂等：再 init_db() 不报错、数据不变。
"""

from __future__ import annotations

import sqlite3

from tianshu.storage import Storage
from tianshu.storage.migrations import MIGRATIONS


def _build_old_db(path: str) -> None:
    """手搓迁移前 schema + 数据。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE telegram_session_anchor (
            chat_id          TEXT PRIMARY KEY,
            current_edict_id TEXT,
            updated_at       TIMESTAMP NOT NULL
        );
        CREATE TABLE feishu_session_anchor (
            chat_id          TEXT PRIMARY KEY,
            current_edict_id TEXT,
            updated_at       TIMESTAMP NOT NULL
        );
        CREATE TABLE telegram_pending_buttons (
            approval_id TEXT PRIMARY KEY,
            chat_id     TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            kind        TEXT NOT NULL,
            created_at  TIMESTAMP NOT NULL
        );
        CREATE TABLE feishu_pending_cards (
            approval_id TEXT PRIMARY KEY,
            chat_id     TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            kind        TEXT NOT NULL,
            created_at  TIMESTAMP NOT NULL
        );
        CREATE TABLE telegram_seen_messages (
            update_id TEXT PRIMARY KEY,
            seen_at   TIMESTAMP NOT NULL
        );
        CREATE TABLE feishu_seen_messages (
            message_id TEXT PRIMARY KEY,
            seen_at    TIMESTAMP NOT NULL
        );

        INSERT INTO telegram_session_anchor (chat_id, current_edict_id, updated_at)
            VALUES ('123', 'ed_tg', '2026-01-01T00:00:00+00:00');
        INSERT INTO feishu_session_anchor (chat_id, current_edict_id, updated_at)
            VALUES ('oc_x', 'ed_fs', '2026-01-01T00:00:00+00:00');
        INSERT INTO telegram_pending_buttons
            (approval_id, chat_id, message_id, kind, created_at)
            VALUES ('ap1', '123', '9', 'approval', '2026-01-01T00:00:00+00:00');
        INSERT INTO feishu_pending_cards
            (approval_id, chat_id, message_id, kind, created_at)
            VALUES ('ap2', 'oc_x', '8', 'approval', '2026-01-01T00:00:00+00:00');
        INSERT INTO telegram_seen_messages (update_id, seen_at)
            VALUES ('u1', '2026-01-01T00:00:00+00:00');
        INSERT INTO feishu_seen_messages (message_id, seen_at)
            VALUES ('m1', '2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()


def _columns(conn, table: str) -> dict[str, dict]:
    """name -> {pk: int, type: str}。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # row: (cid, name, type, notnull, dflt_value, pk)
    return {r[1]: {"pk": r[5], "type": r[2]} for r in rows}


def test_migration_upgrades_old_session_tables(tmp_path):
    db = str(tmp_path / "old.db")
    _build_old_db(db)

    s = Storage(db)
    s.init_db()  # 触发 _migrate

    conn = s._conn

    # --- anchor 表：复合 PK (instance_id, chat_id) ---
    for channel in ("telegram", "feishu"):
        cols = _columns(conn, f"{channel}_session_anchor")
        assert "instance_id" in cols
        # 复合 PK：instance_id 与 chat_id 都参与（pk 序号 > 0）
        assert cols["instance_id"]["pk"] > 0
        assert cols["chat_id"]["pk"] > 0

    # 存量行回填 <channel>-default，current_edict_id 保留
    row = conn.execute(
        "SELECT instance_id, current_edict_id FROM telegram_session_anchor WHERE chat_id = '123'"
    ).fetchone()
    assert row[0] == "telegram-default"
    assert row[1] == "ed_tg"

    # 会话兼容升级与完整 schema 在同一 baseline 中落账，不再在 ledger 外运行。
    assert [
        tuple(item)
        for item in conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    ] == [(migration.version, migration.name) for migration in MIGRATIONS]
    assert (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='edicts'").fetchone()
        is not None
    )

    row = conn.execute(
        "SELECT instance_id, current_edict_id FROM feishu_session_anchor WHERE chat_id = 'oc_x'"
    ).fetchone()
    assert row[0] == "feishu-default"
    assert row[1] == "ed_fs"

    # --- pending / seen 表：新增 instance_id 列，回填 default ---
    assert "instance_id" in _columns(conn, "telegram_pending_buttons")
    assert "instance_id" in _columns(conn, "feishu_pending_cards")
    assert "instance_id" in _columns(conn, "telegram_seen_messages")
    assert "instance_id" in _columns(conn, "feishu_seen_messages")

    assert (
        conn.execute(
            "SELECT instance_id FROM telegram_pending_buttons WHERE approval_id='ap1'"
        ).fetchone()[0]
        == "telegram-default"
    )
    assert (
        conn.execute(
            "SELECT instance_id FROM feishu_pending_cards WHERE approval_id='ap2'"
        ).fetchone()[0]
        == "feishu-default"
    )
    assert (
        conn.execute(
            "SELECT instance_id FROM telegram_seen_messages WHERE update_id='u1'"
        ).fetchone()[0]
        == "telegram-default"
    )
    assert (
        conn.execute(
            "SELECT instance_id FROM feishu_seen_messages WHERE message_id='m1'"
        ).fetchone()[0]
        == "feishu-default"
    )

    s.close()


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "old.db")
    _build_old_db(db)

    s = Storage(db)
    s.init_db()
    s.close()

    # 再开一次（已迁移）→ 不报错，数据不变
    s2 = Storage(db)
    s2.init_db()
    conn = s2._conn

    row = conn.execute(
        "SELECT instance_id, current_edict_id FROM telegram_session_anchor WHERE chat_id = '123'"
    ).fetchone()
    assert row[0] == "telegram-default"
    assert row[1] == "ed_tg"

    # 仍只有 1 行（没有重复回填）
    cnt = conn.execute("SELECT COUNT(*) FROM telegram_session_anchor").fetchone()[0]
    assert cnt == 1

    s2.close()
