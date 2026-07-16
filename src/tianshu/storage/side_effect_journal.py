"""Transactional journal for managed side-effect intents and receipts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime

from pydantic import ValidationError

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.side_effect import (
    SideEffectIntentV1,
    SideEffectReceiptV1,
    SideEffectStatus,
)
from tianshu.storage.attempt_ledger import AttemptFenceLost, AttemptLeaseRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class SideEffectJournalError(RuntimeError):
    """Base error for durable side-effect evidence."""


class SideEffectConflict(SideEffectJournalError):
    """An identity, state, version, or execution fence no longer matches."""


class SideEffectDecodeError(SideEffectJournalError):
    """A persisted journal row violates the strict v1 contract."""


_SELECT = "SELECT * FROM side_effect_journal"


def _decode_object(raw: object, field: str) -> dict[str, object]:
    if not isinstance(raw, str):
        raise SideEffectDecodeError(f"{field} is not text")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SideEffectDecodeError(f"{field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SideEffectDecodeError(f"{field} is not a JSON object")
    return value


def _decode_intent(row: sqlite3.Row) -> SideEffectIntentV1:
    payload = {
        "intent_id": row["intent_id"],
        "effect_id": row["effect_id"],
        "schema_version": row["schema_version"],
        "edict_id": row["edict_id"],
        "memorial_id": row["memorial_id"],
        "attempt_id": row["attempt_id"],
        "owner_id": row["owner_id"],
        "fencing_token": row["fencing_token"],
        "sequence_no": row["sequence_no"],
        "boundary": row["boundary"],
        "operation": row["operation"],
        "semantics": row["semantics"],
        "provider_idempotency_key": row["provider_idempotency_key"],
        "request_metadata": _decode_object(row["request_metadata_json"], "request_metadata_json"),
        "request_hash": row["request_hash"],
        "intent_hash": row["intent_hash"],
        "status": row["status"],
        "reason_code": row["reason_code"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    try:
        return SideEffectIntentV1.model_validate_json(json.dumps(payload))
    except (ValidationError, TypeError, ValueError) as exc:
        raise SideEffectDecodeError("persisted side-effect intent violates v1") from exc


def _decode_receipt(row: sqlite3.Row) -> SideEffectReceiptV1 | None:
    if row["status"] != SideEffectStatus.RECEIPTED.value:
        return None
    payload = {
        "intent_id": row["intent_id"],
        "effect_id": row["effect_id"],
        "schema_version": row["schema_version"],
        "edict_id": row["edict_id"],
        "memorial_id": row["memorial_id"],
        "attempt_id": row["receipt_attempt_id"],
        "owner_id": row["receipt_owner_id"],
        "fencing_token": row["receipt_fencing_token"],
        "provider_receipt_id": row["provider_receipt_id"],
        "result_metadata": _decode_object(row["receipt_metadata_json"], "receipt_metadata_json"),
        "result_hash": row["result_hash"],
        "status": row["status"],
        "reason_code": row["reason_code"],
        "version": row["version"],
        "effective_at": row["effective_at"],
        "recorded_at": row["recorded_at"],
    }
    try:
        return SideEffectReceiptV1.model_validate_json(json.dumps(payload))
    except (ValidationError, TypeError, ValueError) as exc:
        raise SideEffectDecodeError("persisted side-effect receipt violates v1") from exc


class SideEffectJournal:
    """Same-connection primitives plus safe transaction-owning convenience methods."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], SqliteUnitOfWork],
        attempt_repository: AttemptLeaseRepository,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._attempt_repository = attempt_repository

    def load_intent_current(
        self, connection: sqlite3.Connection, intent_id: str
    ) -> SideEffectIntentV1 | None:
        row = connection.execute(_SELECT + " WHERE intent_id=?", (intent_id,)).fetchone()
        return _decode_intent(row) if row is not None else None

    def load_by_position_current(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        sequence_no: int,
    ) -> SideEffectIntentV1 | None:
        row = connection.execute(
            _SELECT + " WHERE memorial_id=? AND sequence_no=?",
            (memorial_id, sequence_no),
        ).fetchone()
        return _decode_intent(row) if row is not None else None

    def load_by_effect_current(
        self, connection: sqlite3.Connection, effect_id: str
    ) -> SideEffectIntentV1 | None:
        row = connection.execute(_SELECT + " WHERE effect_id=?", (effect_id,)).fetchone()
        return _decode_intent(row) if row is not None else None

    def load_receipt_current(
        self, connection: sqlite3.Connection, intent_id: str
    ) -> SideEffectReceiptV1 | None:
        row = connection.execute(_SELECT + " WHERE intent_id=?", (intent_id,)).fetchone()
        return _decode_receipt(row) if row is not None else None

    def uncertainty_decision_id_current(
        self, connection: sqlite3.Connection, intent_id: str
    ) -> str | None:
        row = connection.execute(
            "SELECT uncertainty_decision_id FROM side_effect_journal WHERE intent_id=?",
            (intent_id,),
        ).fetchone()
        return str(row[0]) if row is not None and row[0] is not None else None

    def load_intent(self, intent_id: str) -> SideEffectIntentV1 | None:
        with self._unit_of_work_factory() as unit_of_work:
            intent = self.load_intent_current(unit_of_work.connection, intent_id)
            unit_of_work.commit()
            return intent

    def load_by_position(self, *, memorial_id: str, sequence_no: int) -> SideEffectIntentV1 | None:
        with self._unit_of_work_factory() as unit_of_work:
            intent = self.load_by_position_current(
                unit_of_work.connection,
                memorial_id=memorial_id,
                sequence_no=sequence_no,
            )
            unit_of_work.commit()
            return intent

    def load_receipt(self, intent_id: str) -> SideEffectReceiptV1 | None:
        with self._unit_of_work_factory() as unit_of_work:
            receipt = self.load_receipt_current(unit_of_work.connection, intent_id)
            unit_of_work.commit()
            return receipt

    def require_reconciliation_authority_current(
        self,
        connection: sqlite3.Connection,
        *,
        origin: SideEffectIntentV1,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> None:
        """Prove a live caller is the origin authority or its durable retry descendant."""

        self._require_current_authority(
            connection,
            attempt_id=attempt_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            now=now,
        )
        rows = connection.execute(
            """
            SELECT attempt_id, memorial_id, attempt_no, status, fencing_token
            FROM execution_attempts
            WHERE attempt_id IN (?, ?)
            """,
            (origin.attempt_id, attempt_id),
        ).fetchall()
        attempts = {str(row["attempt_id"]): row for row in rows}
        origin_row = attempts.get(origin.attempt_id)
        current_row = attempts.get(attempt_id)
        ledger_origin_fence = int(origin_row["fencing_token"]) if origin_row is not None else None
        if (
            origin_row is None
            or current_row is None
            or origin_row["memorial_id"] != origin.memorial_id
            or current_row["memorial_id"] != origin.memorial_id
            or ledger_origin_fence is None
            or ledger_origin_fence < origin.fencing_token
            or ledger_origin_fence > fencing_token
        ):
            raise SideEffectConflict("side-effect reconciliation root or origin conflict")

        exact_origin = (
            attempt_id == origin.attempt_id
            and owner_id == origin.owner_id
            and fencing_token == origin.fencing_token
        )
        reclaimed_origin = attempt_id == origin.attempt_id and fencing_token > origin.fencing_token
        origin_no = int(origin_row["attempt_no"])
        current_no = int(current_row["attempt_no"])
        descendant = current_no > origin_no and fencing_token > origin.fencing_token
        if descendant:
            lineage = connection.execute(
                """
                SELECT attempt_no, status
                FROM execution_attempts
                WHERE memorial_id=? AND attempt_no>=? AND attempt_no<?
                ORDER BY attempt_no
                """,
                (origin.memorial_id, origin_no, current_no),
            ).fetchall()
            descendant = len(lineage) == current_no - origin_no and all(
                row["status"] == "failed" for row in lineage
            )
        if not (exact_origin or reclaimed_origin or descendant):
            raise SideEffectConflict("side-effect reconciliation authority is unrelated")

    def begin_intent_current(
        self,
        connection: sqlite3.Connection,
        intent: SideEffectIntentV1,
    ) -> SideEffectIntentV1:
        if intent.status is not SideEffectStatus.INTENDED or intent.version != 1:
            raise ValueError("new side-effect intent must be intended at version 1")
        existing_row = connection.execute(
            _SELECT + " WHERE intent_id=? OR effect_id=? OR (memorial_id=? AND sequence_no=?)",
            (intent.intent_id, intent.effect_id, intent.memorial_id, intent.sequence_no),
        ).fetchone()
        if existing_row is not None:
            existing = _decode_intent(existing_row)
            if existing == intent:
                return existing
            raise SideEffectConflict("side-effect intent identity conflict")
        self._require_current_origin(connection, intent)
        try:
            connection.execute(
                """
                INSERT INTO side_effect_journal (
                    intent_id, effect_id, schema_version, edict_id, memorial_id,
                    attempt_id, owner_id, fencing_token, sequence_no, boundary,
                    operation, semantics, provider_idempotency_key,
                    request_metadata_json, request_hash, intent_hash, status,
                    reason_code, uncertainty_decision_id, receipt_attempt_id,
                    receipt_owner_id, receipt_fencing_token, provider_receipt_id,
                    receipt_metadata_json, result_hash, effective_at, recorded_at,
                    version, created_at, updated_at
                ) VALUES (
                    ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'intended',
                    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                    1, ?, ?
                )
                """,
                (
                    intent.intent_id,
                    intent.effect_id,
                    intent.edict_id,
                    intent.memorial_id,
                    intent.attempt_id,
                    intent.owner_id,
                    intent.fencing_token,
                    intent.sequence_no,
                    intent.boundary,
                    intent.operation,
                    intent.semantics.value,
                    intent.provider_idempotency_key,
                    canonical_json_bytes(intent.request_metadata).decode("utf-8"),
                    intent.request_hash,
                    intent.intent_hash,
                    intent.created_at.isoformat(),
                    intent.updated_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise SideEffectConflict("side-effect intent identity conflict") from exc
        return intent

    def begin_intent(self, intent: SideEffectIntentV1) -> SideEffectIntentV1:
        with self._unit_of_work_factory() as unit_of_work:
            saved = self.begin_intent_current(unit_of_work.connection, intent)
            unit_of_work.commit()
            return saved

    def record_receipt_current(
        self,
        connection: sqlite3.Connection,
        receipt: SideEffectReceiptV1,
        *,
        expected_version: int,
    ) -> SideEffectReceiptV1:
        row = connection.execute(_SELECT + " WHERE intent_id=?", (receipt.intent_id,)).fetchone()
        if row is None:
            raise SideEffectConflict("side-effect receipt intent is missing")
        existing_receipt = _decode_receipt(row)
        if existing_receipt is not None:
            if existing_receipt == receipt and expected_version < existing_receipt.version:
                return existing_receipt
            raise SideEffectConflict("side-effect receipt conflict")
        intent = _decode_intent(row)
        if (
            intent.status is not SideEffectStatus.INTENDED
            or intent.version != expected_version
            or receipt.version != expected_version + 1
            or receipt.effect_id != intent.effect_id
            or receipt.edict_id != intent.edict_id
            or receipt.memorial_id != intent.memorial_id
        ):
            raise SideEffectConflict("side-effect receipt identity or version conflict")
        self._require_current_receipt(connection, receipt)
        cursor = connection.execute(
            """
            UPDATE side_effect_journal
            SET status='receipted', receipt_attempt_id=?, receipt_owner_id=?,
                receipt_fencing_token=?, provider_receipt_id=?,
                receipt_metadata_json=?, result_hash=?, effective_at=?, recorded_at=?,
                version=version + 1, updated_at=?
            WHERE intent_id=? AND status='intended' AND version=?
            """,
            (
                receipt.attempt_id,
                receipt.owner_id,
                receipt.fencing_token,
                receipt.provider_receipt_id,
                canonical_json_bytes(receipt.result_metadata).decode("utf-8"),
                receipt.result_hash,
                receipt.effective_at.isoformat(),
                receipt.recorded_at.isoformat(),
                receipt.recorded_at.isoformat(),
                receipt.intent_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise SideEffectConflict("side-effect receipt compare-and-swap conflict")
        durable = self.load_receipt_current(connection, receipt.intent_id)
        if durable is None:  # pragma: no cover - successful update establishes receipt shape
            raise SideEffectConflict("side-effect receipt disappeared")
        return durable

    def record_receipt(
        self,
        receipt: SideEffectReceiptV1,
        *,
        expected_version: int,
    ) -> SideEffectReceiptV1:
        with self._unit_of_work_factory() as unit_of_work:
            saved = self.record_receipt_current(
                unit_of_work.connection,
                receipt,
                expected_version=expected_version,
            )
            unit_of_work.commit()
            return saved

    def mark_uncertain_current(
        self,
        connection: sqlite3.Connection,
        intent_id: str,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        reason_code: str,
        decision_request_id: str,
        updated_at: datetime,
    ) -> SideEffectIntentV1:
        current = self.load_intent_current(connection, intent_id)
        if current is None:
            raise SideEffectConflict("side-effect intent is missing")
        if current.status is SideEffectStatus.UNCERTAIN:
            existing_decision = self.uncertainty_decision_id_current(connection, intent_id)
            if (
                current.reason_code == reason_code
                and existing_decision == decision_request_id
                and expected_version < current.version
            ):
                return current
            raise SideEffectConflict("side-effect uncertainty conflict")
        if current.status is not SideEffectStatus.INTENDED or current.version != expected_version:
            raise SideEffectConflict("side-effect uncertainty version conflict")
        self._require_current_authority(
            connection,
            attempt_id=attempt_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            now=updated_at,
        )
        cursor = connection.execute(
            """
            UPDATE side_effect_journal
            SET status='uncertain', reason_code=?, uncertainty_decision_id=?,
                version=version + 1, updated_at=?
            WHERE intent_id=? AND status='intended' AND version=?
            """,
            (
                reason_code,
                decision_request_id,
                updated_at.isoformat(),
                intent_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise SideEffectConflict("side-effect uncertainty compare-and-swap conflict")
        durable = self.load_intent_current(connection, intent_id)
        if durable is None:  # pragma: no cover
            raise SideEffectConflict("side-effect uncertainty disappeared")
        return durable

    def mark_uncertain(
        self,
        intent_id: str,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        reason_code: str,
        decision_request_id: str | None,
        updated_at: datetime,
    ) -> SideEffectIntentV1:
        if decision_request_id is None:
            self._require_current_authority_in_uow(
                attempt_id=attempt_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now=updated_at,
            )
            raise SideEffectConflict("side-effect uncertainty requires a durable decision")
        with self._unit_of_work_factory() as unit_of_work:
            saved = self.mark_uncertain_current(
                unit_of_work.connection,
                intent_id,
                attempt_id=attempt_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                expected_version=expected_version,
                reason_code=reason_code,
                decision_request_id=decision_request_id,
                updated_at=updated_at,
            )
            unit_of_work.commit()
            return saved

    def _require_current_origin(
        self, connection: sqlite3.Connection, intent: SideEffectIntentV1
    ) -> None:
        memorial = connection.execute(
            "SELECT edict_id FROM memorials WHERE id=?", (intent.memorial_id,)
        ).fetchone()
        attempt = connection.execute(
            "SELECT memorial_id FROM execution_attempts WHERE attempt_id=?",
            (intent.attempt_id,),
        ).fetchone()
        if (
            memorial is None
            or memorial["edict_id"] != intent.edict_id
            or attempt is None
            or attempt["memorial_id"] != intent.memorial_id
        ):
            raise SideEffectConflict("side-effect root identity conflict")
        self._require_current_authority(
            connection,
            attempt_id=intent.attempt_id,
            owner_id=intent.owner_id,
            fencing_token=intent.fencing_token,
            now=intent.created_at,
        )

    def _require_current_receipt(
        self, connection: sqlite3.Connection, receipt: SideEffectReceiptV1
    ) -> None:
        attempt = connection.execute(
            "SELECT memorial_id FROM execution_attempts WHERE attempt_id=?",
            (receipt.attempt_id,),
        ).fetchone()
        if attempt is None or attempt["memorial_id"] != receipt.memorial_id:
            raise SideEffectConflict("side-effect receipt root conflict")
        self._require_current_authority(
            connection,
            attempt_id=receipt.attempt_id,
            owner_id=receipt.owner_id,
            fencing_token=receipt.fencing_token,
            now=receipt.recorded_at,
        )

    def _require_current_authority(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> None:
        try:
            self._attempt_repository.require_current(
                connection,
                attempt_id=attempt_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now=now,
            )
        except AttemptFenceLost as exc:
            raise SideEffectConflict("side-effect execution fence is no longer current") from exc

    def _require_current_authority_in_uow(
        self,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            self._require_current_authority(
                unit_of_work.connection,
                attempt_id=attempt_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now=now,
            )
            unit_of_work.commit()


__all__ = [
    "SideEffectConflict",
    "SideEffectDecodeError",
    "SideEffectJournal",
    "SideEffectJournalError",
]
