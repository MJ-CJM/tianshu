"""Authenticated Control Center aggregate read endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from tianshu.application.control_center import (
    ControlCenterQueryService,
    ControlCenterUnavailable,
)
from tianshu.gateway.auth import get_auth_context

control_center_router = APIRouter(prefix="/control", tags=["control-center"])


@control_center_router.get("")
def get_control_center(request: Request) -> dict[str, object]:
    context = get_auth_context(request)
    service: ControlCenterQueryService = request.app.state.control_center_service
    try:
        snapshot = service.get_snapshot(context)
    except ControlCenterUnavailable as exc:
        raise HTTPException(
            503,
            {
                "code": "control_center_unavailable",
                "message": "control center sources are unavailable",
                "correlation_id": context.correlation_id,
            },
        ) from exc
    return {
        "data": snapshot.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


__all__ = ["control_center_router"]
