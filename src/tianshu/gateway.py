"""Gateway API routes."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.agent import Agent
from tianshu.config import TianshuSettings
from tianshu.config_manager import ConfigManager
from tianshu.models import (
    ApiResponse,
    Edict,
    EdictCreateRequest,
    EdictStatus,
    EdictStatusUpdateRequest,
    EdictUpdateRequest,
    FollowUpRequest,
    LLMConfig,
    LLMConfigUpdateRequest,
    Memorial,
    TaskStatus,
)
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

gateway_router = APIRouter()


@gateway_router.post("/edicts", response_model=ApiResponse, status_code=202)
async def create_edict(body: EdictCreateRequest, request: Request):
    storage = request.app.state.storage
    agent = request.app.state.agent
    settings = request.app.state.settings

    title = body.goal[:20] + "…" if len(body.goal) > 20 else body.goal
    edict = Edict(title=title, goal=body.goal, context=body.context)
    storage.save_edict(edict)

    memorial = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.SUBMITTED)
    storage.save_memorial(memorial)
    storage.append_event(edict.id, memorial.id, "edict.submitted", {"goal": edict.goal})

    task = asyncio.create_task(
        _run_agent(agent, edict, memorial, storage, settings)
    )
    request.app.state.running_tasks.add(task)
    task.add_done_callback(request.app.state.running_tasks.discard)

    return ApiResponse(
        success=True,
        data=edict.model_dump(mode="json"),
    )


async def _run_agent(
    agent: Agent,
    edict: Edict,
    memorial: Memorial,
    storage: Storage,
    settings: TianshuSettings,
    history: list[dict] | None = None,
    user_content: str | None = None,
) -> None:
    memorial.status = TaskStatus.RUNNING
    memorial.started_at = datetime.now(UTC)
    storage.update_memorial(memorial)

    storage.append_event(edict.id, memorial.id, "execution.started", {
        "memorial_id": memorial.id,
    })

    def on_event(event: dict) -> None:
        storage.append_event(edict.id, memorial.id, event["type"], event)

    try:
        result = await asyncio.wait_for(
            agent.execute(
                edict,
                on_event=on_event,
                history=history,
                user_content=user_content,
            ),
            timeout=settings.agent_timeout_seconds,
        )
        memorial.status = result.status
        memorial.summary = result.summary
        memorial.result = result.result
        memorial.usage = result.usage
        memorial.error = result.error

        event_type = {
            TaskStatus.COMPLETED: "execution.completed",
            TaskStatus.FAILED: "execution.failed",
            TaskStatus.CANCELLED: "execution.cancelled",
        }.get(result.status, "execution.failed")

        storage.append_event(edict.id, memorial.id, event_type, {
            "status": result.status.value,
            "error": result.error,
        })

    except asyncio.CancelledError:
        memorial.status = TaskStatus.CANCELLED
        memorial.error = "Task was cancelled"
        storage.append_event(edict.id, memorial.id, "execution.cancelled", {
            "error": memorial.error,
        })
        raise
    except asyncio.TimeoutError:
        memorial.status = TaskStatus.FAILED
        memorial.error = f"Execution timed out after {settings.agent_timeout_seconds}s"
        storage.append_event(edict.id, memorial.id, "execution.failed", {
            "error": memorial.error,
        })
    except Exception as e:
        logger.exception("Unexpected error executing edict %s", edict.id)
        memorial.status = TaskStatus.FAILED
        memorial.error = str(e)
        storage.append_event(edict.id, memorial.id, "execution.failed", {
            "error": memorial.error,
        })
    finally:
        memorial.completed_at = datetime.now(UTC)
        try:
            storage.update_memorial(memorial)
        except Exception:
            logger.exception("Failed to update memorial %s", memorial.id)


def _build_history(edict: Edict, memorials: list[Memorial]) -> list[dict]:
    history: list[dict] = []
    for m in memorials:
        if m.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            continue
        instruction = m.instruction or edict.goal
        history.append({"role": "user", "content": instruction})
        if m.result:
            history.append({"role": "assistant", "content": m.result})
    return history


@gateway_router.get("/edicts")
async def list_edicts(
    request: Request,
    status: EdictStatus | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    storage = request.app.state.storage
    edicts, total = storage.list_edicts(
        status=status.value if status else None,
        search=search or None,
        limit=limit,
        offset=offset,
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


@gateway_router.patch("/edicts/{edict_id}", response_model=ApiResponse)
async def update_edict(edict_id: str, body: EdictUpdateRequest, request: Request):
    storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    if edict.status != EdictStatus.OPEN:
        raise HTTPException(status_code=400, detail="只有进行中的敕令可以编辑")
    storage.update_edict(edict_id, title=body.title, goal=body.goal, context=body.context)
    storage.append_event(edict_id, None, "edict.updated", {
        "goal": body.goal, "context": body.context,
    })
    updated = storage.get_edict(edict_id)
    return ApiResponse(success=True, data=updated.model_dump(mode="json"))


@gateway_router.delete("/edicts/{edict_id}", response_model=ApiResponse)
async def delete_edict(edict_id: str, request: Request):
    storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    if edict.status not in (EdictStatus.OPEN, EdictStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="已结案的敕令不可删除")
    storage.delete_edict(edict_id)
    return ApiResponse(success=True, data={"id": edict_id})


@gateway_router.get("/edicts/{edict_id}/memorial")
async def get_memorial_by_edict(edict_id: str, request: Request):
    storage = request.app.state.storage
    memorial = storage.get_memorial_by_edict(edict_id)
    if not memorial:
        raise HTTPException(
            status_code=404, detail=f"Memorial for edict '{edict_id}' not found"
        )
    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))


@gateway_router.get("/edicts/{edict_id}/memorials")
async def list_edict_memorials(edict_id: str, request: Request):
    storage = request.app.state.storage
    memorials = storage.list_memorials_by_edict(edict_id)
    return ApiResponse(success=True, data=[m.model_dump(mode="json") for m in memorials])


@gateway_router.post("/edicts/{edict_id}/follow-up", response_model=ApiResponse, status_code=202)
async def follow_up_edict(edict_id: str, body: FollowUpRequest, request: Request):
    storage = request.app.state.storage
    agent = request.app.state.agent
    settings = request.app.state.settings

    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    if edict.status != EdictStatus.OPEN:
        raise HTTPException(status_code=400, detail="敕令已结案，无法继续")

    prev_memorials = storage.list_memorials_by_edict(edict_id)
    has_active = any(m.status in (TaskStatus.SUBMITTED, TaskStatus.RUNNING) for m in prev_memorials)
    if has_active:
        raise HTTPException(status_code=409, detail="尚有奏折正在执行，请等待完成后再下达指令")
    history = _build_history(edict, prev_memorials)

    memorial = Memorial(edict_id=edict_id, instruction=body.instruction, status=TaskStatus.SUBMITTED)
    storage.save_memorial(memorial)
    storage.append_event(edict.id, memorial.id, "followup.submitted", {"instruction": body.instruction})

    task = asyncio.create_task(
        _run_agent(agent, edict, memorial, storage, settings, history=history, user_content=body.instruction)
    )
    request.app.state.running_tasks.add(task)
    task.add_done_callback(request.app.state.running_tasks.discard)

    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))


@gateway_router.patch("/edicts/{edict_id}/status", response_model=ApiResponse)
async def update_edict_status(edict_id: str, body: EdictStatusUpdateRequest, request: Request):
    storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    storage.update_edict_status(edict_id, body.status.value)
    storage.append_event(edict_id, None, "edict.closed", {"status": body.status.value})
    edict.status = body.status
    return ApiResponse(success=True, data=edict.model_dump(mode="json"))


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


def _state_to_config(cm: ConfigManager) -> LLMConfig:
    s = cm.state
    return LLMConfig(
        model=s.model,
        api_key_masked=ConfigManager.mask_api_key(s.api_key),
        api_base=s.api_base,
        max_retries=s.max_retries,
        temperature=s.temperature,
        top_p=s.top_p,
        max_tokens=s.max_tokens,
        enabled=s.enabled,
    )


@gateway_router.get("/config", response_model=ApiResponse)
async def get_config(request: Request):
    cm: ConfigManager = request.app.state.config_manager
    return ApiResponse(success=True, data=_state_to_config(cm).model_dump())


@gateway_router.put("/config", response_model=ApiResponse)
async def update_config(body: LLMConfigUpdateRequest, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return ApiResponse(success=True, data=_state_to_config(cm).model_dump())
    cm.update(**updates)
    logger.info("LLM config updated: %s", list(updates.keys()))
    return ApiResponse(success=True, data=_state_to_config(cm).model_dump())
