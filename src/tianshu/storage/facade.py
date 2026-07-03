"""SQLite storage layer - system truth source."""

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from tianshu.storage._base import _StorageBase
from tianshu.storage.cost_repo import CostMixin
from tianshu.storage.dag_repo import DagMixin
from tianshu.storage.edict_repo import EdictMixin
from tianshu.storage.event_repo import EventMixin
from tianshu.storage.mappers import (
    _row_to_eval_run,
    _row_to_persona_dict,
    _row_to_universe,
)
from tianshu.storage.memorial_repo import MemorialMixin
from tianshu.storage.memory_repo import MemoryMixin
from tianshu.storage.scheduler_repo import SchedulerMixin

logger = logging.getLogger(__name__)


class Storage(
    _StorageBase,
    EdictMixin,
    MemorialMixin,
    EventMixin,
    MemoryMixin,
    CostMixin,
    DagMixin,
    SchedulerMixin,
):
    # 第二波域的方法暂留在此类体内（persona/universe/config/credential/channel/feishu/telegram/orchestrator 等）

    # --- LLM Configs ---

    def save_llm_config(self, config: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO llm_configs
                   (name, model, api_key, api_base, max_retries, temperature,
                    top_p, max_tokens, enabled, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    config["name"],
                    config["model"],
                    config["api_key"],
                    config.get("api_base", ""),
                    config.get("max_retries", 3),
                    config.get("temperature", 0.7),
                    config.get("top_p", 1.0),
                    config.get("max_tokens", 4096),
                    1 if config.get("enabled", True) else 0,
                    1 if config.get("is_active", False) else 0,
                    config.get("created_at", datetime.now(UTC).isoformat()),
                ),
            )

    def list_llm_configs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM llm_configs ORDER BY is_active DESC, name ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_llm_config(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM llm_configs WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def delete_llm_config(self, name: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM llm_configs WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def set_active_llm_config(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE llm_configs SET is_active = 0")
            self._conn.execute("UPDATE llm_configs SET is_active = 1 WHERE name = ?", (name,))

    # --- Providers ---

    def save_provider(self, provider: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO providers
                   (name, model, api_base, capabilities_json, rpm_limit, tpm_limit,
                    rpm_current, tpm_current, rpm_window_start, status, priority,
                    cost_per_1k_prompt, cost_per_1k_completion, cost_per_1k_cache_read,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider["name"],
                    provider["model"],
                    provider.get("api_base"),
                    json.dumps(provider.get("capabilities", [])),
                    provider.get("rpm_limit"),
                    provider.get("tpm_limit"),
                    provider.get("rpm_current", 0),
                    provider.get("tpm_current", 0),
                    provider.get("rpm_window_start"),
                    provider.get("status", "active"),
                    provider.get("priority", 100),
                    provider.get("cost_per_1k_prompt"),
                    provider.get("cost_per_1k_completion"),
                    provider.get("cost_per_1k_cache_read"),
                    provider.get("created_at", datetime.now(UTC).isoformat()),
                ),
            )

    def get_provider(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM providers WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["capabilities"] = json.loads(d.pop("capabilities_json", "[]"))
        return d

    def list_providers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM providers ORDER BY priority ASC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["capabilities"] = json.loads(d.pop("capabilities_json", "[]"))
            result.append(d)
        return result

    def delete_provider(self, name: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM providers WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def update_provider(self, name: str, updates: dict) -> None:
        sets: list[str] = []
        params: list = []
        for key, value in updates.items():
            if key == "capabilities":
                sets.append("capabilities_json = ?")
                params.append(json.dumps(value))
            elif key in (
                "model",
                "api_base",
                "status",
                "rpm_limit",
                "tpm_limit",
                "priority",
                "cost_per_1k_prompt",
                "cost_per_1k_completion",
                "cost_per_1k_cache_read",
            ):
                sets.append(f"{key} = ?")
                params.append(value)
        if not sets:
            return
        params.append(name)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE providers SET {', '.join(sets)} WHERE name = ?", params)

    # --- Plugins ---

    def save_plugin(self, plugin: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO plugins
                   (name, version, manifest_json, status, sha256, installed_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    plugin["name"],
                    plugin.get("version", "0.0.0"),
                    json.dumps(plugin.get("manifest", {})),
                    plugin.get("status", "active"),
                    plugin.get("sha256"),
                    plugin.get("installed_at", now),
                    now,
                ),
            )

    def list_plugins(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM plugins ORDER BY name ASC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["manifest"] = json.loads(d.pop("manifest_json", "{}"))
            result.append(d)
        return result

    def get_plugin(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM plugins WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["manifest"] = json.loads(d.pop("manifest_json", "{}"))
        return d

    def update_plugin_status(self, name: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE plugins SET status = ?, updated_at = ? WHERE name = ?",
                (status, datetime.now(UTC).isoformat(), name),
            )

    # --- Persona Stats (Phase 3.12) ---

    def get_persona_stats(self, persona_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) as total_executions,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                    COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled,
                    COALESCE(SUM(json_extract(usage_json, '$.total_tokens')), 0) as total_tokens,
                    COALESCE(AVG(
                        CASE WHEN completed_at IS NOT NULL AND started_at IS NOT NULL
                        THEN (julianday(completed_at) - julianday(started_at)) * 86400
                        END
                    ), 0.0) as avg_duration_seconds
                FROM memorials
                WHERE persona_id = ?
            """,
                (persona_id,),
            ).fetchone()

        total = row["total_executions"] or 0
        completed = row["completed"] or 0
        success_rate = (completed / total * 100) if total > 0 else 0.0
        total_tokens = row["total_tokens"] or 0
        avg_tokens = (total_tokens / total) if total > 0 else 0.0

        # Cost from cost_ledger (join via memorial_id)
        cost_row = self._conn.execute(
            """
            SELECT COALESCE(SUM(cl.cost_cny), 0.0) as total_cost
            FROM cost_ledger cl
            JOIN memorials m ON cl.memorial_id = m.id
            WHERE m.persona_id = ?
        """,
            (persona_id,),
        ).fetchone()

        return {
            "total_executions": total,
            "completed": completed,
            "failed": row["failed"] or 0,
            "cancelled": row["cancelled"] or 0,
            "success_rate": round(success_rate, 2),
            "total_tokens": total_tokens,
            "avg_tokens_per_execution": round(avg_tokens, 1),
            "total_cost_cny": round(cost_row["total_cost"], 6) if cost_row else 0.0,
            "avg_duration_seconds": round(row["avg_duration_seconds"] or 0.0, 2),
        }

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
                    soul_path, role_path, llm_config_name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            if key in ("tools_allowed", "tools_denied", "skills_allowed", "delegates_to"):
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

    def last_activity_at(self) -> str | None:
        """Most recent event timestamp (ISO) for idle gating; None if no events.

        Execution events carry an edict_id and are persisted, so MAX(created_at)
        across the events table approximates the last real agent activity.
        """
        with self._lock:
            row = self._conn.execute("SELECT MAX(created_at) AS ts FROM events").fetchone()
        return row["ts"] if row and row["ts"] else None

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

    # --- Universes (平行位面) ---

    def save_universe(self, uni: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO universes
                   (id, name, parent_universe_id, status, origin,
                    mutation_reason, description, fitness_json, code_ref, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uni["id"],
                    uni["name"],
                    uni.get("parent_universe_id"),
                    uni["status"],
                    uni["origin"],
                    uni.get("mutation_reason"),
                    uni.get("description", ""),
                    json.dumps(uni.get("fitness", {}), ensure_ascii=False),
                    uni.get("code_ref"),
                    uni["created_at"],
                ),
            )

    def get_universe(self, universe_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM universes WHERE id = ?", (universe_id,)
            ).fetchone()
        return _row_to_universe(row) if row else None

    def list_universes(self, *, include_archived: bool = True) -> list[dict]:
        with self._lock:
            sql = "SELECT * FROM universes"
            if not include_archived:
                sql += " WHERE status != 'archived'"
            sql += " ORDER BY created_at DESC"
            return [_row_to_universe(r) for r in self._conn.execute(sql).fetchall()]

    def get_champion_universe(self) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM universes WHERE status = 'champion'").fetchone()
        return _row_to_universe(row) if row else None

    def set_universe_status(self, universe_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE universes SET status = ? WHERE id = ?", (status, universe_id)
            )

    def update_universe_fitness(self, universe_id: str, fitness: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE universes SET fitness_json = ? WHERE id = ?",
                (json.dumps(fitness, ensure_ascii=False), universe_id),
            )

    def delete_universe(self, universe_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM variant_eval_runs WHERE universe_id = ?", (universe_id,)
            )
            self._conn.execute("DELETE FROM universes WHERE id = ?", (universe_id,))

    def save_variant_eval_run(self, run: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO variant_eval_runs
                   (id, universe_id, gate_passed, gate_detail,
                    fitness_json, eval_set_version, cost, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run["id"],
                    run["universe_id"],
                    1 if run.get("gate_passed") else 0,
                    json.dumps(run.get("gate_detail"), ensure_ascii=False)
                    if run.get("gate_detail") is not None
                    else None,
                    json.dumps(run.get("fitness", {}), ensure_ascii=False),
                    run.get("eval_set_version"),
                    float(run.get("cost", 0.0)),
                    run["created_at"],
                ),
            )

    def list_variant_eval_runs(self, universe_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM variant_eval_runs WHERE universe_id = ? ORDER BY created_at DESC",
                (universe_id,),
            ).fetchall()
        return [_row_to_eval_run(r) for r in rows]

    # --- Memorials by Persona ---

    def list_memorials_by_persona(
        self,
        persona_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return memorials grouped by edict for a persona.

        For 'bingbu' (default executor), also includes memorials with NULL persona_id
        to cover legacy data created before persona_id tracking was added.
        """
        if persona_id == "bingbu":
            join_where = "(m.persona_id = ? OR m.persona_id IS NULL)"
            count_where = "(persona_id = ? OR persona_id IS NULL)"
        else:
            join_where = "m.persona_id = ?"
            count_where = "persona_id = ?"
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT m.*, e.title as edict_title, e.goal as edict_goal, e.status as edict_status
                   FROM memorials m
                   JOIN edicts e ON m.edict_id = e.id
                   WHERE {join_where}
                   ORDER BY m.created_at DESC
                   LIMIT ? OFFSET ?""",
                (persona_id, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM memorials WHERE {count_where}",
                (persona_id,),
            ).fetchone()[0]

        # Group by edict_id
        edicts_map: dict[str, dict] = {}
        for r in rows:
            eid = r["edict_id"]
            if eid not in edicts_map:
                edicts_map[eid] = {
                    "edict_id": eid,
                    "edict_title": r["edict_title"],
                    "edict_goal": r["edict_goal"],
                    "edict_status": r["edict_status"],
                    "memorials": [],
                }
            edicts_map[eid]["memorials"].append(
                {
                    "id": r["id"],
                    "instruction": r["instruction"],
                    "status": r["status"],
                    "result": r["result"],
                    "summary": r["summary"],
                    "error": r["error"],
                    "created_at": r["created_at"],
                    "started_at": r["started_at"],
                    "completed_at": r["completed_at"],
                }
            )

        return list(edicts_map.values()), total

    # --- Helpers ---

    def insert_credential(
        self,
        *,
        cred_id: str,
        name: str,
        host_pattern: str,
        header_template: str,
        extra_headers_json: str,
        encrypted_value: bytes,
        now_iso: str,
        kind: str = "edict_auth",
        provider_name: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO network_credentials
                   (id, name, host_pattern, header_template, extra_headers,
                    encrypted_value, created_at, updated_at, kind, provider_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cred_id,
                    name,
                    host_pattern,
                    header_template,
                    extra_headers_json,
                    encrypted_value,
                    now_iso,
                    now_iso,
                    kind,
                    provider_name,
                ),
            )

    def list_credentials(self, kind: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            if kind:
                cur = self._conn.execute(
                    "SELECT * FROM network_credentials "
                    "WHERE deleted_at IS NULL AND kind=? "
                    "ORDER BY name",
                    (kind,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM network_credentials WHERE deleted_at IS NULL ORDER BY name"
                )
            return cur.fetchall()

    def get_credential_by_id(self, cred_id: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM network_credentials WHERE id=? AND deleted_at IS NULL",
                (cred_id,),
            )
            return cur.fetchone()

    def find_credentials_by_host(self, host: str) -> list[sqlite3.Row]:
        """返回所有可能匹配此 host 的 edict_auth 凭证（literal + 通配）。
        强制 kind='edict_auth' 过滤 — engine_provider key 永不参与 host 匹配，
        从根源隔离 LLM 可访问面。enabled=0 视为未配置，跳过。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM network_credentials "
                "WHERE deleted_at IS NULL AND kind='edict_auth' "
                "AND enabled = 1 "
                "AND (host_pattern=? OR host_pattern LIKE '*.%')",
                (host,),
            )
            return cur.fetchall()

    def find_credentials_by_provider(self, provider_name: str) -> sqlite3.Row | None:
        """disabled 的 provider 凭证视为未配置，返回 None 让 resolve 回落 env。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM network_credentials "
                "WHERE deleted_at IS NULL AND kind='engine_provider' "
                "AND enabled = 1 "
                "AND provider_name=?",
                (provider_name,),
            )
            return cur.fetchone()

    def update_credential(
        self,
        cred_id: str,
        *,
        encrypted_value: bytes | None = None,
        extra_headers_json: str | None = None,
        enabled: bool | None = None,
        now_iso: str,
    ) -> None:
        sets = ["updated_at=?"]
        params: list[object] = [now_iso]
        if encrypted_value is not None:
            sets.append("encrypted_value=?")
            params.append(encrypted_value)
        if extra_headers_json is not None:
            sets.append("extra_headers=?")
            params.append(extra_headers_json)
        if enabled is not None:
            sets.append("enabled=?")
            params.append(1 if enabled else 0)
        params.append(cred_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE network_credentials SET {', '.join(sets)} WHERE id=?",
                params,
            )

    def mark_credential_used(self, cred_id: str, now_iso: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE network_credentials SET last_used_at=? WHERE id=?",
                (now_iso, cred_id),
            )

    def soft_delete_credential(self, cred_id: str, now_iso: str) -> None:
        # 同时 append 后缀让出 name（UNIQUE）位置，防止用户重建同名凭证时 IntegrityError
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE network_credentials "
                "SET deleted_at=?, name = name || '__deleted_' || id "
                "WHERE id=? AND deleted_at IS NULL",
                (now_iso, cred_id),
            )

    # --- tool switches ---------------------------------------------------

    def list_disabled_tools(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool_name FROM tool_switches WHERE enabled = 0"
            ).fetchall()
            return {r["tool_name"] for r in rows}

    def set_tool_enabled(self, tool_name: str, enabled: bool) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO tool_switches (tool_name, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tool_name) DO UPDATE SET
                     enabled = excluded.enabled,
                     updated_at = excluded.updated_at""",
                (tool_name, 1 if enabled else 0, now),
            )

    # --- mcp server overrides --------------------------------------------

    def list_mcp_overrides(self) -> list[dict]:
        """读取所有 mcp_server_overrides 行。

        nullable 字段语义：
          * 若 YAML 中存在同名 server：NULL = 沿用 YAML，非 NULL = 覆写
          * 若 YAML 中无同名 server：DB 必须填够 transport + 主字段，merge 时晋级为完整 server
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT name, enabled, env_json,
                          tools_include_json, tools_exclude_json,
                          transport, command, args_json,
                          url, headers_json,
                          default_tier, timeout, connect_timeout,
                          tool_overrides_json
                     FROM mcp_server_overrides"""
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "name": r["name"],
                    "enabled": None if r["enabled"] is None else bool(r["enabled"]),
                    "env": json.loads(r["env_json"]) if r["env_json"] else None,
                    "tools_include": (
                        json.loads(r["tools_include_json"]) if r["tools_include_json"] else None
                    ),
                    "tools_exclude": (
                        json.loads(r["tools_exclude_json"]) if r["tools_exclude_json"] else None
                    ),
                    "transport": r["transport"],
                    "command": r["command"],
                    "args": json.loads(r["args_json"]) if r["args_json"] else None,
                    "url": r["url"],
                    "headers": (json.loads(r["headers_json"]) if r["headers_json"] else None),
                    "default_tier": r["default_tier"],
                    "timeout": r["timeout"],
                    "connect_timeout": r["connect_timeout"],
                    "tool_overrides": (
                        json.loads(r["tool_overrides_json"]) if r["tool_overrides_json"] else None
                    ),
                }
            )
        return out

    def upsert_mcp_override(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        env: dict[str, str] | None = None,
        tools_include: list[str] | None = None,
        tools_exclude: list[str] | None = None,
        transport: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        default_tier: int | None = None,
        timeout: int | None = None,
        connect_timeout: int | None = None,
        tool_overrides: dict[str, int] | None = None,
    ) -> None:
        """upsert 一行 server 配置；None 字段写入 NULL（= 沿用 YAML 或不指定）。"""
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                # COALESCE 让 NULL 入参表示「不动该字段」，保留旧值。
                # 这避免 PATCH 单字段时把其他列清成 NULL。
                # 想真的清字段 → 走 DELETE override 删整行后重建。
                """INSERT INTO mcp_server_overrides
                   (name, enabled, env_json, tools_include_json, tools_exclude_json,
                    transport, command, args_json, url, headers_json,
                    default_tier, timeout, connect_timeout, tool_overrides_json,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     enabled = COALESCE(excluded.enabled, mcp_server_overrides.enabled),
                     env_json = COALESCE(excluded.env_json, mcp_server_overrides.env_json),
                     tools_include_json = COALESCE(excluded.tools_include_json, mcp_server_overrides.tools_include_json),
                     tools_exclude_json = COALESCE(excluded.tools_exclude_json, mcp_server_overrides.tools_exclude_json),
                     transport = COALESCE(excluded.transport, mcp_server_overrides.transport),
                     command = COALESCE(excluded.command, mcp_server_overrides.command),
                     args_json = COALESCE(excluded.args_json, mcp_server_overrides.args_json),
                     url = COALESCE(excluded.url, mcp_server_overrides.url),
                     headers_json = COALESCE(excluded.headers_json, mcp_server_overrides.headers_json),
                     default_tier = COALESCE(excluded.default_tier, mcp_server_overrides.default_tier),
                     timeout = COALESCE(excluded.timeout, mcp_server_overrides.timeout),
                     connect_timeout = COALESCE(excluded.connect_timeout, mcp_server_overrides.connect_timeout),
                     tool_overrides_json = COALESCE(excluded.tool_overrides_json, mcp_server_overrides.tool_overrides_json),
                     updated_at = excluded.updated_at""",
                (
                    name,
                    None if enabled is None else (1 if enabled else 0),
                    json.dumps(env) if env is not None else None,
                    json.dumps(tools_include) if tools_include is not None else None,
                    json.dumps(tools_exclude) if tools_exclude is not None else None,
                    transport,
                    command,
                    json.dumps(args) if args is not None else None,
                    url,
                    json.dumps(headers) if headers is not None else None,
                    default_tier,
                    timeout,
                    connect_timeout,
                    json.dumps(tool_overrides) if tool_overrides is not None else None,
                    now,
                ),
            )

    def delete_mcp_override(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM mcp_server_overrides WHERE name = ?", (name,))

    # --- engine preferences ---------------------------------------------

    def get_engine_preferences(self) -> dict:
        """返回 {fetch_chain, search_provider, fallback_mode,
        scrapling_dynamic_enabled, scrapling_stealthy_enabled};
        无记录返回全空（不覆盖 profile），开关默认 False。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT fetch_chain, search_provider, fallback_mode, "
                "scrapling_dynamic_enabled, scrapling_stealthy_enabled "
                "FROM engine_preferences WHERE id='default'"
            ).fetchone()
        if row is None:
            return {
                "fetch_chain": [],
                "search_provider": None,
                "fallback_mode": None,
                "scrapling_dynamic_enabled": False,
                "scrapling_stealthy_enabled": False,
            }
        chain = json.loads(row["fetch_chain"] or "[]")
        return {
            "fetch_chain": chain if isinstance(chain, list) else [],
            "search_provider": row["search_provider"],
            "fallback_mode": row["fallback_mode"],
            "scrapling_dynamic_enabled": bool(row["scrapling_dynamic_enabled"]),
            "scrapling_stealthy_enabled": bool(row["scrapling_stealthy_enabled"]),
        }

    def set_engine_preferences(
        self,
        *,
        fetch_chain: list[str],
        search_provider: str | None,
        fallback_mode: str | None,
        scrapling_dynamic_enabled: bool = False,
        scrapling_stealthy_enabled: bool = False,
    ) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO engine_preferences
                   (id, fetch_chain, search_provider, fallback_mode,
                    scrapling_dynamic_enabled, scrapling_stealthy_enabled, updated_at)
                   VALUES ('default', ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     fetch_chain = excluded.fetch_chain,
                     search_provider = excluded.search_provider,
                     fallback_mode = excluded.fallback_mode,
                     scrapling_dynamic_enabled = excluded.scrapling_dynamic_enabled,
                     scrapling_stealthy_enabled = excluded.scrapling_stealthy_enabled,
                     updated_at = excluded.updated_at""",
                (
                    json.dumps(fetch_chain),
                    search_provider,
                    fallback_mode,
                    1 if scrapling_dynamic_enabled else 0,
                    1 if scrapling_stealthy_enabled else 0,
                    now,
                ),
            )

    # --- outer loop iterations ------------------------------------------

    def save_outer_loop_iteration(self, record: dict) -> None:
        """写入一条 outer loop iteration（dict 形式以避免循环 import）。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO outer_loop_iterations
                (id, edict_id, iteration, level, actor_output, checks_result,
                 critic_result, cost_cny, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edict_id, iteration) DO NOTHING
            """,
                (
                    record["id"],
                    record["edict_id"],
                    record["iteration"],
                    record["level"],
                    record["actor_output"],
                    record["checks_result"],
                    record["critic_result"],
                    record["cost_cny"],
                    record["started_at"],
                    record["finished_at"],
                ),
            )

    def get_outer_loop_iterations(self, edict_id: str) -> list[dict]:
        """按 iteration 升序返回所有迭代记录。"""
        rows = self._conn.execute(
            """
            SELECT id, edict_id, iteration, level, actor_output, checks_result,
                   critic_result, cost_cny, started_at, finished_at, archived_at
            FROM outer_loop_iterations
            WHERE edict_id = ?
            ORDER BY iteration ASC
        """,
            (edict_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_iterations_to_archive(self, before: str) -> list[str]:
        """返回 finished_at < before 且未归档的 iteration id 列表。"""
        rows = self._conn.execute(
            """
            SELECT id FROM outer_loop_iterations
            WHERE finished_at < ? AND archived_at IS NULL
        """,
            (before,),
        ).fetchall()
        return [r["id"] for r in rows]

    def archive_iteration(self, iteration_id: str, archived_at: str) -> None:
        """归档：actor_output 置 NULL，archived_at 写时间戳。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE outer_loop_iterations
                SET actor_output = NULL, archived_at = ?
                WHERE id = ?
            """,
                (archived_at, iteration_id),
            )

    # --- outer loop checkpoints ------------------------------------------

    def save_outer_loop_checkpoint(self, edict_id: str, data_json: str, saved_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO outer_loop_checkpoints (edict_id, data_json, saved_at)
                VALUES (?, ?, ?)
                ON CONFLICT(edict_id) DO UPDATE SET data_json=excluded.data_json, saved_at=excluded.saved_at
            """,
                (edict_id, data_json, saved_at),
            )

    def get_outer_loop_checkpoint(self, edict_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT data_json FROM outer_loop_checkpoints WHERE edict_id = ?",
            (edict_id,),
        ).fetchone()
        return row["data_json"] if row else None

    def clear_outer_loop_checkpoint(self, edict_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM outer_loop_checkpoints WHERE edict_id = ?",
                (edict_id,),
            )

    # --- Supervision report (long task 终态总评) ---

    def save_supervision_report(self, record: dict) -> None:
        """写一行监督报告（PK = (memorial_id, persona_id)）。

        record 必带 memorial_id；旧调用方未传时会落 KeyError，强制升级到新 schema。
        """
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO supervision_reports
                   (edict_id, memorial_id, persona_id, persona_name, final_status,
                    iterations_count, total_cost_cny, report_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["edict_id"],
                    record["memorial_id"],
                    record["persona_id"],
                    record["persona_name"],
                    record["final_status"],
                    record["iterations_count"],
                    record["total_cost_cny"],
                    record["report_json"],
                    record["created_at"],
                ),
            )

    def get_supervision_report(self, edict_id: str) -> dict | None:
        """单监督官兼容入口；返同 edict 最新一行。"""
        row = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE edict_id = ? ORDER BY created_at DESC LIMIT 1",
            (edict_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_supervision_reports(self, edict_id: str) -> list[dict]:
        """同 edict 全部报告，按 created_at DESC + persona_id 排序。"""
        rows = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE edict_id = ? "
            "ORDER BY created_at DESC, persona_id ASC",
            (edict_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_supervision_reports_by_memorial(self, memorial_id: str) -> list[dict]:
        """按 memorial 维度返回报告（每条奏折独立的监督报告）。"""
        rows = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE memorial_id = ? ORDER BY persona_id ASC",
            (memorial_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Feishu session anchor ---

    def get_feishu_anchor(self, chat_id: str, instance_id: str = "feishu-default") -> str | None:
        row = self._conn.execute(
            "SELECT current_edict_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        ).fetchone()
        return row[0] if row else None

    def set_feishu_anchor(
        self, chat_id: str, edict_id: str, instance_id: str = "feishu-default"
    ) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT INTO feishu_session_anchor (instance_id, chat_id, current_edict_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(instance_id, chat_id) DO UPDATE SET "
            "    current_edict_id = excluded.current_edict_id, "
            "    updated_at = excluded.updated_at",
            (instance_id, chat_id, edict_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def delete_feishu_anchor(self, chat_id: str, instance_id: str = "feishu-default") -> None:
        """`/exit` 用：清除该 chat 的 anchor，回到助手模式。"""
        self._conn.execute(
            "DELETE FROM feishu_session_anchor WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        )
        self._conn.commit()

    def has_sent_upgrade_notice(self, chat_id: str, version_tag: str) -> bool:
        """幂等检查：是否已对此 chat 发过该版本的升级通告。"""
        row = self._conn.execute(
            "SELECT 1 FROM feishu_pending_cards WHERE approval_id = ? AND kind = ?",
            (chat_id, f"upgrade_notice_{version_tag}"),
        ).fetchone()
        return row is not None

    def mark_upgrade_notice_sent(self, chat_id: str, version_tag: str) -> None:
        """标记已发送升级通告（用于幂等）。"""
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR IGNORE INTO feishu_pending_cards "
            "(approval_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, chat_id, "", f"upgrade_notice_{version_tag}", datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def list_active_anchor_chats(self, instance_id: str = "feishu-default") -> list[str]:
        """列出所有有活跃 anchor 的 chat（用于升级通告下发）。"""
        rows = self._conn.execute(
            "SELECT chat_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND current_edict_id IS NOT NULL",
            (instance_id,),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Feishu typing reaction（替代 v1 的 "🤔 思考中" 卡片）---
    #
    # 沿用旧表 feishu_thinking_messages：`message_id` 列存 reaction_id，
    # `source_message_id` 列存用户原消息 id（reaction API 必需）。

    def save_feishu_thinking(
        self,
        *,
        memorial_id: str,
        chat_id: str,
        reaction_id: str,
        source_message_id: str,
    ) -> None:
        """登记一条 typing reaction，等 execution 完成时 remove。"""
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR REPLACE INTO feishu_thinking_messages "
            "(memorial_id, chat_id, message_id, source_message_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (memorial_id, chat_id, reaction_id, source_message_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_feishu_thinking(self, memorial_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id, source_message_id "
            "FROM feishu_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM feishu_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        )
        self._conn.commit()
        return {
            "chat_id": row[0],
            "reaction_id": row[1],
            "source_message_id": row[2],
        }

    def list_chats_anchored_to(
        self, edict_id: str, instance_id: str = "feishu-default"
    ) -> list[str]:
        """反查：哪些飞书 chat 的 anchor 当前指向该 edict。

        用于飞书 outbound 在 edict.metadata.chat_id 缺失（web 创建敕令）时
        定位回执目标 —— 精准送回到 /select 切过来的那个 chat。
        """
        rows = self._conn.execute(
            "SELECT chat_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND current_edict_id = ?",
            (instance_id, edict_id),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Feishu dedup ---

    def is_feishu_message_seen(self, message_id: str, instance_id: str = "feishu-default") -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM feishu_seen_messages WHERE message_id = ? AND instance_id = ?",
            (message_id, instance_id),
        ).fetchone()
        return row is not None

    def mark_feishu_message_seen(
        self,
        message_id: str,
        max_entries: int = 2048,
        instance_id: str = "feishu-default",
    ) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO feishu_seen_messages (message_id, instance_id, seen_at) "
            "VALUES (?, ?, ?)",
            (message_id, instance_id, now),
        )
        self._conn.execute(
            "DELETE FROM feishu_seen_messages WHERE instance_id = ? AND message_id IN ("
            "  SELECT message_id FROM feishu_seen_messages WHERE instance_id = ? "
            "  ORDER BY seen_at ASC "
            "  LIMIT MAX(0, (SELECT COUNT(*) FROM feishu_seen_messages WHERE instance_id = ?) - ?))",
            (instance_id, instance_id, instance_id, max_entries),
        )
        self._conn.commit()

    # --- Feishu pending cards (Step 5 用) ---

    def save_feishu_pending_card(
        self,
        approval_id: str,
        chat_id: str,
        message_id: str,
        kind: str,
        instance_id: str = "feishu-default",
    ) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR REPLACE INTO feishu_pending_cards "
            "(approval_id, instance_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, instance_id, chat_id, message_id, kind, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_feishu_pending_card(self, approval_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id, kind FROM feishu_pending_cards WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM feishu_pending_cards WHERE approval_id = ?",
            (approval_id,),
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}

    # --- Telegram session anchor（与飞书并列）---

    def get_telegram_anchor(
        self, chat_id: str, instance_id: str = "telegram-default"
    ) -> str | None:
        row = self._conn.execute(
            "SELECT current_edict_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        ).fetchone()
        return row[0] if row else None

    def set_telegram_anchor(
        self, chat_id: str, edict_id: str, instance_id: str = "telegram-default"
    ) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT INTO telegram_session_anchor (instance_id, chat_id, current_edict_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(instance_id, chat_id) DO UPDATE SET "
            "    current_edict_id = excluded.current_edict_id, "
            "    updated_at = excluded.updated_at",
            (instance_id, chat_id, edict_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def delete_telegram_anchor(self, chat_id: str, instance_id: str = "telegram-default") -> None:
        self._conn.execute(
            "DELETE FROM telegram_session_anchor WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        )
        self._conn.commit()

    def list_telegram_active_anchor_chats(self, instance_id: str = "telegram-default") -> list[str]:
        rows = self._conn.execute(
            "SELECT chat_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND current_edict_id IS NOT NULL",
            (instance_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def list_telegram_chats_anchored_to(
        self, edict_id: str, instance_id: str = "telegram-default"
    ) -> list[str]:
        """反查：哪些 telegram chat 的 anchor 指向该 edict（出站定位回执目标）。"""
        rows = self._conn.execute(
            "SELECT chat_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND current_edict_id = ?",
            (instance_id, edict_id),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Telegram dedup ---

    def is_telegram_update_seen(
        self, update_id: str, instance_id: str = "telegram-default"
    ) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM telegram_seen_messages WHERE update_id = ? AND instance_id = ?",
            (update_id, instance_id),
        ).fetchone()
        return row is not None

    def mark_telegram_update_seen(
        self,
        update_id: str,
        max_entries: int = 2048,
        instance_id: str = "telegram-default",
    ) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO telegram_seen_messages (update_id, instance_id, seen_at) "
            "VALUES (?, ?, ?)",
            (update_id, instance_id, now),
        )
        self._conn.execute(
            "DELETE FROM telegram_seen_messages WHERE instance_id = ? AND update_id IN ("
            "  SELECT update_id FROM telegram_seen_messages WHERE instance_id = ? "
            "  ORDER BY seen_at ASC "
            "  LIMIT MAX(0, (SELECT COUNT(*) FROM telegram_seen_messages WHERE instance_id = ?) - ?))",
            (instance_id, instance_id, instance_id, max_entries),
        )
        self._conn.commit()

    # --- Telegram thinking 占位消息（替代飞书 typing reaction）---

    def save_telegram_thinking(
        self,
        *,
        memorial_id: str,
        chat_id: str,
        message_id: str,
    ) -> None:
        """登记一条 ⏳ 占位消息，等 execution 完成时 delete。"""
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR REPLACE INTO telegram_thinking_messages "
            "(memorial_id, chat_id, message_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (memorial_id, chat_id, message_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_telegram_thinking(self, memorial_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id FROM telegram_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM telegram_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1]}

    # --- Telegram pending buttons（审批 inline keyboard 反查）---

    def save_telegram_pending_button(
        self,
        *,
        approval_id: str,
        chat_id: str,
        message_id: str,
        kind: str,
        instance_id: str = "telegram-default",
    ) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR REPLACE INTO telegram_pending_buttons "
            "(approval_id, instance_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, instance_id, chat_id, message_id, kind, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_telegram_pending_button(self, approval_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id, kind FROM telegram_pending_buttons WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM telegram_pending_buttons WHERE approval_id = ?",
            (approval_id,),
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}

    def get_telegram_pending_button(self, approval_id: str) -> dict | None:
        """只读查询（不删除）：callback 处理时先看 pending 是否还在。"""
        row = self._conn.execute(
            "SELECT chat_id, message_id, kind FROM telegram_pending_buttons WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}

    def list_telegram_pending_for_chat(
        self, chat_id: str, instance_id: str = "telegram-default"
    ) -> list[str]:
        """该 chat 下尚未响应的待审批 memorial_id（approval 文本命令用）。"""
        rows = self._conn.execute(
            "SELECT approval_id FROM telegram_pending_buttons "
            "WHERE instance_id = ? AND chat_id = ? AND kind = 'tool.approval_required' "
            "ORDER BY created_at ASC",
            (instance_id, chat_id),
        ).fetchall()
        return [r[0] for r in rows]

    # --- Channel configs (通政司) ---

    def get_channel_config(self, channel_type: str) -> dict | None:
        """返回非敏感配置 dict + secret 是否已配（不返明文）。None 表示未配置。"""
        row = self._conn.execute(
            "SELECT config_json, encrypted_secret, updated_at "
            "FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()
        if not row:
            return None
        import json as _json

        cfg = _json.loads(row[0])
        cfg["_has_secret"] = row[1] is not None
        cfg["_updated_at"] = row[2]
        return cfg

    def save_channel_config(
        self,
        channel_type: str,
        config: dict,
        secret_plaintext: str | None = None,
    ) -> None:
        """保存配置；secret_plaintext=None 时不动 encrypted_secret，空串清空。"""
        import json as _json
        from datetime import UTC, datetime

        from tianshu.secrets.vault import get_vault

        # 排除内部字段
        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        config_json_str = _json.dumps(clean_config, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()

        encrypted: bytes | None = None
        update_secret = False
        if secret_plaintext is not None:
            update_secret = True
            if secret_plaintext == "":
                encrypted = None  # 清空
            else:
                vault = get_vault()
                if vault is None:
                    raise RuntimeError(
                        "TIANSHU_SECRET_MASTER_KEY 未设置，无法保存敏感凭证。"
                        "请先配置主密钥后重启服务。"
                    )
                encrypted = vault.encrypt(secret_plaintext)

        existing = self._conn.execute(
            "SELECT 1 FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()

        if existing:
            if update_secret:
                self._conn.execute(
                    "UPDATE channel_configs SET config_json=?, encrypted_secret=?, updated_at=? "
                    "WHERE channel_type=?",
                    (config_json_str, encrypted, now, channel_type),
                )
            else:
                self._conn.execute(
                    "UPDATE channel_configs SET config_json=?, updated_at=? WHERE channel_type=?",
                    (config_json_str, now, channel_type),
                )
        else:
            self._conn.execute(
                "INSERT INTO channel_configs (channel_type, config_json, encrypted_secret, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (channel_type, config_json_str, encrypted, now),
            )
        self._conn.commit()

    def load_channel_runtime_config(self, channel_type: str) -> dict | None:
        """返回**含明文 secret** 的运行时配置；启动加载/reload 用，不暴露给 API。"""
        row = self._conn.execute(
            "SELECT config_json, encrypted_secret FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()
        if not row:
            return None
        import json as _json

        from tianshu.secrets.vault import get_vault

        cfg = _json.loads(row[0])
        # 解密后的 secret 放到 channel 对应的字段：飞书=app_secret，telegram=bot_token
        secret_key = "bot_token" if channel_type == "telegram" else "app_secret"
        if row[1]:
            vault = get_vault()
            if vault is None:
                return None  # 配了 secret 但 vault 缺失 → 视为不可用
            try:
                cfg[secret_key] = vault.decrypt(row[1])
            except ValueError:
                return None
        else:
            cfg[secret_key] = ""
        return cfg

    # --- Channel instances（多 bot 实例）---

    def list_channel_instances(self, channel_type: str | None = None) -> list[dict]:
        """列实例（不含明文 secret）。每行展开 config + instance_id / channel_type /
        label / enabled(bool) / _has_secret / updated_at。"""
        import json as _json

        sql = (
            "SELECT instance_id, channel_type, label, enabled, config_json, "
            "encrypted_secret, updated_at FROM channel_instances"
        )
        params: tuple = ()
        if channel_type is not None:
            sql += " WHERE channel_type = ?"
            params = (channel_type,)
        sql += " ORDER BY channel_type, instance_id"
        rows = self._conn.execute(sql, params).fetchall()
        result: list[dict] = []
        for row in rows:
            cfg = _json.loads(row[4])
            cfg["instance_id"] = row[0]
            cfg["channel_type"] = row[1]
            cfg["label"] = row[2]
            cfg["enabled"] = bool(row[3])
            cfg["_has_secret"] = row[5] is not None
            cfg["updated_at"] = row[6]
            result.append(cfg)
        return result

    def get_channel_instance(self, instance_id: str) -> dict | None:
        """单条实例（不含明文 secret）。None 表示不存在。"""
        import json as _json

        row = self._conn.execute(
            "SELECT instance_id, channel_type, label, enabled, config_json, "
            "encrypted_secret, updated_at FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if not row:
            return None
        cfg = _json.loads(row[4])
        cfg["instance_id"] = row[0]
        cfg["channel_type"] = row[1]
        cfg["label"] = row[2]
        cfg["enabled"] = bool(row[3])
        cfg["_has_secret"] = row[5] is not None
        cfg["updated_at"] = row[6]
        return cfg

    def save_channel_instance(
        self,
        *,
        instance_id: str,
        channel_type: str,
        label: str,
        enabled: bool,
        config: dict,
        secret_plaintext: str | None = None,
    ) -> None:
        """保存实例。secret_plaintext=None 时不动 encrypted_secret，空串清空，
        非空则 vault.encrypt（vault 缺失 raise RuntimeError）。config 去掉 _ 开头的 key。"""
        import json as _json
        from datetime import UTC, datetime

        from tianshu.secrets.vault import get_vault

        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        config_json_str = _json.dumps(clean_config, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()

        encrypted: bytes | None = None
        update_secret = False
        if secret_plaintext is not None:
            update_secret = True
            if secret_plaintext == "":
                encrypted = None  # 清空
            else:
                vault = get_vault()
                if vault is None:
                    raise RuntimeError(
                        "TIANSHU_SECRET_MASTER_KEY 未设置，无法保存敏感凭证。"
                        "请先配置主密钥后重启服务。"
                    )
                encrypted = vault.encrypt(secret_plaintext)

        existing = self._conn.execute(
            "SELECT 1 FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()

        if existing:
            if update_secret:
                self._conn.execute(
                    "UPDATE channel_instances SET channel_type=?, label=?, enabled=?, "
                    "config_json=?, encrypted_secret=?, updated_at=? WHERE instance_id=?",
                    (
                        channel_type,
                        label,
                        int(enabled),
                        config_json_str,
                        encrypted,
                        now,
                        instance_id,
                    ),
                )
            else:
                self._conn.execute(
                    "UPDATE channel_instances SET channel_type=?, label=?, enabled=?, "
                    "config_json=?, updated_at=? WHERE instance_id=?",
                    (channel_type, label, int(enabled), config_json_str, now, instance_id),
                )
        else:
            self._conn.execute(
                "INSERT INTO channel_instances "
                "(instance_id, channel_type, label, enabled, config_json, "
                "encrypted_secret, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (instance_id, channel_type, label, int(enabled), config_json_str, encrypted, now),
            )
        self._conn.commit()

    def set_channel_instance_enabled(self, instance_id: str, enabled: bool) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "UPDATE channel_instances SET enabled=?, updated_at=? WHERE instance_id=?",
            (int(enabled), datetime.now(UTC).isoformat(), instance_id),
        )
        self._conn.commit()

    def delete_channel_instance(self, instance_id: str) -> None:
        self._conn.execute(
            "DELETE FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        )
        self._conn.commit()

    def load_channel_instance_runtime(self, instance_id: str) -> dict | None:
        """返回**含明文 secret** 的运行时配置（启动/reload 用）。

        返回 dict 展开 config + instance_id / channel_type / label / enabled，
        且 secret 解密放到 bot_token(telegram) / app_secret(feishu)。
        vault 缺失或解密失败时：若该实例确实存了 secret 则返回 None（视为不可用）；
        无 secret 则 secret 字段填空串。
        """
        import json as _json

        from tianshu.secrets.vault import get_vault

        row = self._conn.execute(
            "SELECT channel_type, label, enabled, config_json, encrypted_secret "
            "FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if not row:
            return None
        channel_type = row[0]
        cfg = _json.loads(row[3])
        cfg["instance_id"] = instance_id
        cfg["channel_type"] = channel_type
        cfg["label"] = row[1]
        cfg["enabled"] = bool(row[2])
        secret_key = "bot_token" if channel_type == "telegram" else "app_secret"
        if row[4]:
            vault = get_vault()
            if vault is None:
                return None  # 配了 secret 但 vault 缺失 → 视为不可用
            try:
                cfg[secret_key] = vault.decrypt(row[4])
            except ValueError:
                return None
        else:
            cfg[secret_key] = ""
        return cfg
