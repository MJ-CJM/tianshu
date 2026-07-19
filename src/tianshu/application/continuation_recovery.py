"""Atomic convergence of durable Decisions and suspended execution attempts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from tianshu.models.canonical import RedactedError, canonical_json_bytes
from tianshu.models.decision import DecisionKind, DecisionRecordV1, DecisionStatus
from tianshu.models.events import EventEnvelope
from tianshu.models.run_state import RunPhase, RunStateV1
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository


class ContinuationRecoveryError(RuntimeError):
    """Durable continuation authority is missing, mismatched, or concurrently changed."""


class ContinuationRecoveryService:
    """Resume or cancel exactly one Decision-bound suspended attempt in one UoW."""

    def __init__(
        self,
        storage: Storage,
        *,
        clock: Callable[[], datetime] | None = None,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))
        self._boundary_hook = boundary_hook
        self._outbox = OutboxRepository()

    async def handle_decision_resolved(self, event: EventEnvelope) -> bool:
        """Converge one resolved outer-loop or managed-effect Decision idempotently."""

        if event.event_type != "decision.resolved":
            return False
        decision_id = event.payload.get("decision_request_id")
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ContinuationRecoveryError("resolved event is missing decision identity")
        now = self._now()
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            record = self._storage.decision_repo.get(connection, decision_id)
            if not self._is_supported(record):
                unit_of_work.commit()
                return False
            assert record is not None and record.resolution is not None
            request = record.request
            if (
                event.edict_id != request.edict_id
                or event.memorial_id != request.memorial_id
                or event.payload.get("kind") != request.kind.value
                or event.payload.get("action") != record.resolution.action
            ):
                raise ContinuationRecoveryError("resolved event does not match durable authority")

            memorial = connection.execute(
                "SELECT edict_id, status FROM memorials WHERE id=? AND dag_node_id IS NULL",
                (request.memorial_id,),
            ).fetchone()
            if memorial is None or str(memorial["edict_id"]) != request.edict_id:
                raise ContinuationRecoveryError("decision root binding is unavailable")
            if str(memorial["status"]) in {"completed", "failed", "cancelled"}:
                unit_of_work.commit()
                return False

            event_id = f"{decision_id}:execution.resume.requested"
            cancel_event_id = f"{decision_id}:execution.cancelled"
            if (
                self._outbox.get(connection, event_id) is not None
                or self._outbox.get(connection, cancel_event_id) is not None
            ):
                unit_of_work.commit()
                return False

            state = self._storage.run_state_repo.load(connection, request.memorial_id)
            self._require_state_binding(state, record)
            assert state is not None
            attempt = connection.execute(
                """
                SELECT attempt_id, status, version
                FROM execution_attempts
                WHERE memorial_id=? AND status IN ('suspended','claimable','claimed')
                ORDER BY attempt_no DESC, created_at DESC LIMIT 1
                """,
                (request.memorial_id,),
            ).fetchone()
            if attempt is None:
                raise ContinuationRecoveryError("decision suspension has no live attempt")

            if self._is_cancel(record):
                self._cancel(
                    connection,
                    record=record,
                    state=state,
                    attempt=attempt,
                    now=now,
                    event_id=cancel_event_id,
                )
                unit_of_work.commit()
                return True

            state = self._resume_state(connection, record=record, state=state, now=now)
            self._observe("after_run_state")
            if str(attempt["status"]) == "suspended":
                if not self._storage.attempt_repo.resume_suspended_current(
                    connection,
                    attempt_id=str(attempt["attempt_id"]),
                    memorial_id=request.memorial_id,
                    expected_version=int(attempt["version"]),
                    available_at=now,
                ):
                    raise ContinuationRecoveryError("suspended attempt changed during resume")
            elif str(attempt["status"]) not in {"claimable", "claimed"}:
                raise ContinuationRecoveryError("attempt cannot resume")
            self._observe("before_resume_outbox")
            self._outbox.add(
                connection,
                EventEnvelope(
                    event_id=event_id,
                    event_type="execution.resume.requested",
                    edict_id=request.edict_id,
                    memorial_id=request.memorial_id,
                    producer="continuation-recovery.v1",
                    timestamp=now,
                    payload={
                        "schema_version": 1,
                        "decision_request_id": decision_id,
                        "attempt_id": str(attempt["attempt_id"]),
                        "run_state_version": state.version,
                    },
                ),
            )
            unit_of_work.commit()
            return True

    @staticmethod
    def _is_supported(record: DecisionRecordV1 | None) -> bool:
        if (
            record is None
            or record.request.status is not DecisionStatus.RESOLVED
            or record.resolution is None
        ):
            return False
        if record.request.kind is DecisionKind.OUTER_LOOP:
            return True
        return record.request.kind is DecisionKind.TOOL and record.request.request_key.startswith(
            "side-effect:"
        )

    @staticmethod
    def _is_cancel(record: DecisionRecordV1) -> bool:
        assert record.resolution is not None
        return (
            record.request.kind is DecisionKind.OUTER_LOOP and record.resolution.action == "abort"
        ) or (record.request.kind is DecisionKind.TOOL and record.resolution.action == "reject")

    @staticmethod
    def _require_state_binding(
        state: RunStateV1 | None,
        record: DecisionRecordV1,
    ) -> None:
        if state is None:
            raise ContinuationRecoveryError("decision RunState is unavailable")
        decision_id = record.request.decision_request_id
        waiting = (
            state.phase is RunPhase.WAITING_DECISION
            and state.continuation.pending_decision_id == decision_id
            and state.continuation.resolved_decision_id is None
        )
        projected = (
            state.phase is RunPhase.EXECUTING
            and state.continuation.pending_decision_id is None
            and state.continuation.resolved_decision_id == decision_id
        )
        if (
            state.edict_id != record.request.edict_id
            or state.memorial_id != record.request.memorial_id
            or not (waiting or projected)
        ):
            raise ContinuationRecoveryError("decision RunState binding is invalid")

    def _resume_state(
        self,
        connection,
        *,
        record: DecisionRecordV1,
        state: RunStateV1,
        now: datetime,
    ) -> RunStateV1:
        if state.phase is RunPhase.EXECUTING:
            return state
        decision_id = record.request.decision_request_id
        continuation = state.continuation.model_copy(
            update={
                "pending_decision_id": None,
                "resolved_decision_id": decision_id,
            }
        )
        if record.request.kind is DecisionKind.TOOL:
            intent_id = record.request.request_key.removeprefix("side-effect:")
            intent = self._storage.side_effect_journal.load_intent_current(connection, intent_id)
            if intent is None or intent.memorial_id != state.memorial_id:
                raise ContinuationRecoveryError("managed-effect intent binding is unavailable")
            cursor = max(state.side_effect_cursor, intent.sequence_no + 1)
            continuation = continuation.model_copy(
                update={"pending_tool": None, "side_effect_cursor": cursor}
            )
        else:
            cursor = state.side_effect_cursor
        candidate = state.model_copy(
            update={
                "phase": RunPhase.EXECUTING,
                "continuation": continuation,
                "side_effect_cursor": cursor,
                "updated_at": max(state.updated_at, record.resolution.resolved_at, now),
            }
        )
        return self._storage.run_state_repo.compare_and_swap(
            connection,
            candidate,
            expected_version=state.version,
        )

    def _cancel(
        self,
        connection,
        *,
        record: DecisionRecordV1,
        state: RunStateV1,
        attempt,
        now: datetime,
        event_id: str,
    ) -> None:
        decision_id = record.request.decision_request_id
        continuation = state.continuation.model_copy(
            update={
                "pending_decision_id": None,
                "resolved_decision_id": decision_id,
            }
        )
        if hasattr(continuation, "pending_tool"):
            continuation = continuation.model_copy(update={"pending_tool": None})
        terminal = state.model_copy(
            update={
                "phase": RunPhase.FAILED,
                "continuation": continuation,
                "updated_at": max(state.updated_at, record.resolution.resolved_at, now),
            }
        )
        self._storage.run_state_repo.compare_and_swap(
            connection,
            terminal,
            expected_version=state.version,
        )
        self._observe("after_run_state")
        failure = RedactedError(
            code="decision_cancelled",
            message="Execution was cancelled by durable decision",
            retryable=False,
            details_hash=None,
        )
        cursor = connection.execute(
            """
            UPDATE execution_attempts
            SET status='failed', owner_id=NULL, heartbeat_at=NULL, lease_expires_at=NULL,
                failure_json=?, version=version + 1, updated_at=?
            WHERE attempt_id=? AND memorial_id=? AND status IN ('suspended','claimable','claimed')
              AND version=?
            """,
            (
                canonical_json_bytes(failure).decode("utf-8"),
                now.isoformat(),
                attempt["attempt_id"],
                record.request.memorial_id,
                attempt["version"],
            ),
        )
        if cursor.rowcount != 1:
            raise ContinuationRecoveryError("attempt changed during cancellation")
        cursor = connection.execute(
            """
            UPDATE memorials
            SET status='cancelled', error='Execution was cancelled',
                failure_reason='decision_cancelled', completed_at=?
            WHERE id=? AND edict_id=? AND dag_node_id IS NULL
              AND status NOT IN ('completed','failed','cancelled')
            """,
            (now.isoformat(), record.request.memorial_id, record.request.edict_id),
        )
        if cursor.rowcount != 1:
            raise ContinuationRecoveryError("root changed during cancellation")
        self._observe("before_resume_outbox")
        self._outbox.add(
            connection,
            EventEnvelope(
                event_id=event_id,
                event_type="execution.cancelled",
                edict_id=record.request.edict_id,
                memorial_id=record.request.memorial_id,
                producer="continuation-recovery.v1",
                timestamp=now,
                payload={
                    "schema_version": 1,
                    "decision_request_id": decision_id,
                    "attempt_id": str(attempt["attempt_id"]),
                    "status": "cancelled",
                    "failure_reason": "decision_cancelled",
                },
            ),
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("continuation recovery clock must be timezone-aware")
        return now.astimezone(UTC)

    def _observe(self, boundary: str) -> None:
        if self._boundary_hook is not None:
            self._boundary_hook(boundary)


__all__ = ["ContinuationRecoveryError", "ContinuationRecoveryService"]
