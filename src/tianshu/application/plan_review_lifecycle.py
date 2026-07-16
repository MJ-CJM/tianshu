"""Atomic convergence between plan-review decisions and suspended attempts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from tianshu.models.canonical import RedactedError, canonical_json_bytes
from tianshu.models.decision import DecisionKind, DecisionStatus
from tianshu.models.events import EventEnvelope
from tianshu.models.run_state import RunPhase
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository


class PlanReviewAttemptCoordinator:
    """Resume or terminalize only the attempt bound by a durable continuation."""

    def __init__(
        self,
        storage: Storage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))
        self._outbox = OutboxRepository()

    def reconcile_once(self, *, limit: int = 100) -> int:
        now = self._clock().astimezone(UTC)
        changed = 0
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            rows = connection.execute(
                """
                SELECT attempt_id, memorial_id, version
                FROM execution_attempts
                WHERE status = 'suspended'
                ORDER BY available_at, created_at, attempt_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                state = self._storage.run_state_repo.load(
                    connection,
                    str(row["memorial_id"]),
                )
                if state is None or state.phase is not RunPhase.WAITING_DECISION:
                    continue
                decision_id = state.continuation.pending_decision_id
                if decision_id is None:
                    continue
                record = self._storage.decision_repo.get(connection, decision_id)
                if (
                    record is None
                    or record.request.kind is not DecisionKind.PLAN_REVIEW
                    or record.request.memorial_id != row["memorial_id"]
                    or record.request.edict_id != state.edict_id
                    or record.request.status is DecisionStatus.PENDING
                ):
                    continue
                approve = (
                    record.request.status is DecisionStatus.RESOLVED
                    and record.resolution is not None
                    and record.resolution.action == "approve"
                )
                continuation = state.continuation.model_copy(
                    update={
                        "pending_decision_id": None,
                        "resolved_decision_id": decision_id,
                    }
                )
                decision_at = (
                    record.resolution.resolved_at
                    if record.resolution is not None
                    else record.request.updated_at
                )
                next_state = state.model_copy(
                    update={
                        "phase": RunPhase.PLANNING if approve else RunPhase.FAILED,
                        "continuation": continuation,
                        "updated_at": max(state.updated_at, decision_at),
                    }
                )
                self._storage.run_state_repo.compare_and_swap(
                    connection,
                    next_state,
                    expected_version=state.version,
                )
                if approve:
                    cursor = connection.execute(
                        """
                        UPDATE execution_attempts
                        SET status='claimable', heartbeat_at=NULL, available_at=?,
                            version=version + 1, updated_at=?
                        WHERE attempt_id=? AND memorial_id=? AND status='suspended'
                          AND version=?
                        """,
                        (
                            max(now, decision_at).isoformat(),
                            now.isoformat(),
                            row["attempt_id"],
                            row["memorial_id"],
                            row["version"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("plan review attempt changed during resume")
                else:
                    failure = RedactedError(
                        code="plan_review_rejected",
                        message="Plan review did not approve execution",
                        retryable=False,
                        details_hash=None,
                    )
                    cursor = connection.execute(
                        """
                        UPDATE execution_attempts
                        SET status='failed', heartbeat_at=NULL, failure_json=?,
                            version=version + 1, updated_at=?
                        WHERE attempt_id=? AND memorial_id=? AND status='suspended'
                          AND version=?
                        """,
                        (
                            canonical_json_bytes(failure).decode("utf-8"),
                            now.isoformat(),
                            row["attempt_id"],
                            row["memorial_id"],
                            row["version"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("plan review attempt changed during terminalization")
                    connection.execute(
                        """
                        UPDATE memorials
                        SET status='failed', error=?, completed_at=?
                        WHERE id=? AND edict_id=? AND dag_node_id IS NULL
                        """,
                        (
                            failure.message,
                            now.isoformat(),
                            row["memorial_id"],
                            state.edict_id,
                        ),
                    )
                    self._outbox.add(
                        connection,
                        EventEnvelope(
                            event_id=f"{decision_id}:execution.failed",
                            event_type="execution.failed",
                            edict_id=state.edict_id,
                            memorial_id=str(row["memorial_id"]),
                            timestamp=now,
                            producer="plan-review-attempt-coordinator",
                            payload={
                                "status": "failed",
                                "error": failure.message,
                                "failure_reason": failure.code,
                                "decision_request_id": decision_id,
                            },
                        ),
                    )
                changed += 1
            unit_of_work.commit()
        return changed


__all__ = ["PlanReviewAttemptCoordinator"]
