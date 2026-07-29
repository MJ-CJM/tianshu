"""Storage ModelProvider 领域 Mixin —— model_providers 表 CRUD。"""

import sqlite3
import threading
from datetime import UTC, datetime


class ModelProviderMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def list_model_providers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM model_providers ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_model_provider(self, provider_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM model_providers WHERE id = ?", (provider_id,)
            ).fetchone()
        return dict(row) if row else None

    def save_model_provider(self, provider: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO model_providers
                   (id, profile_id, display_name, base_url, api_key_ref, enabled,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     profile_id = excluded.profile_id,
                     display_name = excluded.display_name,
                     base_url = excluded.base_url,
                     api_key_ref = excluded.api_key_ref,
                     enabled = excluded.enabled,
                     updated_at = excluded.updated_at""",
                (
                    provider["id"],
                    provider["profile_id"],
                    provider.get("display_name", ""),
                    provider.get("base_url", ""),
                    provider.get("api_key_ref", ""),
                    1 if provider.get("enabled", True) else 0,
                    provider.get("created_at", now),
                    now,
                ),
            )

    def delete_model_provider(self, provider_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM model_providers WHERE id = ?", (provider_id,))
            return cursor.rowcount > 0

    def count_llm_configs_by_provider(self, provider_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM llm_configs WHERE provider_id = ?", (provider_id,)
            ).fetchone()
        return int(row[0])
