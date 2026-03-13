"""Gateway API routes."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.models import (
    ApiResponse,
    Edict,
    EdictCreateRequest,
    Memorial,
    TaskStatus,
)

logger = logging.getLogger(__name__)

gateway_router = APIRouter()


@gateway_router.post("/edicts", response_model=ApiResponse)
async def create_edict(body: EdictCreateRequest, request: Request):
    storage = request.app.state.storage
    agent = request.app.state.agent
    settings = request.app.state.settings

    # Build Edict
    edict = Edict(goal=body.goal, context=body.context)
    storage.save_edict(edict)

    # Create Memorial (RUNNING)
    memorial = Memorial(
        edict_id=edict.id,
        status=TaskStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    storage.save_memorial(memorial)
    storage.append_event(edict.id, memorial.id, "edict.submitted", {"goal": edict.goal})

    try:
        result = await asyncio.wait_for(
            agent.execute(edict),
            timeout=settings.agent_timeout_seconds,
        )
        memorial.status = result.status
        memorial.summary = result.summary
        memorial.result = result.result
        memorial.usage = result.usage
        memorial.error = result.error
        memorial.completed_at = datetime.now(UTC)

        for event in result.events:
            storage.append_event(edict.id, memorial.id, event["type"], event)

        event_type = {
            TaskStatus.COMPLETED: "execution.completed",
            TaskStatus.FAILED: "execution.failed",
            TaskStatus.CANCELLED: "execution.cancelled",
        }.get(result.status, "execution.failed")

        storage.append_event(edict.id, memorial.id, event_type, {
            "status": result.status.value,
            "error": result.error,
        })

    except asyncio.TimeoutError:
        memorial.status = TaskStatus.FAILED
        memorial.error = f"Execution timed out after {settings.agent_timeout_seconds}s"
        memorial.completed_at = datetime.now(UTC)
        storage.append_event(edict.id, memorial.id, "execution.failed", {
            "error": memorial.error,
        })
    except Exception as e:
        logger.exception("Unexpected error executing edict %s", edict.id)
        memorial.status = TaskStatus.FAILED
        memorial.error = str(e)
        memorial.completed_at = datetime.now(UTC)
        storage.append_event(edict.id, memorial.id, "execution.failed", {
            "error": memorial.error,
        })
    finally:
        try:
            storage.update_memorial(memorial)
        except Exception:
            logger.exception("Failed to update memorial %s", memorial.id)

    return ApiResponse(
        success=memorial.status == TaskStatus.COMPLETED,
        data=memorial.model_dump(mode="json"),
        error=memorial.error,
    )


@gateway_router.get("/edicts")
async def list_edicts(
    request: Request,
    status: TaskStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    storage = request.app.state.storage
    edicts, total = storage.list_edicts(
        status=status.value if status else None, limit=limit, offset=offset
    )
    return ApiResponse(
        success=True,
        data=[e.model_dump(mode="json") for e in edicts],
        metadata={"total": total, "limit": limit, "offset": offset},
    )


@gateway_router.get("/edicts/{edict_id}")
async def get_edict(edict_id: str, request: Request):
    storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    return ApiResponse(success=True, data=edict.model_dump(mode="json"))


@gateway_router.get("/edicts/{edict_id}/memorial")
async def get_memorial_by_edict(edict_id: str, request: Request):
    storage = request.app.state.storage
    memorial = storage.get_memorial_by_edict(edict_id)
    if not memorial:
        raise HTTPException(
            status_code=404, detail=f"Memorial for edict '{edict_id}' not found"
        )
    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))


@gateway_router.get("/edicts/{edict_id}/events")
async def get_events(edict_id: str, request: Request):
    storage = request.app.state.storage
    events = storage.get_events(edict_id)
    return ApiResponse(success=True, data=events)


@gateway_router.get("/memorials")
async def list_memorials(
    request: Request,
    status: TaskStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    storage = request.app.state.storage
    memorials, total = storage.list_memorials(
        status=status.value if status else None, limit=limit, offset=offset
    )
    return ApiResponse(
        success=True,
        data=[m.model_dump(mode="json") for m in memorials],
        metadata={"total": total, "limit": limit, "offset": offset},
    )


@gateway_router.get("/memorials/{memorial_id}")
async def get_memorial(memorial_id: str, request: Request):
    storage = request.app.state.storage
    memorial = storage.get_memorial(memorial_id)
    if not memorial:
        raise HTTPException(
            status_code=404, detail=f"Memorial '{memorial_id}' not found"
        )
    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))
