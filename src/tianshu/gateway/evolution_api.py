"""Authenticated read-only Evolution Center endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from tianshu.application.evolution_view import (
    EvolutionCenterQueryService,
    EvolutionCenterUnavailable,
)
from tianshu.gateway.auth import get_auth_context

evolution_router = APIRouter(prefix="/evolution", tags=["evolution-center"])


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


__all__ = ["evolution_router"]
