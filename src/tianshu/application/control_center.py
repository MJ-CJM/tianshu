"""Principal-scoped aggregate query for the Control Center."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, cast

from tianshu.models.control_center import ControlCenterSnapshotV1
from tianshu.models.principal import AuthContext
from tianshu.storage.artifact_repo import EvidenceRepository
from tianshu.storage.decision_repo import DecisionRepository
from tianshu.storage.run_state_repo import RunStateRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork

_SUMMARY_LIMIT = 20


class ControlCenterUnavailable(RuntimeError):
    """One or more authoritative snapshot sources could not be read."""


class ControlCenterQueryService:
    def __init__(
        self,
        *,
        unit_of_work: Callable[[], SqliteUnitOfWork],
        decision_repository: DecisionRepository,
        run_state_repository: RunStateRepository,
        evidence_repository: EvidenceRepository,
        readiness_status: Callable[[], str],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._decision_repository = decision_repository
        self._run_state_repository = run_state_repository
        self._evidence_repository = evidence_repository
        self._readiness_status = readiness_status
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_snapshot(self, auth: AuthContext) -> ControlCenterSnapshotV1:
        try:
            readiness_status = self._readiness_status()
            if readiness_status not in {"ready", "degraded"}:
                raise ControlCenterUnavailable("system is not ready")
            readiness = cast(Literal["ready", "degraded"], readiness_status)
            submitter = auth.principal.id
            with self._unit_of_work() as unit_of_work:
                active_run_total = self._run_state_repository.count_active_for_submitter(
                    unit_of_work.connection,
                    submitter=submitter,
                )
                active_runs = self._run_state_repository.list_active_for_submitter(
                    unit_of_work.connection,
                    submitter=submitter,
                    limit=_SUMMARY_LIMIT,
                )
                pending_decision_total = self._decision_repository.count_pending_for_submitter(
                    unit_of_work.connection,
                    submitter=submitter,
                )
                pending_decisions = self._decision_repository.list_pending_for_submitter(
                    unit_of_work.connection,
                    submitter=submitter,
                    limit=_SUMMARY_LIMIT,
                )
                evidence_total = self._evidence_repository.count_for_submitter_current(
                    unit_of_work.connection,
                    submitter=submitter,
                )
                recent_evidence = self._evidence_repository.list_recent_for_submitter_current(
                    unit_of_work.connection,
                    submitter=submitter,
                    limit=_SUMMARY_LIMIT,
                )
                unit_of_work.commit()
        except ControlCenterUnavailable:
            raise
        except Exception as exc:
            raise ControlCenterUnavailable("authoritative snapshot source failed") from exc

        return ControlCenterSnapshotV1(
            generated_at=self._clock(),
            readiness=readiness,
            active_run_total=active_run_total,
            pending_decision_total=pending_decision_total,
            evidence_total=evidence_total,
            active_runs=tuple(
                sorted(
                    active_runs,
                    key=lambda item: (-item.updated_at.timestamp(), item.memorial_id),
                )
            ),
            pending_decisions=tuple(
                sorted(
                    pending_decisions,
                    key=lambda item: (
                        item.expires_at,
                        item.created_at,
                        item.decision_request_id,
                    ),
                )
            ),
            recent_evidence=tuple(
                sorted(
                    recent_evidence,
                    key=lambda item: (
                        -(item.closed_at or item.created_at).timestamp(),
                        item.bundle_id,
                    ),
                )
            ),
            evolution_status="not_enabled",
        )


__all__ = ["ControlCenterQueryService", "ControlCenterUnavailable"]
