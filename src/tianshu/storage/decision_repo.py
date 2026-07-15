"""Connection-level persistence for durable governance decisions."""

from __future__ import annotations

import json
import sqlite3

from pydantic import ValidationError

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.decision import (
    DecisionRecordV1,
    DecisionRequestV1,
    DecisionResolutionV1,
    DecisionStatus,
    validate_resolution_payload,
)


class DecisionRepositoryError(RuntimeError):
    """Base error for durable decision persistence."""


class DecisionIdentityConflict(DecisionRepositoryError):
    """The stable request identity was reused with different content."""


class DecisionStateConflict(DecisionRepositoryError):
    """The decision was missing, stale, or no longer pending."""


class DecisionDecodeError(DecisionRepositoryError):
    """A persisted decision row does not satisfy the v1 contract."""


_SELECT_RECORD = """
SELECT
    request.decision_request_id,
    request.schema_version,
    request.kind,
    request.edict_id,
    request.memorial_id,
    request.request_key,
    request.payload_json,
    request.payload_hash,
    request.requested_by,
    request.expires_at,
    request.status,
    request.version,
    request.created_at,
    request.updated_at,
    resolution.action AS resolution_action,
    resolution.reason AS resolution_reason,
    resolution.payload_json AS resolution_payload_json,
    resolution.actor_principal_id,
    resolution.actor_display_name,
    resolution.resolved_at
FROM decision_requests AS request
LEFT JOIN decision_resolutions AS resolution
    ON resolution.decision_request_id = request.decision_request_id
"""


def _decode_json_object(raw: object, field: str) -> dict[str, object]:
    if not isinstance(raw, str):
        raise DecisionDecodeError(f"{field} is not text")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DecisionDecodeError(f"{field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DecisionDecodeError(f"{field} is not a JSON object")
    return value


def _decode_record(row: sqlite3.Row) -> DecisionRecordV1:
    request_payload = _decode_json_object(row["payload_json"], "payload_json")
    request_data = {
        "decision_request_id": row["decision_request_id"],
        "schema_version": row["schema_version"],
        "kind": row["kind"],
        "edict_id": row["edict_id"],
        "memorial_id": row["memorial_id"],
        "request_key": row["request_key"],
        "payload": request_payload,
        "payload_hash": row["payload_hash"],
        "requested_by": row["requested_by"],
        "expires_at": row["expires_at"],
        "status": row["status"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    resolution_data = None
    if row["resolution_action"] is not None:
        resolution_data = {
            "decision_request_id": row["decision_request_id"],
            "action": row["resolution_action"],
            "reason": row["resolution_reason"],
            "payload": _decode_json_object(
                row["resolution_payload_json"], "resolution_payload_json"
            ),
            "actor_principal_id": row["actor_principal_id"],
            "actor_display_name": row["actor_display_name"],
            "resolved_at": row["resolved_at"],
        }
    try:
        request = DecisionRequestV1.model_validate_json(json.dumps(request_data))
        resolution = (
            DecisionResolutionV1.model_validate_json(json.dumps(resolution_data))
            if resolution_data is not None
            else None
        )
        return DecisionRecordV1(request=request, resolution=resolution)
    except (ValidationError, TypeError, ValueError) as exc:
        raise DecisionDecodeError("persisted decision violates the v1 contract") from exc


def _require_memorial_binding(
    connection: sqlite3.Connection, memorial_id: str, edict_id: str
) -> None:
    row = connection.execute(
        "SELECT edict_id FROM memorials WHERE id = ?", (memorial_id,)
    ).fetchone()
    if row is None or str(row[0]) != edict_id:
        raise DecisionIdentityConflict("memorial does not belong to the decision edict")


class DecisionRepository:
    """Stateless repository whose caller owns the SQLite transaction."""

    def get(
        self, connection: sqlite3.Connection, decision_request_id: str
    ) -> DecisionRecordV1 | None:
        row = connection.execute(
            _SELECT_RECORD + " WHERE request.decision_request_id = ?",
            (decision_request_id,),
        ).fetchone()
        return _decode_record(row) if row is not None else None

    def add_or_get(
        self, connection: sqlite3.Connection, request: DecisionRequestV1
    ) -> DecisionRequestV1:
        if request.status is not DecisionStatus.PENDING or request.version != 1:
            raise ValueError("new decisions must be pending at version 1")
        _require_memorial_binding(connection, request.memorial_id, request.edict_id)
        existing_row = connection.execute(
            _SELECT_RECORD
            + " WHERE request.memorial_id = ? AND request.kind = ? AND request.request_key = ?",
            (request.memorial_id, request.kind.value, request.request_key),
        ).fetchone()
        if existing_row is not None:
            existing = _decode_record(existing_row).request
            if existing.payload_hash != request.payload_hash:
                raise DecisionIdentityConflict("decision request identity payload hash conflict")
            return existing
        try:
            connection.execute(
                """
                INSERT INTO decision_requests (
                    decision_request_id, schema_version, kind, edict_id, memorial_id,
                    request_key, payload_json, payload_hash, requested_by, expires_at,
                    status, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.decision_request_id,
                    request.schema_version,
                    request.kind.value,
                    request.edict_id,
                    request.memorial_id,
                    request.request_key,
                    canonical_json_bytes(request.payload).decode("utf-8"),
                    request.payload_hash,
                    request.requested_by,
                    request.expires_at.isoformat(),
                    request.status.value,
                    request.version,
                    request.created_at.isoformat(),
                    request.updated_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DecisionIdentityConflict("decision request identity conflict") from exc
        return request

    def resolve(
        self,
        connection: sqlite3.Connection,
        resolution: DecisionResolutionV1,
        *,
        expected_version: int,
    ) -> DecisionRecordV1:
        record = self.get(connection, resolution.decision_request_id)
        if record is None:
            raise DecisionStateConflict("decision request does not exist")
        validate_resolution_payload(record.request.kind, resolution.action, resolution.payload)
        cursor = connection.execute(
            """
            UPDATE decision_requests
            SET status = 'resolved', version = version + 1, updated_at = ?
            WHERE decision_request_id = ? AND status = 'pending' AND version = ?
            """,
            (
                resolution.resolved_at.isoformat(),
                resolution.decision_request_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise DecisionStateConflict("decision request is stale or no longer pending")
        connection.execute(
            """
            INSERT INTO decision_resolutions (
                decision_request_id, action, reason, payload_json,
                actor_principal_id, actor_display_name, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolution.decision_request_id,
                resolution.action,
                resolution.reason,
                canonical_json_bytes(resolution.payload).decode("utf-8"),
                resolution.actor_principal_id,
                resolution.actor_display_name,
                resolution.resolved_at.isoformat(),
            ),
        )
        saved = self.get(connection, resolution.decision_request_id)
        if saved is None:  # pragma: no cover - guarded by the successful CAS above
            raise DecisionStateConflict("resolved decision disappeared")
        return saved


__all__ = [
    "DecisionDecodeError",
    "DecisionIdentityConflict",
    "DecisionRepository",
    "DecisionRepositoryError",
    "DecisionStateConflict",
]
