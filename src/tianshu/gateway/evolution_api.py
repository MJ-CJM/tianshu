"""Authenticated read-only Evolution Center endpoint."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

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
from tianshu.storage.evolution_repo import EvolutionRepository, EvolutionRepositoryConflict

evolution_router = APIRouter(prefix="/evolution", tags=["evolution-center"])


class _EvaluateGateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    expected_version: int = Field(ge=1)


@evolution_router.get("")
def get_evolution_center(request: Request) -> dict[str, object]:
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
    return {
        "data": snapshot.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


@evolution_router.get("/runs/{memorial_id}/assignment")
def get_run_evolution_assignment(memorial_id: str, request: Request) -> dict[str, object]:
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
        if owner is None or owner["submitter"] != context.principal.id:
            raise HTTPException(
                404,
                {
                    "code": "run_assignment_not_found",
                    "correlation_id": context.correlation_id,
                },
            )
        loaded = EvolutionRepository().get_assignment(connection, memorial_id)
        if loaded is None:
            raise HTTPException(
                404,
                {
                    "code": "run_assignment_not_found",
                    "correlation_id": context.correlation_id,
                },
            )
        assignment, overlay = loaded
        unit_of_work.commit()
    return {
        "data": {
            "assignment": assignment.model_dump(mode="json"),
            "effective_overlay": overlay.model_dump(mode="json"),
        },
        "correlation_id": context.correlation_id,
    }


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
