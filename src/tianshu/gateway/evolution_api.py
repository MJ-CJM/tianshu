"""Authenticated read-only Evolution Center endpoint."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.application.evolution_view import (
    EvolutionCenterQueryService,
    EvolutionCenterUnavailable,
)
from tianshu.evolution.gates import GateEvaluator
from tianshu.evolution.promotion import (
    PromoteCommand,
    PromotionAuthorizationError,
    PromotionConflict,
    PromotionService,
    RollbackCommand,
    StartCanaryCommand,
)
from tianshu.gateway.auth import get_auth_context
from tianshu.models.events import make_event
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.evolution_policy import EvolutionPolicyMode, EvolutionPolicyV1
from tianshu.models.evolution_view import EvolutionCenterSnapshotV1
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentSetV1,
    RunAssignmentV1,
)
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.evolution_policy_repo import (
    EvolutionPolicyConflict,
    EvolutionPolicyRepository,
    EvolutionPolicyRepositoryError,
)
from tianshu.storage.evolution_repo import (
    EvolutionRepository,
    EvolutionRepositoryConflict,
    EvolutionRepositoryDecodeError,
)
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository

evolution_router = APIRouter(prefix="/evolution", tags=["evolution-center"])


class _EvaluateGateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    expected_version: int = Field(ge=1)


class RunSystemSnapshotViewV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    components: dict[str, str]
    generation_ids: tuple[str, ...]


class RunEvolutionAssignmentViewV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    assignment: RunAssignmentV1 | LegacyRunAssignmentV1
    effective_overlay: EffectiveEvolutionOverlayV1 | None
    assignment_set: RunAssignmentSetV1 | None
    system_snapshot: RunSystemSnapshotViewV1 | None


class RunEvolutionAssignmentResponseV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data: RunEvolutionAssignmentViewV1
    correlation_id: str


class EvolutionCenterResponseV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data: EvolutionCenterSnapshotV1
    correlation_id: str


class UpsertEvolutionPolicyRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: CandidateKind
    mode: EvolutionPolicyMode
    max_canary_basis_points: int = Field(ge=0, le=1_000)
    expected_version: int | None = Field(ge=1)

    @field_validator("kind", mode="before")
    @classmethod
    def parse_kind(cls, value: object) -> object:
        return CandidateKind(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_canary_allocation(self) -> UpsertEvolutionPolicyRequestV1:
        if self.mode == "canary" and self.max_canary_basis_points == 0:
            raise ValueError("canary mode requires a positive max_canary_basis_points")
        return self


class EvolutionPolicyResponseV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data: EvolutionPolicyV1
    correlation_id: str


class EvolutionPolicyListResponseV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data: tuple[EvolutionPolicyV1, ...]
    correlation_id: str


_SubjectKey = Annotated[
    str,
    Path(min_length=1, max_length=512, pattern=r".*\S.*"),
]
_EVOLUTION_POLICY_EVENT = "evolution_policy_updated"


def _raise_evolution_policy_error(
    error: EvolutionPolicyRepositoryError,
    correlation_id: str,
) -> NoReturn:
    code = (
        error.reason_code
        if isinstance(error, EvolutionPolicyConflict)
        else "evolution_policy_decode_error"
    )
    raise HTTPException(409, {"code": code, "correlation_id": correlation_id}) from error


def _record_evolution_policy_update(
    connection: sqlite3.Connection,
    *,
    previous: EvolutionPolicyV1 | None,
    current: EvolutionPolicyV1,
    principal_id: str,
    correlation_id: str,
) -> None:
    _append_system_audit_unlocked(
        connection,
        AppendSystemAuditRequest(
            correlation_id=correlation_id,
            actor_digest=hashlib.sha256(principal_id.encode()).hexdigest(),
            action=_EVOLUTION_POLICY_EVENT,
            outcome="succeeded",
            reason_code=_EVOLUTION_POLICY_EVENT,
            subject_kind="evolution_policy",
            subject_digest=hashlib.sha256(current.subject_key.encode()).hexdigest(),
            metadata={
                "candidate_kind": current.kind.value,
                "old_mode": previous.mode if previous is not None else None,
                "new_mode": current.mode,
                "old_canary_basis_points": (
                    previous.max_canary_basis_points if previous is not None else None
                ),
                "new_canary_basis_points": current.max_canary_basis_points,
                "old_version": previous.version if previous is not None else None,
                "new_version": current.version,
            },
        ),
    )
    OutboxRepository().add(
        connection,
        make_event(
            event_type=_EVOLUTION_POLICY_EVENT,
            producer="evolution_policy_api",
            payload={
                "subject_key": current.subject_key,
                "kind": current.kind.value,
                "old_mode": previous.mode if previous is not None else None,
                "new_mode": current.mode,
                "old_canary_basis_points": (
                    previous.max_canary_basis_points if previous is not None else None
                ),
                "new_canary_basis_points": current.max_canary_basis_points,
                "old_version": previous.version if previous is not None else None,
                "new_version": current.version,
                "correlation_id": correlation_id,
            },
        ),
    )


@evolution_router.get("", response_model=EvolutionCenterResponseV1)
def get_evolution_center(request: Request) -> EvolutionCenterResponseV1:
    context = get_auth_context(request)
    service: EvolutionCenterQueryService = request.app.state.evolution_center_service
    try:
        snapshot = service.get_snapshot(context)
    except EvolutionCenterUnavailable as exc:
        raise HTTPException(
            503,
            {
                "code": "evolution_center_unavailable",
                "message": "evolution center source is unavailable",
                "correlation_id": context.correlation_id,
            },
        ) from exc
    routing_enabled = bool(getattr(request.app.state.settings, "evolution_routing_enabled", True))
    return EvolutionCenterResponseV1(
        data=snapshot.model_copy(update={"routing_enabled": routing_enabled}),
        correlation_id=context.correlation_id,
    )


@evolution_router.get(
    "/runs/{memorial_id}/assignment",
    response_model=RunEvolutionAssignmentResponseV1,
)
def get_run_evolution_assignment(
    memorial_id: str,
    request: Request,
) -> RunEvolutionAssignmentResponseV1:
    context = get_auth_context(request)
    storage = request.app.state.storage
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        owner = connection.execute(
            """SELECT edict.submitter
               FROM memorials AS memorial
               JOIN edicts AS edict ON edict.id=memorial.edict_id
               WHERE memorial.id=?""",
            (memorial_id,),
        ).fetchone()
        is_admin = "admin" in context.principal.scopes
        if owner is None or (not is_admin and owner["submitter"] != context.principal.id):
            raise HTTPException(
                404,
                {
                    "code": "run_assignment_not_found",
                    "correlation_id": context.correlation_id,
                },
            )
        repository = EvolutionRepository()
        try:
            loaded = repository.get_assignment(connection, memorial_id)
            assignment_set = repository.get_assignment_set(connection, memorial_id)
            repository.validate_assignment_projection(loaded, assignment_set)
        except (EvolutionRepositoryConflict, EvolutionRepositoryDecodeError) as exc:
            raise HTTPException(
                503,
                {
                    "code": "run_assignment_unavailable",
                    "correlation_id": context.correlation_id,
                },
            ) from exc
        if loaded is None:
            raise HTTPException(
                404,
                {
                    "code": "run_assignment_not_found",
                    "correlation_id": context.correlation_id,
                },
            )
        assignment, overlay = loaded
        binding = SystemSnapshotRepository().get_last_binding(connection, memorial_id)
        unit_of_work.commit()
    return RunEvolutionAssignmentResponseV1(
        data=RunEvolutionAssignmentViewV1(
            assignment=assignment,
            effective_overlay=overlay,
            assignment_set=assignment_set,
            system_snapshot=(
                RunSystemSnapshotViewV1(
                    digest=binding.snapshot.digest,
                    components=binding.snapshot.components,
                    generation_ids=binding.generation_ids,
                )
                if binding is not None
                else None
            ),
        ),
        correlation_id=context.correlation_id,
    )


@evolution_router.get(
    "/policies",
    response_model=EvolutionPolicyListResponseV1,
)
def list_evolution_policies(request: Request) -> EvolutionPolicyListResponseV1:
    context = get_auth_context(request)
    repository = EvolutionPolicyRepository()
    try:
        with request.app.state.storage.unit_of_work() as unit_of_work:
            rows = unit_of_work.connection.execute(
                "SELECT subject_key FROM evolution_policies ORDER BY subject_key"
            ).fetchall()
            loaded_policies: list[EvolutionPolicyV1] = []
            for row in rows:
                policy = repository.get_policy(unit_of_work.connection, row["subject_key"])
                if policy is None:  # pragma: no cover - same transaction read preserves the row
                    raise EvolutionPolicyRepositoryError("evolution policy row disappeared")
                loaded_policies.append(policy)
            unit_of_work.commit()
    except EvolutionPolicyRepositoryError as exc:
        _raise_evolution_policy_error(exc, context.correlation_id)
    return EvolutionPolicyListResponseV1(
        data=tuple(loaded_policies),
        correlation_id=context.correlation_id,
    )


@evolution_router.get(
    "/policies/{subject_key}",
    response_model=EvolutionPolicyResponseV1,
)
def get_evolution_policy(
    subject_key: _SubjectKey,
    request: Request,
) -> EvolutionPolicyResponseV1:
    context = get_auth_context(request)
    repository = EvolutionPolicyRepository()
    try:
        with request.app.state.storage.unit_of_work() as unit_of_work:
            policy = repository.get_policy(unit_of_work.connection, subject_key)
            unit_of_work.commit()
    except EvolutionPolicyRepositoryError as exc:
        _raise_evolution_policy_error(exc, context.correlation_id)
    if policy is None:
        raise HTTPException(
            404,
            {
                "code": "evolution_policy_not_found",
                "correlation_id": context.correlation_id,
            },
        )
    return EvolutionPolicyResponseV1(data=policy, correlation_id=context.correlation_id)


@evolution_router.put(
    "/policies/{subject_key}",
    response_model=EvolutionPolicyResponseV1,
)
def put_evolution_policy(
    subject_key: _SubjectKey,
    body: UpsertEvolutionPolicyRequestV1,
    request: Request,
) -> EvolutionPolicyResponseV1:
    context = get_auth_context(request)
    repository = EvolutionPolicyRepository()
    requested = EvolutionPolicyV1(
        subject_key=subject_key,
        kind=body.kind,
        mode=body.mode,
        max_canary_basis_points=body.max_canary_basis_points,
        version=body.expected_version if body.expected_version is not None else 1,
        updated_at=datetime.now(UTC),
    )
    try:
        with request.app.state.storage.unit_of_work() as unit_of_work:
            previous = repository.get_policy(unit_of_work.connection, subject_key)
            durable = repository.upsert_policy(
                unit_of_work.connection,
                requested,
                expected_version=body.expected_version,
            )
            _record_evolution_policy_update(
                unit_of_work.connection,
                previous=previous,
                current=durable,
                principal_id=context.principal.id,
                correlation_id=context.correlation_id,
            )
            unit_of_work.commit()
    except EvolutionPolicyRepositoryError as exc:
        _raise_evolution_policy_error(exc, context.correlation_id)
    return EvolutionPolicyResponseV1(data=durable, correlation_id=context.correlation_id)


@evolution_router.get("/candidates/{candidate_id}")
def get_evolution_candidate(candidate_id: str, request: Request) -> dict[str, object]:
    context = get_auth_context(request)
    evaluator: GateEvaluator = request.app.state.evolution_gate_evaluator
    candidate = evaluator.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(404, {"code": "candidate_not_found"})
    return {
        "data": candidate.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


@evolution_router.get("/candidates/{candidate_id}/gate")
def get_evolution_gate(candidate_id: str, request: Request) -> dict[str, object]:
    context = get_auth_context(request)
    evaluator: GateEvaluator = request.app.state.evolution_gate_evaluator
    try:
        report = evaluator.get_current_report(candidate_id)
    except EvolutionRepositoryConflict as exc:
        raise HTTPException(409, {"code": "gate_snapshot_conflict"}) from exc
    if report is None:
        raise HTTPException(404, {"code": "gate_snapshot_not_found"})
    return {
        "data": report.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


@evolution_router.post("/candidates/{candidate_id}/gate/evaluate")
def evaluate_evolution_gate(
    candidate_id: str,
    body: _EvaluateGateRequest,
    request: Request,
) -> dict[str, object]:
    context = get_auth_context(request)
    evaluator: GateEvaluator = request.app.state.evolution_gate_evaluator
    try:
        report = evaluator.evaluate(candidate_id, expected_version=body.expected_version)
    except EvolutionRepositoryConflict as exc:
        raise HTTPException(409, {"code": "candidate_version_conflict"}) from exc
    return {
        "data": report.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


def _promotion_service(request: Request) -> PromotionService:
    service = getattr(request.app.state, "promotion_service", None)
    if not isinstance(service, PromotionService):
        raise HTTPException(503, {"code": "promotion_service_unavailable"})
    return service


def _promotion_response(receipt: BaseModel, correlation_id: str) -> dict[str, object]:
    return {"data": receipt.model_dump(mode="json"), "correlation_id": correlation_id}


def _raise_promotion_error(exc: Exception) -> NoReturn:
    if isinstance(exc, PromotionAuthorizationError):
        raise HTTPException(403, {"code": str(exc)}) from exc
    if isinstance(exc, PromotionConflict):
        raise HTTPException(409, {"code": str(exc)}) from exc
    raise exc


@evolution_router.post("/candidates/{candidate_id}/canary")
def start_candidate_canary(
    candidate_id: str,
    body: StartCanaryCommand,
    request: Request,
) -> dict[str, object]:
    context = get_auth_context(request)
    try:
        receipt = _promotion_service(request).start_canary(candidate_id, body, auth=context)
    except (PromotionAuthorizationError, PromotionConflict) as exc:
        _raise_promotion_error(exc)
    return _promotion_response(receipt, context.correlation_id)


@evolution_router.post("/candidates/{candidate_id}/promote")
def promote_candidate(
    candidate_id: str,
    body: PromoteCommand,
    request: Request,
) -> dict[str, object]:
    context = get_auth_context(request)
    try:
        receipt = _promotion_service(request).promote(candidate_id, body, auth=context)
    except (PromotionAuthorizationError, PromotionConflict) as exc:
        _raise_promotion_error(exc)
    return _promotion_response(receipt, context.correlation_id)


@evolution_router.post("/candidates/{candidate_id}/rollback")
def rollback_candidate(
    candidate_id: str,
    body: RollbackCommand,
    request: Request,
) -> dict[str, object]:
    context = get_auth_context(request)
    try:
        receipt = _promotion_service(request).rollback(candidate_id, body, auth=context)
    except (PromotionAuthorizationError, PromotionConflict) as exc:
        _raise_promotion_error(exc)
    return _promotion_response(receipt, context.correlation_id)


__all__ = ["evolution_router"]
