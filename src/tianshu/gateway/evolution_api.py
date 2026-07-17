"""Authenticated read-only Evolution Center endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from tianshu.application.evolution_view import (
    EvolutionCenterQueryService,
    EvolutionCenterUnavailable,
)
from tianshu.evolution.gates import GateEvaluator
from tianshu.gateway.auth import get_auth_context
from tianshu.storage.evolution_repo import EvolutionRepositoryConflict

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


__all__ = ["evolution_router"]
