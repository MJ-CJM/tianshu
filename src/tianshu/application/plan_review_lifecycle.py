"""Atomic convergence between plan-review decisions and suspended attempts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from tianshu.models.canonical import RedactedError, canonical_json_bytes, canonical_sha256
from tianshu.models.decision import DecisionKind, DecisionRecordV1, DecisionStatus
from tianshu.models.events import EventEnvelope
from tianshu.models.run_state import AgentContinuationV1, RunPhase, RunStateV1
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
                decision_id = self._decision_id(state)
                record = (
                    self._storage.decision_repo.get(connection, decision_id)
                    if decision_id is not None
                    else None
                )
                binding_error = self._binding_error(
                    state=state,
                    record=record,
                    memorial_id=str(row["memorial_id"]),
                    decision_id=decision_id,
                )
                if binding_error is not None:
                    self._terminalize(
                        connection,
                        row=row,
                        state=state,
                        decision_id=decision_id,
                        now=now,
                        code="plan_review_binding_invalid",
                        message="Plan review binding is invalid",
                    )
                    changed += 1
                    continue
                assert state is not None and record is not None and decision_id is not None
                if record.request.status is DecisionStatus.PENDING:
                    continue
                approve = (
                    record.request.status is DecisionStatus.RESOLVED
                    and record.resolution is not None
                    and record.resolution.action == "approve"
                )
                decision_at = (
                    record.resolution.resolved_at
                    if record.resolution is not None
                    else record.request.updated_at
                )
                if state.phase is RunPhase.WAITING_DECISION:
                    continuation = state.continuation.model_copy(
                        update={
                            "pending_decision_id": None,
                            "resolved_decision_id": decision_id,
                        }
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
                    self._resume(
                        connection,
                        row=row,
                        now=now,
                        available_at=max(now, decision_at),
                    )
                    connection.execute(
                        """
                        UPDATE memorials SET status='planning'
                        WHERE id=? AND edict_id=? AND dag_node_id IS NULL
                          AND status NOT IN ('completed','failed','cancelled')
                        """,
                        (row["memorial_id"], state.edict_id),
                    )
                else:
                    self._terminalize(
                        connection,
                        row=row,
                        state=state,
                        decision_id=decision_id,
                        now=now,
                        code="plan_review_rejected",
                        message="Plan review did not approve execution",
                    )
                changed += 1
            unit_of_work.commit()
        return changed

    @staticmethod
    def _decision_id(state: RunStateV1 | None) -> str | None:
        if state is None:
            return None
        if state.phase is RunPhase.WAITING_DECISION:
            return state.continuation.pending_decision_id
        if state.phase in {RunPhase.PLANNING, RunPhase.FAILED}:
            return state.continuation.resolved_decision_id
        return None

    @staticmethod
    def _binding_error(
        *,
        state: RunStateV1 | None,
        record: DecisionRecordV1 | None,
        memorial_id: str,
        decision_id: str | None,
    ) -> str | None:
        if state is None or record is None or decision_id is None:
            return "missing"
        request = record.request
        if (
            request.decision_request_id != decision_id
            or request.kind is not DecisionKind.PLAN_REVIEW
            or request.memorial_id != memorial_id
            or request.edict_id != state.edict_id
        ):
            return "identity"
        continuation = state.continuation
        if not isinstance(continuation, AgentContinuationV1):
            return "continuation"
        plan = request.payload.get("plan")
        plan_ref = request.payload.get("plan_ref")
        plan_hash = request.payload.get("plan_hash")
        if (
            not isinstance(plan, dict)
            or not isinstance(plan_ref, str)
            or not isinstance(plan_hash, str)
            or continuation.plan_ref != plan_ref
            or continuation.plan_hash != plan_hash
            or canonical_sha256(plan) != plan_hash
        ):
            return "plan"
        if state.phase is RunPhase.WAITING_DECISION:
            if (
                continuation.pending_decision_id != decision_id
                or continuation.resolved_decision_id is not None
            ):
                return "pending"
            return None
        expected_phase = (
            RunPhase.PLANNING
            if record.request.status is DecisionStatus.RESOLVED
            and record.resolution is not None
            and record.resolution.action == "approve"
            else RunPhase.FAILED
        )
        if (
            record.request.status is DecisionStatus.PENDING
            or state.phase is not expected_phase
            or continuation.pending_decision_id is not None
            or continuation.resolved_decision_id != decision_id
        ):
            return "projected"
        return None

    @staticmethod
    def _resume(connection, *, row, now: datetime, available_at: datetime) -> None:
        cursor = connection.execute(
            """
            UPDATE execution_attempts
            SET status='claimable', heartbeat_at=NULL, available_at=?,
                version=version + 1, updated_at=?
            WHERE attempt_id=? AND memorial_id=? AND status='suspended' AND version=?
            """,
            (
                available_at.isoformat(),
                now.isoformat(),
                row["attempt_id"],
                row["memorial_id"],
                row["version"],
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("plan review attempt changed during resume")

    def _terminalize(
        self,
        connection,
        *,
        row,
        state: RunStateV1 | None,
        decision_id: str | None,
        now: datetime,
        code: str,
        message: str,
    ) -> None:
        failure = RedactedError(
            code=code,
            message=message,
            retryable=False,
            details_hash=None,
        )
        cursor = connection.execute(
            """
            UPDATE execution_attempts
            SET status='failed', heartbeat_at=NULL, failure_json=?,
                version=version + 1, updated_at=?
            WHERE attempt_id=? AND memorial_id=? AND status='suspended' AND version=?
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
        edict_id = state.edict_id if state is not None else "unknown"
        connection.execute(
            """
            UPDATE memorials
            SET status='failed', error=?, failure_reason=?, completed_at=?
            WHERE id=? AND dag_node_id IS NULL
              AND status NOT IN ('completed','cancelled')
            """,
            (message, code, now.isoformat(), row["memorial_id"]),
        )
        event_identity = decision_id or str(row["attempt_id"])
        self._outbox.add(
            connection,
            EventEnvelope(
                event_id=f"{event_identity}:execution.failed:{code}",
                event_type="execution.failed",
                edict_id=edict_id if edict_id != "unknown" else None,
                memorial_id=str(row["memorial_id"]),
                timestamp=now,
                producer="plan-review-attempt-coordinator",
                payload={
                    "status": "failed",
                    "error": message,
                    "failure_reason": code,
                    "decision_request_id": decision_id,
                },
            ),
        )


__all__ = ["PlanReviewAttemptCoordinator"]
