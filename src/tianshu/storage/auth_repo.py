"""Storage mixin for hash-only platform authentication credentials."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked


def _row_to_auth_token(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "prefix": row["prefix"],
        "token_hash": row["token_hash"],
        "principal_id": row["principal_id"],
        "principal_kind": row["principal_kind"],
        "display_name": row["display_name"],
        "label": row["label"],
        "scopes": json.loads(row["scopes_json"]),
        "token_type": row["token_type"],
        "family_id": row["family_id"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "replaced_by": row["replaced_by"],
        "last_used_at": row["last_used_at"],
    }


def _auth_token_values(record: dict[str, object]) -> tuple[object, ...]:
    return (
        record["id"],
        record["prefix"],
        record["token_hash"],
        record["principal_id"],
        record["principal_kind"],
        record["display_name"],
        record.get("label", ""),
        json.dumps(record.get("scopes", []), separators=(",", ":"), sort_keys=True),
        record["token_type"],
        record.get("family_id"),
        record["created_at"],
        record.get("expires_at"),
        record.get("revoked_at"),
        record.get("replaced_by"),
        record.get("last_used_at"),
    )


_INSERT_AUTH_TOKEN = """
INSERT INTO auth_tokens (
    id, prefix, token_hash, principal_id, principal_kind, display_name, label,
    scopes_json, token_type, family_id, created_at, expires_at, revoked_at,
    replaced_by, last_used_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _with_transition_count(
    audit: AppendSystemAuditRequest,
    transition_count: int,
) -> AppendSystemAuditRequest:
    if audit.action not in {"auth.token.revoked", "auth.session.revoked"}:
        return audit
    payload = audit.model_dump()
    payload["metadata"] = {**audit.metadata, "family_size": transition_count}
    return AppendSystemAuditRequest.model_validate(payload)


class AuthMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def save_auth_token(self, record: dict[str, object]) -> None:
        with self._lock, self._conn:
            self._conn.execute(_INSERT_AUTH_TOKEN, _auth_token_values(record))

    def save_auth_token_with_audit(
        self,
        record: dict[str, object],
        audit: AppendSystemAuditRequest,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(_INSERT_AUTH_TOKEN, _auth_token_values(record))
            _append_system_audit_unlocked(self._conn, audit)

    def save_auth_tokens(self, records: list[dict[str, object]]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                _INSERT_AUTH_TOKEN,
                [_auth_token_values(record) for record in records],
            )

    def save_auth_tokens_with_audit(
        self,
        records: list[dict[str, object]],
        audits: list[AppendSystemAuditRequest],
    ) -> None:
        if len(records) != len(audits):
            raise ValueError("each auth token requires one audit event")
        with self._lock, self._conn:
            self._conn.executemany(
                _INSERT_AUTH_TOKEN,
                [_auth_token_values(record) for record in records],
            )
            for audit in audits:
                _append_system_audit_unlocked(self._conn, audit)

    def get_auth_token_by_prefix(self, prefix: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM auth_tokens WHERE prefix = ?",
                (prefix,),
            ).fetchone()
        return _row_to_auth_token(row) if row is not None else None

    def get_auth_token(self, token_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM auth_tokens WHERE id = ?",
                (token_id,),
            ).fetchone()
        return _row_to_auth_token(row) if row is not None else None

    def list_auth_tokens(self, token_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM auth_tokens"
        parameters: tuple[object, ...] = ()
        if token_type is not None:
            query += " WHERE token_type = ?"
            parameters = (token_type,)
        query += " ORDER BY created_at DESC, id DESC"
        with self._lock:
            rows = self._conn.execute(query, parameters).fetchall()
        return [_row_to_auth_token(row) for row in rows]

    def revoke_auth_token(self, token_id: str, revoked_at: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE auth_tokens
                SET revoked_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (revoked_at, token_id),
            )
        return cursor.rowcount > 0

    def revoke_auth_token_with_audit(
        self,
        token_id: str,
        revoked_at: str,
        audit: AppendSystemAuditRequest,
    ) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE auth_tokens
                SET revoked_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (revoked_at, token_id),
            )
            if cursor.rowcount > 0:
                _append_system_audit_unlocked(
                    self._conn,
                    _with_transition_count(audit, cursor.rowcount),
                )
        return cursor.rowcount > 0

    def revoke_auth_family(self, family_id: str, revoked_at: str) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE auth_tokens
                SET revoked_at = ?
                WHERE family_id = ? AND revoked_at IS NULL
                """,
                (revoked_at, family_id),
            )
        return cursor.rowcount

    def revoke_auth_family_with_audit(
        self,
        family_id: str,
        revoked_at: str,
        audit: AppendSystemAuditRequest,
    ) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE auth_tokens
                SET revoked_at = ?
                WHERE family_id = ? AND revoked_at IS NULL
                """,
                (revoked_at, family_id),
            )
            if cursor.rowcount > 0 or audit.outcome == "denied":
                _append_system_audit_unlocked(
                    self._conn,
                    _with_transition_count(audit, cursor.rowcount),
                )
        return cursor.rowcount

    def replace_auth_token(
        self,
        old_token_id: str,
        new_record: dict[str, object],
        revoked_at: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(_INSERT_AUTH_TOKEN, _auth_token_values(new_record))
            cursor = self._conn.execute(
                """
                UPDATE auth_tokens
                SET revoked_at = COALESCE(revoked_at, ?), replaced_by = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (revoked_at, new_record["id"], old_token_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("active auth token not found")

    def replace_auth_token_with_audit(
        self,
        old_token_id: str,
        new_record: dict[str, object],
        revoked_at: str,
        audit: AppendSystemAuditRequest,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(_INSERT_AUTH_TOKEN, _auth_token_values(new_record))
            cursor = self._conn.execute(
                """
                UPDATE auth_tokens
                SET revoked_at = COALESCE(revoked_at, ?), replaced_by = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (revoked_at, new_record["id"], old_token_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("active auth token not found")
            _append_system_audit_unlocked(self._conn, audit)

    def replace_auth_session_family(
        self,
        family_id: str,
        refresh_token_id: str,
        new_records: list[dict[str, object]],
        revoked_at: str,
    ) -> None:
        replacement_refresh_ids = [
            str(record["id"]) for record in new_records if record.get("token_type") == "refresh"
        ]
        if len(replacement_refresh_ids) != 1:
            raise ValueError("session rotation requires exactly one refresh token")
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT revoked_at FROM auth_tokens
                WHERE id = ? AND family_id = ? AND token_type = 'refresh'
                """,
                (refresh_token_id, family_id),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise ValueError("active refresh token not found")
            self._conn.execute(
                """
                UPDATE auth_tokens SET revoked_at = COALESCE(revoked_at, ?)
                WHERE family_id = ?
                """,
                (revoked_at, family_id),
            )
            self._conn.executemany(
                _INSERT_AUTH_TOKEN,
                [_auth_token_values(record) for record in new_records],
            )
            self._conn.execute(
                "UPDATE auth_tokens SET replaced_by = ? WHERE id = ?",
                (replacement_refresh_ids[0], refresh_token_id),
            )

    def replace_auth_session_family_with_audit(
        self,
        family_id: str,
        refresh_token_id: str,
        new_records: list[dict[str, object]],
        revoked_at: str,
        audit: AppendSystemAuditRequest,
    ) -> None:
        replacement_refresh_ids = [
            str(record["id"]) for record in new_records if record.get("token_type") == "refresh"
        ]
        if len(replacement_refresh_ids) != 1:
            raise ValueError("session rotation requires exactly one refresh token")
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT revoked_at FROM auth_tokens
                WHERE id = ? AND family_id = ? AND token_type = 'refresh'
                """,
                (refresh_token_id, family_id),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise ValueError("active refresh token not found")
            self._conn.execute(
                """
                UPDATE auth_tokens SET revoked_at = COALESCE(revoked_at, ?)
                WHERE family_id = ?
                """,
                (revoked_at, family_id),
            )
            self._conn.executemany(
                _INSERT_AUTH_TOKEN,
                [_auth_token_values(record) for record in new_records],
            )
            self._conn.execute(
                "UPDATE auth_tokens SET replaced_by = ? WHERE id = ?",
                (replacement_refresh_ids[0], refresh_token_id),
            )
            _append_system_audit_unlocked(self._conn, audit)

    def touch_auth_token(self, token_id: str, used_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE auth_tokens SET last_used_at = ? WHERE id = ?",
                (used_at, token_id),
            )


__all__ = ["AuthMixin"]
