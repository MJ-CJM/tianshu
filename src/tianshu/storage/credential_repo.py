"""Storage Credential 领域 Mixin —— 网络凭证（network_credentials）CRUD 与查找。"""

import sqlite3
import threading


class CredentialMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

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
