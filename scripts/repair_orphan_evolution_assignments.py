"""一次性修复：删除 run_evolution_assignments 中引用已删 memorials 的孤儿行。

背景：库中存在 7 条孤儿分流记录（parent memorial 已不存在），导致迁移账本的
foreign_key_check 拦下所有后续迁移（首个触发者是 0019_model_providers）。
该表带不可变触发器，需临时摘除后原样重建；全程单事务，任一步失败即回滚。

用法（先停服务）：
    .venv/bin/python scripts/repair_orphan_evolution_assignments.py ~/.tianshu/tianshu.db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

TRIGGER = "run_evolution_assignments_no_delete"


def main() -> None:
    db_path = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path(
        "~/.tianshu/tianshu.db"
    ).expanduser()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    orphans = conn.execute(
        "SELECT COUNT(*) FROM run_evolution_assignments "
        "WHERE memorial_id NOT IN (SELECT id FROM memorials)"
    ).fetchone()[0]
    print(f"孤儿行: {orphans}")
    if not orphans:
        print("无需修复")
        return

    trigger_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (TRIGGER,)
    ).fetchone()[0]

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"DROP TRIGGER {TRIGGER}")
        cur = conn.execute(
            "DELETE FROM run_evolution_assignments "
            "WHERE memorial_id NOT IN (SELECT id FROM memorials)"
        )
        print(f"已删除: {cur.rowcount}")
        conn.execute(trigger_sql)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall(), "FK 校验未通过"
        quick = conn.execute("PRAGMA quick_check").fetchall()
        assert quick and all(str(r[0]).lower() == "ok" for r in quick), quick
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    print("修复完成，触发器已原样重建")


if __name__ == "__main__":
    main()
