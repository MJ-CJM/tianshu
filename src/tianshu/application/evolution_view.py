"""Truthful Evolution Center read service for disabled and governed S5 states."""

from __future__ import annotations

import sqlite3
from typing import Literal, Protocol

from tianshu.evolution.gates import EvolutionGateReportV1, GateStatus
from tianshu.models.evolution_candidate import CandidateLifecycle, EvolutionCandidateV1
from tianshu.models.evolution_view import (
    EvolutionCandidateSummaryV1,
    EvolutionCenterSnapshotV1,
    EvolutionGateSummaryV1,
    EvolutionRoutingSummaryV1,
)
from tianshu.models.principal import AuthContext
from tianshu.storage.evolution_repo import (
    EvolutionRepository,
    EvolutionRepositoryError,
)
from tianshu.storage.unit_of_work import SqliteUnitOfWork

EVOLUTION_NOT_ENABLED_REASON_CODE = "s5_governed_evolution_not_enabled"


class EvolutionCenterUnavailable(RuntimeError):
    """The authoritative Evolution Center source could not be read."""


class _Storage(Protocol):
    def unit_of_work(self) -> SqliteUnitOfWork: ...


class _GateReader(Protocol):
    def get_current_report_current(
        self, connection: sqlite3.Connection, candidate_id: str
    ) -> EvolutionGateReportV1 | None: ...


def _gate_status(status: GateStatus) -> Literal["passed", "failed", "error", "missing"]:
    if status is GateStatus.PASSED:
        return "passed"
    if status is GateStatus.BLOCKED:
        return "failed"
    if status is GateStatus.ERROR:
        return "error"
    return "missing"


def _rollback_state(
    lifecycle: CandidateLifecycle,
) -> Literal["not_required", "ready", "pending", "completed", "failed"]:
    if lifecycle is CandidateLifecycle.ROLLBACK_PENDING:
        return "pending"
    if lifecycle is CandidateLifecycle.ROLLED_BACK:
        return "completed"
    if lifecycle in {CandidateLifecycle.CANARY, CandidateLifecycle.PROMOTED}:
        return "ready"
    return "not_required"


class EvolutionCenterQueryService:
    """Project current governed evolution rows without inventing unavailable evidence."""

    def __init__(
        self,
        storage: _Storage | None = None,
        gate_reader: _GateReader | None = None,
    ) -> None:
        self._storage = storage
        self._gate_reader = gate_reader
        self._repository = EvolutionRepository()

    def get_snapshot(self, auth: AuthContext) -> EvolutionCenterSnapshotV1:
        del auth
        if self._storage is None or self._gate_reader is None:
            return self._disabled_snapshot()
        try:
            return self._read_snapshot()
        except (EvolutionRepositoryError, sqlite3.Error, TypeError, ValueError) as exc:
            raise EvolutionCenterUnavailable("governed evolution read failed") from exc

    @staticmethod
    def _disabled_snapshot() -> EvolutionCenterSnapshotV1:
        return EvolutionCenterSnapshotV1(
            status="not_enabled",
            reason_code=EVOLUTION_NOT_ENABLED_REASON_CODE,
            candidates=(),
            routing=(),
            last_gate_hash=None,
        )

    def _read_snapshot(self) -> EvolutionCenterSnapshotV1:
        assert self._storage is not None
        assert self._gate_reader is not None
        summaries: list[EvolutionCandidateSummaryV1] = []
        routing: list[EvolutionRoutingSummaryV1] = []
        reports: list[tuple[EvolutionCandidateV1, EvolutionGateReportV1]] = []
        degraded = False
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            rows = connection.execute(
                """SELECT candidate_id
                   FROM evolution_candidates
                   ORDER BY updated_at DESC, candidate_id
                   LIMIT 100"""
            ).fetchall()
            for row in rows:
                candidate = self._repository.get_candidate(connection, row["candidate_id"])
                if candidate is None:  # pragma: no cover - row came from the same read transaction
                    degraded = True
                    continue
                try:
                    report = (
                        self._gate_reader.get_current_report_current(
                            connection, candidate.candidate_id
                        )
                        if candidate.gate_snapshot_version > 0
                        else None
                    )
                except EvolutionRepositoryError:
                    report = None
                    degraded = True
                summaries.append(self._candidate_summary(candidate, report))
                current_routing = self._routing_summary(connection, candidate)
                if current_routing is not None:
                    routing.append(current_routing)
                if report is not None:
                    reports.append((candidate, report))
            unit_of_work.commit()

        if degraded:
            reason_code = "evolution_source_degraded"
            status: Literal["enabled", "degraded"] = "degraded"
        else:
            status = "enabled"
            reason_code = self._reason_code(summaries, reports)
        return EvolutionCenterSnapshotV1(
            status=status,
            reason_code=reason_code,
            candidates=tuple(summaries),
            routing=tuple(routing),
            last_gate_hash=reports[0][1].report_hash if reports else None,
        )

    @staticmethod
    def _candidate_summary(
        candidate: EvolutionCandidateV1,
        report: EvolutionGateReportV1 | None,
    ) -> EvolutionCandidateSummaryV1:
        gates = (
            tuple(
                EvolutionGateSummaryV1(
                    code=result.gate.value,
                    status=_gate_status(result.status),
                    blocking=result.status is not GateStatus.PASSED,
                )
                for result in report.results
            )
            if report is not None
            else ()
        )
        return EvolutionCandidateSummaryV1(
            candidate_id=candidate.candidate_id,
            kind=candidate.kind.value,
            version=candidate.version,
            lifecycle=candidate.lifecycle.value,
            artifact_hash=candidate.candidate.artifact_digest,
            promotion_allowed=report.promotion_allowed if report is not None else False,
            rollback_state=_rollback_state(candidate.lifecycle),
            gates=gates,
        )

    @staticmethod
    def _routing_summary(
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
    ) -> EvolutionRoutingSummaryV1 | None:
        row = connection.execute(
            """SELECT routing_version, allocation_basis_points
               FROM evolution_routing_allocations
               WHERE candidate_id=?""",
            (candidate.candidate_id,),
        ).fetchone()
        if row is None:
            return None
        counts = connection.execute(
            """SELECT
                   COALESCE(SUM(CASE WHEN selected_ref_json=champion_ref_json THEN 1 ELSE 0 END), 0)
                       AS champion_count,
                   COALESCE(SUM(CASE WHEN selected_ref_json<>champion_ref_json THEN 1 ELSE 0 END), 0)
                       AS challenger_count
               FROM run_evolution_assignments
               WHERE candidate_id=? AND routing_version=?""",
            (candidate.candidate_id, row["routing_version"]),
        ).fetchone()
        return EvolutionRoutingSummaryV1(
            candidate_id=candidate.candidate_id,
            routing_version=row["routing_version"],
            allocation_percent=row["allocation_basis_points"] / 100,
            champion_assignment_count=counts["champion_count"],
            challenger_assignment_count=counts["challenger_count"],
        )

    @staticmethod
    def _reason_code(
        summaries: list[EvolutionCandidateSummaryV1],
        reports: list[tuple[EvolutionCandidateV1, EvolutionGateReportV1]],
    ) -> str:
        if not summaries:
            return "enabled_no_candidates"
        blocking_results = [
            result
            for _candidate, report in reports
            for result in report.results
            if result.status is not GateStatus.PASSED
        ]
        if blocking_results and all(
            result.reason_code == "evidence_missing" for result in blocking_results
        ):
            return "evidence_blocking"
        if blocking_results:
            return "gate_blocking"
        return "enabled"


__all__ = [
    "EVOLUTION_NOT_ENABLED_REASON_CODE",
    "EvolutionCenterQueryService",
    "EvolutionCenterUnavailable",
]
