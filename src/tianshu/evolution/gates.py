"""Fail-closed gate reports for governed evolution candidates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.events import make_event
from tianshu.models.evidence import ArtifactRefV1, ClosedEvidenceBundleV1
from tianshu.models.evolution_candidate import (
    CandidateLifecycle,
    EvolutionCandidateV1,
    GateName,
    validate_lifecycle_transition,
)
from tianshu.models.evolution_gate import (
    REQUIRED_GATES,
    EvolutionGateReportV1,
    EvolutionGateResultV1,
    GateStatus,
)
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.artifact_repo import (
    ArtifactRepository,
    ArtifactRepositoryError,
    EvidenceRepository,
    EvidenceRepositoryError,
)
from tianshu.storage.evolution_repo import (
    EvolutionRepository,
    EvolutionRepositoryConflict,
)
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class _ArtifactVerifier(Protocol):
    def verify_ref_current(self, artifact: ArtifactRefV1) -> bool: ...


class _LifecycleJournalEntryV1(_StrictModel):
    schema_version: Literal[1]
    candidate_id: str
    candidate_version: int = Field(ge=1)
    from_lifecycle: CandidateLifecycle | None
    to_lifecycle: CandidateLifecycle
    decision_request_id: str | None
    created_at: datetime


class GateEvaluator:
    """Re-derive and persist one current fail-closed gate snapshot."""

    def __init__(
        self,
        storage: _Storage,
        *,
        artifact_verifier: _ArtifactVerifier | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._artifact_verifier = artifact_verifier
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = EvolutionRepository()
        self._outbox = OutboxRepository()

    def evaluate(
        self,
        candidate_id: str,
        *,
        expected_version: int,
        additional_evidence_bundle_ids: tuple[str, ...] = (),
    ) -> EvolutionGateReportV1:
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            current = self._repository.get_candidate(connection, candidate_id)
            if current is None or current.version != expected_version:
                raise EvolutionRepositoryConflict("evolution candidate compare-and-swap conflict")
            if current.lifecycle not in {
                CandidateLifecycle.STAGED,
                CandidateLifecycle.BLOCKED,
                CandidateLifecycle.EVALUATING,
            }:
                raise EvolutionRepositoryConflict("candidate is not gate-evaluable")
            if (
                additional_evidence_bundle_ids
                and current.lifecycle is CandidateLifecycle.EVALUATING
            ):
                raise EvolutionRepositoryConflict(
                    "evidence can only bind while entering gate evaluation"
                )
            evidence_bundle_ids = current.evidence_bundle_ids + additional_evidence_bundle_ids
            if len(evidence_bundle_ids) != len(set(evidence_bundle_ids)):
                raise EvolutionRepositoryConflict("candidate evidence bundle ids must be unique")
            evaluating = current
            if current.lifecycle is not CandidateLifecycle.EVALUATING:
                evaluating = self._repository.save_candidate(
                    connection,
                    current.model_copy(
                        update={
                            "evidence_bundle_ids": evidence_bundle_ids,
                            "lifecycle": CandidateLifecycle.EVALUATING,
                            "updated_at": self._now(),
                        }
                    ),
                    expected_version=current.version,
                )
            results = self._derive_results(
                connection,
                evaluating,
                binding_version=current.version,
                evidence_not_before=current.updated_at,
                artifact_verifier=self._artifact_verifier,
            )
            snapshot_version = evaluating.gate_snapshot_version + 1
            report = EvolutionGateReportV1.from_results(
                candidate_id=evaluating.candidate_id,
                candidate_version=evaluating.version + 1,
                candidate_digest=evaluating.candidate.artifact_digest,
                gate_snapshot_version=snapshot_version,
                results=results,
                evidence_bundle_ids=evaluating.evidence_bundle_ids,
                evaluated_at=self._now(),
            )
            lifecycle = (
                CandidateLifecycle.READY if report.promotion_allowed else CandidateLifecycle.BLOCKED
            )
            durable = self._repository.save_candidate(
                connection,
                evaluating.model_copy(
                    update={
                        "gate_snapshot_version": snapshot_version,
                        "lifecycle": lifecycle,
                        "updated_at": self._now(),
                    }
                ),
                expected_version=evaluating.version,
            )
            if durable.version != report.candidate_version:
                raise EvolutionRepositoryConflict("gate snapshot candidate version mismatch")
            self._insert_snapshot(connection, report)
            self._append_audit_and_outbox(connection, report)
            unit_of_work.commit()
            return report

    def get_candidate(self, candidate_id: str) -> EvolutionCandidateV1 | None:
        with self._storage.unit_of_work() as unit_of_work:
            candidate = self._repository.get_candidate(unit_of_work.connection, candidate_id)
            unit_of_work.commit()
            return candidate

    def get_current_report(self, candidate_id: str) -> EvolutionGateReportV1 | None:
        with self._storage.unit_of_work() as unit_of_work:
            report = self.get_current_report_current(unit_of_work.connection, candidate_id)
            unit_of_work.commit()
            return report

    def get_current_report_current(
        self, connection: sqlite3.Connection, candidate_id: str
    ) -> EvolutionGateReportV1 | None:
        """Validate the current candidate-bound snapshot inside the caller's UoW."""

        candidate = self._repository.get_candidate(connection, candidate_id)
        if candidate is None or candidate.gate_snapshot_version == 0:
            return None
        report = self._load_bound_report_current(
            connection,
            candidate_id,
            gate_snapshot_version=candidate.gate_snapshot_version,
        )
        if (
            report.candidate_version != candidate.version
            or report.candidate_digest != candidate.candidate.artifact_digest
            or report.gate_snapshot_version != candidate.gate_snapshot_version
        ):
            raise EvolutionRepositoryConflict("current gate snapshot is stale")
        self._validate_green_evidence_current(connection, candidate, report)
        return report

    def get_latest_compatible_report_current(
        self, connection: sqlite3.Connection, candidate_id: str
    ) -> EvolutionGateReportV1 | None:
        """Read the immutable gate that governs the current lifecycle lineage."""

        candidate = self._repository.get_candidate(connection, candidate_id)
        if candidate is None or candidate.gate_snapshot_version == 0:
            return None
        report = self._load_bound_report_current(
            connection,
            candidate_id,
            gate_snapshot_version=candidate.gate_snapshot_version,
        )
        if (
            report.candidate_id != candidate.candidate_id
            or report.candidate_digest != candidate.candidate.artifact_digest
            or report.gate_snapshot_version != candidate.gate_snapshot_version
            or report.evidence_bundle_ids != candidate.evidence_bundle_ids
            or report.candidate_version > candidate.version
        ):
            raise EvolutionRepositoryConflict("compatible gate snapshot is stale")
        if report.candidate_version != candidate.version:
            self._validate_lifecycle_lineage_current(connection, candidate, report)
        self._validate_green_evidence_current(connection, candidate, report)
        return report

    @staticmethod
    def _validate_lifecycle_lineage_current(
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        report: EvolutionGateReportV1,
    ) -> None:
        if not report.promotion_allowed:
            raise EvolutionRepositoryConflict("blocked gate cannot govern a later lifecycle")
        rows = connection.execute(
            """SELECT journal_id, candidate_id, candidate_version, from_lifecycle,
                      to_lifecycle, decision_request_id, entry_json, entry_hash, created_at
               FROM evolution_lifecycle_journal
               WHERE candidate_id=? AND candidate_version BETWEEN ? AND ?
               ORDER BY candidate_version""",
            (candidate.candidate_id, report.candidate_version, candidate.version),
        ).fetchall()
        expected_versions = tuple(range(report.candidate_version, candidate.version + 1))
        if tuple(row["candidate_version"] for row in rows) != expected_versions:
            raise EvolutionRepositoryConflict("compatible gate lifecycle lineage is incomplete")
        previous: CandidateLifecycle | None = None
        for index, row in enumerate(rows):
            raw = row["entry_json"]
            if (
                not isinstance(raw, str)
                or hashlib.sha256(raw.encode()).hexdigest() != row["entry_hash"]
            ):
                raise EvolutionRepositoryConflict("compatible gate lifecycle journal is corrupt")
            try:
                entry = _LifecycleJournalEntryV1.model_validate_json(raw)
            except (TypeError, ValueError, ValidationError) as exc:
                raise EvolutionRepositoryConflict(
                    "compatible gate lifecycle journal is corrupt"
                ) from exc
            expected_id = hashlib.sha256(
                f"{entry.candidate_id}:{entry.candidate_version}:{entry.to_lifecycle.value}".encode()
            ).hexdigest()
            canonical_entry = entry.model_dump(mode="json")
            canonical_entry["created_at"] = entry.created_at.isoformat()
            if (
                canonical_json_bytes(canonical_entry).decode("utf-8") != raw
                or row["journal_id"] != expected_id
                or row["candidate_id"] != entry.candidate_id
                or row["candidate_version"] != entry.candidate_version
                or row["from_lifecycle"]
                != (entry.from_lifecycle.value if entry.from_lifecycle is not None else None)
                or row["to_lifecycle"] != entry.to_lifecycle.value
                or row["decision_request_id"] != entry.decision_request_id
                or row["created_at"] != entry.created_at.isoformat()
            ):
                raise EvolutionRepositoryConflict("compatible gate lifecycle journal conflicts")
            if index == 0:
                if (
                    entry.from_lifecycle is not CandidateLifecycle.EVALUATING
                    or entry.to_lifecycle is not CandidateLifecycle.READY
                ):
                    raise EvolutionRepositoryConflict(
                        "compatible gate does not begin at ready lifecycle"
                    )
                try:
                    validate_lifecycle_transition(entry.from_lifecycle, entry.to_lifecycle)
                except ValueError as exc:
                    raise EvolutionRepositoryConflict(
                        "compatible gate lifecycle lineage is illegal"
                    ) from exc
            else:
                if entry.from_lifecycle is not previous:
                    raise EvolutionRepositoryConflict("compatible gate lifecycle lineage conflicts")
                try:
                    validate_lifecycle_transition(entry.from_lifecycle, entry.to_lifecycle)
                except ValueError as exc:
                    raise EvolutionRepositoryConflict(
                        "compatible gate lifecycle lineage is illegal"
                    ) from exc
            previous = entry.to_lifecycle
        if previous is not candidate.lifecycle:
            raise EvolutionRepositoryConflict("compatible gate lifecycle does not reach candidate")

    def validate_bound_green_report_current(
        self,
        connection: sqlite3.Connection,
        candidate_id: str,
        *,
        candidate_version: int,
        gate_snapshot_version: int,
        candidate_digest: str,
        report_hash: str,
    ) -> EvolutionGateReportV1:
        """Revalidate a pre-canary green binding without minting a new snapshot."""

        candidate = self._repository.get_candidate(connection, candidate_id)
        if candidate is None:
            raise EvolutionRepositoryConflict("bound gate candidate is missing")
        report = self._load_bound_report_current(
            connection,
            candidate_id,
            gate_snapshot_version=gate_snapshot_version,
        )
        if (
            not report.promotion_allowed
            or report.candidate_version != candidate_version
            or report.candidate_digest != candidate_digest
            or report.gate_snapshot_version != gate_snapshot_version
            or report.report_hash != report_hash
            or candidate.candidate.artifact_digest != candidate_digest
            or candidate.gate_snapshot_version != gate_snapshot_version
            or candidate.evidence_bundle_ids != report.evidence_bundle_ids
        ):
            raise EvolutionRepositoryConflict("bound green gate snapshot is stale")
        self._validate_green_evidence_current(connection, candidate, report)
        return report

    @staticmethod
    def _load_bound_report_current(
        connection: sqlite3.Connection,
        candidate_id: str,
        *,
        gate_snapshot_version: int,
    ) -> EvolutionGateReportV1:
        row = connection.execute(
            """SELECT snapshot_json, snapshot_hash
               FROM evolution_gate_snapshots
               WHERE candidate_id=? AND gate_snapshot_version=?""",
            (candidate_id, gate_snapshot_version),
        ).fetchone()
        if row is None or not isinstance(row["snapshot_json"], str):
            raise EvolutionRepositoryConflict("current gate snapshot is missing")
        snapshot_json = row["snapshot_json"]
        if hashlib.sha256(snapshot_json.encode()).hexdigest() != row["snapshot_hash"]:
            raise EvolutionRepositoryConflict("current gate snapshot hash mismatch")
        try:
            return EvolutionGateReportV1.model_validate_json(snapshot_json)
        except (TypeError, ValueError, ValidationError) as exc:
            raise EvolutionRepositoryConflict("current gate snapshot is corrupt") from exc

    def _validate_green_evidence_current(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        report: EvolutionGateReportV1,
    ) -> None:
        if not report.promotion_allowed:
            return
        bundles: list[ClosedEvidenceBundleV1] = []
        try:
            for bundle_id in candidate.evidence_bundle_ids:
                bundle = EvidenceRepository.get_current(connection, bundle_id)
                if not isinstance(bundle, ClosedEvidenceBundleV1):
                    raise EvolutionRepositoryConflict(
                        "current gate snapshot evidence is no longer valid"
                    )
                bundles.append(bundle)
        except EvidenceRepositoryError as exc:
            raise EvolutionRepositoryConflict(
                "current gate snapshot evidence is no longer valid"
            ) from exc
        evidence_result = next(
            result for result in report.results if result.gate is GateName.EVIDENCE
        )
        if evidence_result.evidence_hashes != tuple(
            bundle.content_hash for bundle in bundles
        ) or not self._artifacts_are_valid(connection, bundles):
            raise EvolutionRepositoryConflict("current gate snapshot evidence is no longer valid")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("gate evaluator clock must be timezone-aware")
        return value.astimezone(UTC)

    def _derive_results(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        *,
        binding_version: int,
        evidence_not_before: datetime,
        artifact_verifier: _ArtifactVerifier | None,
    ) -> tuple[EvolutionGateResultV1, ...]:
        if not candidate.evidence_bundle_ids:
            return ()
        bundles: list[ClosedEvidenceBundleV1] = []
        for bundle_id in candidate.evidence_bundle_ids:
            try:
                bundle = EvidenceRepository.get_current(connection, bundle_id)
            except EvidenceRepositoryError:
                return (
                    EvolutionGateResultV1(
                        gate=GateName.EVIDENCE,
                        status=GateStatus.ERROR,
                        reason_code="evidence_bundle_corrupt",
                    ),
                )
            if bundle is None:
                return (
                    EvolutionGateResultV1(
                        gate=GateName.EVIDENCE,
                        status=GateStatus.MISSING,
                        reason_code="evidence_bundle_missing",
                    ),
                )
            if not isinstance(bundle, ClosedEvidenceBundleV1):
                return (
                    EvolutionGateResultV1(
                        gate=GateName.EVIDENCE,
                        status=GateStatus.BLOCKED,
                        reason_code="evidence_bundle_open",
                    ),
                )
            if bundle.closed_at < evidence_not_before:
                return (
                    EvolutionGateResultV1(
                        gate=GateName.EVIDENCE,
                        status=GateStatus.BLOCKED,
                        reason_code="evidence_bundle_stale",
                    ),
                )
            bundles.append(bundle)
        if not self._artifacts_are_valid(
            connection,
            bundles,
            artifact_verifier=artifact_verifier,
        ):
            return (
                EvolutionGateResultV1(
                    gate=GateName.EVIDENCE,
                    status=GateStatus.ERROR,
                    reason_code="evidence_artifact_invalid",
                ),
            )
        evidence_hashes = tuple(bundle.content_hash for bundle in bundles)
        checks = {check.name: check for bundle in bundles for check in bundle.snapshot.checks}
        binding_name = (
            f"evolution.candidate.{candidate.candidate_id}."
            f"{binding_version}.{candidate.candidate.artifact_digest}"
        )
        binding = checks.get(binding_name)
        if binding is None or binding.status != "passed":
            return (
                EvolutionGateResultV1(
                    gate=GateName.EVIDENCE,
                    status=GateStatus.BLOCKED,
                    reason_code="candidate_binding_mismatch",
                    evidence_hashes=evidence_hashes,
                ),
            )

        results: list[EvolutionGateResultV1] = []
        for gate in REQUIRED_GATES:
            if gate is GateName.EVIDENCE:
                results.append(
                    EvolutionGateResultV1(
                        gate=gate,
                        status=GateStatus.PASSED,
                        reason_code="evidence_verified",
                        evidence_hashes=evidence_hashes,
                    )
                )
                continue
            check = checks.get(f"evolution.gate.{gate.value}")
            if check is None:
                status = GateStatus.MISSING
                reason_code = "gate_check_missing"
            elif check.status == "passed":
                status = GateStatus.PASSED
                reason_code = "gate_check_passed"
            else:
                status = GateStatus.BLOCKED
                reason_code = f"gate_check_{check.status}"
            if gate is GateName.BUDGET and status is GateStatus.PASSED:
                costs = tuple(bundle.snapshot.cost for bundle in bundles)
                if any(
                    cost.effective_budget is not None and cost.actual_cost > cost.effective_budget
                    for cost in costs
                ):
                    status = GateStatus.BLOCKED
                    reason_code = "budget_exceeded"
            if gate is GateName.HUMAN_VETO and status is GateStatus.PASSED:
                vetoed = any(
                    decision.action in {"deny", "reject", "veto"}
                    for bundle in bundles
                    for decision in bundle.snapshot.decisions
                    if decision.kind == "governed_apply"
                )
                if vetoed:
                    status = GateStatus.BLOCKED
                    reason_code = "human_veto_present"
            results.append(
                EvolutionGateResultV1(
                    gate=gate,
                    status=status,
                    reason_code=reason_code,
                    evidence_hashes=evidence_hashes,
                )
            )
        return tuple(results)

    def _artifacts_are_valid(
        self,
        connection: sqlite3.Connection,
        bundles: list[ClosedEvidenceBundleV1],
        *,
        artifact_verifier: _ArtifactVerifier | None = None,
    ) -> bool:
        verifier = artifact_verifier or self._artifact_verifier
        for bundle in bundles:
            for artifact in bundle.snapshot.artifacts:
                try:
                    durable = ArtifactRepository.get_current(connection, artifact.digest)
                    if (
                        durable != artifact
                        or verifier is None
                        or not verifier.verify_ref_current(artifact)
                    ):
                        return False
                except (ArtifactRepositoryError, OSError, TypeError, ValueError):
                    return False
        return True

    @staticmethod
    def _insert_snapshot(connection: sqlite3.Connection, report: EvolutionGateReportV1) -> None:
        snapshot_json = canonical_json_bytes(report).decode("utf-8")
        try:
            connection.execute(
                """
                INSERT INTO evolution_gate_snapshots (
                    gate_snapshot_id, candidate_id, candidate_version,
                    gate_snapshot_version, snapshot_json, snapshot_hash,
                    evidence_bundle_ids_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"gate:{report.candidate_id}:{report.gate_snapshot_version}",
                    report.candidate_id,
                    report.candidate_version,
                    report.gate_snapshot_version,
                    snapshot_json,
                    hashlib.sha256(snapshot_json.encode()).hexdigest(),
                    json.dumps(
                        list(report.evidence_bundle_ids),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    report.evaluated_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise EvolutionRepositoryConflict("gate snapshot compare-and-swap conflict") from exc

    def _append_audit_and_outbox(
        self, connection: sqlite3.Connection, report: EvolutionGateReportV1
    ) -> None:
        identity = report.candidate_id
        correlation_id = f"gate:{hashlib.sha256(identity.encode()).hexdigest()}"
        _append_system_audit_unlocked(
            connection,
            AppendSystemAuditRequest(
                correlation_id=correlation_id,
                actor_digest=hashlib.sha256(b"tianshu.gate-evaluator").hexdigest(),
                action="evolution.gate.evaluated",
                outcome="succeeded",
                reason_code=("ready" if report.promotion_allowed else "blocked"),
                subject_kind="evolution_candidate",
                subject_digest=hashlib.sha256(identity.encode()).hexdigest(),
                metadata={
                    "blocking_gate_count": len(report.blocking_gates),
                    "gate_snapshot_version": report.gate_snapshot_version,
                },
            ),
        )
        self._outbox.add(
            connection,
            make_event(
                event_type="evolution.gate.evaluated",
                producer="gate_evaluator",
                payload={
                    "candidate_id": report.candidate_id,
                    "candidate_version": report.candidate_version,
                    "gate_snapshot_version": report.gate_snapshot_version,
                    "promotion_allowed": report.promotion_allowed,
                    "report_hash": report.report_hash,
                    "correlation_id": correlation_id,
                },
            ),
        )


class _Storage(Protocol):
    def unit_of_work(self) -> SqliteUnitOfWork: ...


__all__ = [
    "EvolutionGateReportV1",
    "EvolutionGateResultV1",
    "GateEvaluator",
    "GateStatus",
    "REQUIRED_GATES",
]
