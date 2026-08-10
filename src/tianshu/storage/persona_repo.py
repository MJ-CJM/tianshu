"""Storage Persona 领域 Mixin —— 人格/部门 CRUD、画像综合锁与心跳计数、部门播种。"""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

from tianshu.storage.mappers import _row_to_persona_dict


class PersonaMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Department CRUD ---

    def save_department(self, dept: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO departments (id, name, description, created_at) VALUES (?, ?, ?, ?)",
                (
                    dept["id"],
                    dept["name"],
                    dept.get("description", ""),
                    dept.get("created_at", now),
                ),
            )

    def list_departments(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM departments ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_department(self, dept_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM departments WHERE id = ?", (dept_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_department(self, dept_id: str, **fields) -> None:
        allowed = {"name", "description"}
        sets: list[str] = []
        params: list = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        params.append(dept_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE departments SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def delete_department(self, dept_id: str) -> bool:
        """Delete department. Refuses if any persona references it."""
        with self._lock:
            ref_count = self._conn.execute(
                "SELECT COUNT(*) FROM personas WHERE department = ?", (dept_id,)
            ).fetchone()[0]
            if ref_count > 0:
                return False
            with self._conn:
                cursor = self._conn.execute("DELETE FROM departments WHERE id = ?", (dept_id,))
                return cursor.rowcount > 0

    # --- Persona CRUD ---

    def save_persona(self, persona: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO personas
                   (id, name, department, title, tools_allowed, tools_denied,
                    skills_allowed, tool_tier_max, can_delegate, memory_global_read, delegates_to,
                    soul_path, role_path, llm_config_name, allowed_paths, workspace_dir, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    persona["id"],
                    persona["name"],
                    persona["department"],
                    persona.get("title"),
                    json.dumps(persona.get("tools_allowed", [])),
                    json.dumps(persona.get("tools_denied", [])),
                    json.dumps(persona.get("skills_allowed", [])),
                    persona.get("tool_tier_max", 0),
                    int(persona.get("can_delegate", False)),
                    int(persona.get("memory_global_read", False)),
                    json.dumps(persona.get("delegates_to", [])),
                    persona.get("soul_path"),
                    persona.get("role_path"),
                    persona.get("llm_config_name"),
                    json.dumps(persona.get("allowed_paths", [])),
                    persona.get("workspace_dir", ""),
                    persona.get("created_at", now),
                    now,
                ),
            )

    def list_personas(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM personas ORDER BY department, name").fetchall()
        return [_row_to_persona_dict(r) for r in rows]

    def get_persona(self, persona_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM personas WHERE id = ?", (persona_id,)
            ).fetchone()
        return _row_to_persona_dict(row) if row else None

    def update_persona(self, persona_id: str, **fields) -> None:
        allowed = {
            "name",
            "department",
            "title",
            "tools_allowed",
            "tools_denied",
            "allowed_paths",
            "workspace_dir",
            "skills_allowed",
            "tool_tier_max",
            "can_delegate",
            "memory_global_read",
            "delegates_to",
            "soul_path",
            "role_path",
            "llm_config_name",
        }
        sets: list[str] = []
        params: list = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in (
                "tools_allowed",
                "tools_denied",
                "allowed_paths",
                "skills_allowed",
                "delegates_to",
            ):
                sets.append(f"{key} = ?")
                params.append(json.dumps(value))
            elif key in ("can_delegate", "memory_global_read"):
                sets.append(f"{key} = ?")
                params.append(int(value))
            else:
                sets.append(f"{key} = ?")
                params.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(datetime.now(UTC).isoformat())
        params.append(persona_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE personas SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def delete_persona(self, persona_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))
            return cursor.rowcount > 0

    # --- Persona Metrics / Profile Synthesis ---

    def try_acquire_synthesis_lock(self, persona_id: str, stale_timeout_sec: int = 600) -> bool:
        """Return True if lock acquired. Reclaims stale locks > stale_timeout_sec."""
        now_iso = datetime.now(UTC).isoformat()
        stale_cutoff = (datetime.now(UTC) - timedelta(seconds=stale_timeout_sec)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE persona_metrics
                SET synthesis_in_progress=1, synthesis_started_at=?
                WHERE persona_id=?
                  AND (synthesis_in_progress=0
                       OR synthesis_started_at < ?)
                """,
                (now_iso, persona_id, stale_cutoff),
            )
            if cur.rowcount > 0:
                self._conn.commit()
                return True
            # ensure row exists
            self._conn.execute(
                "INSERT OR IGNORE INTO persona_metrics(persona_id) VALUES (?)",
                (persona_id,),
            )
            self._conn.commit()
            # retry once
            cur = self._conn.execute(
                """
                UPDATE persona_metrics
                SET synthesis_in_progress=1, synthesis_started_at=?
                WHERE persona_id=?
                  AND (synthesis_in_progress=0
                       OR synthesis_started_at < ?)
                """,
                (now_iso, persona_id, stale_cutoff),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def release_synthesis_lock(self, persona_id: str) -> None:
        """Release lock and reset throttle counter after synthesis cycle (success or degraded)."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE persona_metrics
                SET synthesis_in_progress=0, synthesis_started_at=NULL,
                    tasks_since_last_synthesis=0
                WHERE persona_id=?
                """,
                (persona_id,),
            )
            self._conn.commit()

    def increment_persona_task_counter(self, persona_id: str) -> int:
        """Increment tasks_since_last_synthesis by 1, create row if missing; return new value."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO persona_metrics(persona_id) VALUES (?)",
                (persona_id,),
            )
            self._conn.execute(
                """
                UPDATE persona_metrics
                SET tasks_since_last_synthesis = tasks_since_last_synthesis + 1
                WHERE persona_id=?
                """,
                (persona_id,),
            )
            cur = self._conn.execute(
                "SELECT tasks_since_last_synthesis FROM persona_metrics WHERE persona_id=?",
                (persona_id,),
            )
            row = cur.fetchone()
            self._conn.commit()
            return int(row[0]) if row else 0

    def _seed_departments(self) -> None:
        """Populate departments table from existing personas if empty."""
        count = self._conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if count > 0:
            return

        KNOWN = {
            "bingbu": "兵部 (Ministry of War)",
            "neige": "内阁 (Imperial Cabinet)",
            "ducha": "都察院 (Censorate)",
            "tongzheng": "通政司 (Bureau of Coordination)",
            "wenyuan": "文渊阁 (Grand Secretariat)",
            "hubu": "户部 (Ministry of Revenue)",
        }

        now = datetime.now(UTC).isoformat()
        # Collect distinct departments from personas
        rows = self._conn.execute("SELECT DISTINCT department FROM personas").fetchall()
        dept_ids = {r[0] for r in rows}
        # Merge with known defaults
        dept_ids.update(KNOWN.keys())

        with self._conn:
            for dept_id in dept_ids:
                name = KNOWN.get(dept_id, dept_id)
                self._conn.execute(
                    "INSERT OR IGNORE INTO departments (id, name, description, created_at) VALUES (?, ?, '', ?)",
                    (dept_id, name, now),
                )
