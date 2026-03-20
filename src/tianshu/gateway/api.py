"""Gateway API routes — event-driven edition."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.executor import Executor
from tianshu.models import (
    AgentConfig,
    AgentConfigUpdateRequest,
    ApiResponse,
    Decree,
    DecreeCreateRequest,
    Edict,
    EdictCreateRequest,
    EdictStatus,
    EdictStatusUpdateRequest,
    EdictUpdateRequest,
    FollowUpRequest,
    LLMConfig,
    LLMConfigCreateRequest,
    LLMConfigListResponse,
    LLMConfigUpdateRequest,
    Memorial,
    TaskStatus,
    make_event,
)
from tianshu.consultation.models import ConsultationRequest
from tianshu.consultation.session import ConsultationSession
from tianshu.cost.manager import CostManager
from tianshu.cost.models import BudgetStatus, CostSummary
from tianshu.executor.lanes import LaneManager
from tianshu.executor.worker_pool import WorkerPool
from tianshu.memory.manager import MemoryManager
from tianshu.memory.models import MemoryEntry, MemoryQuery
from tianshu.notifier.notifier import Notifier
from tianshu.persona.evaluator import PerformanceEvaluator
from tianshu.persona.selector import OfficialSelector
from tianshu.scheduler.scheduler import Scheduler
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

gateway_router = APIRouter()


# --- Helper to build history from previous memorials ---

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


# --- Edict endpoints ---


@gateway_router.post("/edicts", response_model=ApiResponse, status_code=202)
async def create_edict(body: EdictCreateRequest, request: Request):
    storage: Storage = request.app.state.storage
    event_bus: EventBus = request.app.state.event_bus

    # Idempotency check: (submitter, idempotency_key) dedup
    if body.idempotency_key:
        existing = storage.find_edict_by_idempotency_key(
            body.submitter, body.idempotency_key,
        )
        if existing:
            return ApiResponse(
                success=True,
                data=existing.model_dump(mode="json"),
                metadata={"deduplicated": True},
            )

    title = body.title or (body.goal[:20] + "…" if len(body.goal) > 20 else body.goal)
    edict_kwargs: dict = {"title": title, "goal": body.goal, "context": body.context}
    if body.idempotency_key:
        edict_kwargs["idempotency_key"] = body.idempotency_key
    if body.submitter:
        edict_kwargs["submitter"] = body.submitter
    if body.priority:
        edict_kwargs["priority"] = body.priority
    if body.review_policy:
        edict_kwargs["review_policy"] = body.review_policy
    if body.schedule and body.schedule.type != "immediate":
        from tianshu.models.edict import EdictSchedule
        edict_kwargs["schedule"] = EdictSchedule(type=body.schedule.type, cron=body.schedule.cron)
    if body.constraints:
        edict_kwargs["constraints"] = body.constraints
    if body.output_format:
        edict_kwargs["output_format"] = body.output_format
    if body.runtime:
        from tianshu.models.edict import EdictRuntime
        rt_data = {k: v for k, v in body.runtime.model_dump().items() if v is not None}
        if rt_data:
            edict_kwargs["runtime"] = EdictRuntime(**rt_data)
    edict = Edict(**edict_kwargs)
    storage.save_edict(edict)

    memorial = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.SUBMITTED)
    storage.save_memorial(memorial)

    await event_bus.emit(
        make_event(
            "edict.submitted",
            edict_id=edict.id,
            memorial_id=memorial.id,
            producer="gateway",
            payload={"goal": edict.goal},
        )
    )

    return ApiResponse(
        success=True,
        data=edict.model_dump(mode="json"),
    )


@gateway_router.get("/edicts")
async def list_edicts(
    request: Request,
    status: EdictStatus | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    storage: Storage = request.app.state.storage
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
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    return ApiResponse(success=True, data=edict.model_dump(mode="json"))


@gateway_router.patch("/edicts/{edict_id}", response_model=ApiResponse)
async def update_edict(edict_id: str, body: EdictUpdateRequest, request: Request):
    storage: Storage = request.app.state.storage
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
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    # Only allow deletion of completed/cancelled edicts, or those with no active memorials
    if edict.status == EdictStatus.OPEN and storage.has_active_memorials(edict_id):
        raise HTTPException(
            status_code=409,
            detail="无法删除正在执行中的敕令，请先取消执行",
        )
    storage.delete_edict(edict_id)
    return ApiResponse(success=True, data={"id": edict_id})


@gateway_router.get("/edicts/{edict_id}/memorial")
async def get_memorial_by_edict(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    memorial = storage.get_memorial_by_edict(edict_id)
    if not memorial:
        raise HTTPException(
            status_code=404, detail=f"Memorial for edict '{edict_id}' not found"
        )
    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))


@gateway_router.get("/edicts/{edict_id}/memorials")
async def list_edict_memorials(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    memorials = storage.list_memorials_by_edict(edict_id)
    return ApiResponse(success=True, data=[m.model_dump(mode="json") for m in memorials])


@gateway_router.post("/edicts/{edict_id}/follow-up", response_model=ApiResponse, status_code=202)
async def follow_up_edict(edict_id: str, body: FollowUpRequest, request: Request):
    storage: Storage = request.app.state.storage
    event_bus: EventBus = request.app.state.event_bus
    executor: Executor = request.app.state.executor

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

    import asyncio
    task = asyncio.create_task(
        executor.execute_edict(
            edict, memorial=memorial, history=history, user_content=body.instruction
        )
    )
    executor.running_tasks.add(task)
    task.add_done_callback(executor.running_tasks.discard)

    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))


@gateway_router.patch("/edicts/{edict_id}/status", response_model=ApiResponse)
async def update_edict_status(edict_id: str, body: EdictStatusUpdateRequest, request: Request):
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    storage.update_edict_status(edict_id, body.status.value)
    storage.append_event(edict_id, None, "edict.closed", {"status": body.status.value})
    edict.status = body.status
    return ApiResponse(success=True, data=edict.model_dump(mode="json"))


@gateway_router.get("/edicts/{edict_id}/events")
async def get_events(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    events = storage.get_events(edict_id)
    return ApiResponse(success=True, data=events)


# --- Memorial endpoints ---


@gateway_router.get("/memorials")
async def list_memorials(
    request: Request,
    status: TaskStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    storage: Storage = request.app.state.storage
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
    storage: Storage = request.app.state.storage
    memorial = storage.get_memorial(memorial_id)
    if not memorial:
        raise HTTPException(
            status_code=404, detail=f"Memorial '{memorial_id}' not found"
        )
    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))


# --- Scheduler endpoints ---


@gateway_router.get("/scheduler/jobs")
async def list_scheduler_jobs(request: Request):
    scheduler: Scheduler = request.app.state.scheduler
    jobs = await scheduler.list_jobs()
    return ApiResponse(success=True, data=jobs)


@gateway_router.delete("/scheduler/jobs/{job_id}", response_model=ApiResponse)
async def cancel_scheduler_job(job_id: str, request: Request):
    scheduler: Scheduler = request.app.state.scheduler
    await scheduler.cancel(job_id)
    return ApiResponse(success=True, data={"job_id": job_id})


# --- Audit endpoints ---


@gateway_router.get("/audit/stats")
async def get_audit_stats(request: Request):
    storage: Storage = request.app.state.storage
    stats = storage.get_audit_stats()
    return ApiResponse(success=True, data=stats)


# --- Decree (approval) endpoints ---


@gateway_router.post("/decrees", response_model=ApiResponse, status_code=201)
async def create_decree(body: DecreeCreateRequest, request: Request):
    approval_manager: ApprovalManager = request.app.state.approval_manager

    decree = Decree(
        memorial_id=body.memorial_id,
        action=body.action,
        comment=body.comment,
        amended_goal=body.amended_goal,
        actor=body.actor,
    )

    try:
        await approval_manager.submit_decree(decree)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ApiResponse(success=True, data=decree.model_dump(mode="json"))


# --- WebSocket endpoint ---


@gateway_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, request: Request = None):
    notifier: Notifier = websocket.app.state.notifier
    await websocket.accept()
    notifier.register_ws(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        notifier.unregister_ws(websocket)


# --- Memory endpoints ---


@gateway_router.get("/memory/{persona_id}")
async def get_persona_memory(
    persona_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
):
    mm: MemoryManager = request.app.state.memory_manager
    # Auto-sync once per persona per session (won't re-sync after user deletes)
    mm.auto_sync_if_needed(persona_id)
    entries = mm.list_by_persona(persona_id, limit=limit)
    return ApiResponse(
        success=True,
        data=[e.model_dump(mode="json") for e in entries],
    )


@gateway_router.post("/memory/recall", response_model=ApiResponse)
async def recall_memory(request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    source = body.pop("source", "sqlite")
    query = MemoryQuery(**body)
    entries = mm.recall(query, source=source)
    return ApiResponse(
        success=True,
        data=[e.model_dump(mode="json") for e in entries],
    )


@gateway_router.post("/memory/sync", response_model=ApiResponse)
async def sync_memory_index(request: Request):
    """Manually trigger SQLite index rebuild from Markdown files."""
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    persona_id = body.get("persona_id")
    if persona_id:
        count = mm.sync_index(persona_id)
        results = {persona_id: count}
    else:
        results = mm.sync_all_indices()
    return ApiResponse(success=True, data=results)


@gateway_router.delete("/memory/{entry_id}", response_model=ApiResponse)
async def delete_memory(entry_id: str, request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    deleted = mm.delete(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Memory entry '{entry_id}' not found")
    return ApiResponse(success=True, data={"id": entry_id})


@gateway_router.post("/memory/batch-delete", response_model=ApiResponse)
async def batch_delete_memory(request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    entry_ids = body.get("entry_ids", [])
    if not entry_ids:
        raise HTTPException(status_code=400, detail="entry_ids is required")
    deleted = mm.delete_batch(entry_ids)
    return ApiResponse(success=True, data={"deleted": deleted})


@gateway_router.get("/memory/policies")
async def get_memory_policies(request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    policies = {}
    for pid, policy in mm._access_control._policies.items():
        policies[pid] = {
            "persona_id": policy.persona_id,
            "can_read": policy.can_read,
            "can_write": policy.can_write,
            "share_level": policy.share_level,
        }
    return ApiResponse(success=True, data=policies)


@gateway_router.put("/memory/policies/{persona_id}", response_model=ApiResponse)
async def update_memory_policy(persona_id: str, request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    from tianshu.memory.access_control import MemoryAccessPolicy
    policy = MemoryAccessPolicy(
        persona_id=persona_id,
        can_read=body.get("can_read", []),
        can_write=body.get("can_write", []),
        share_level=body.get("share_level", "private"),
    )
    mm._access_control.set_policy(policy)
    return ApiResponse(success=True, data={
        "persona_id": persona_id,
        "can_read": policy.can_read,
        "can_write": policy.can_write,
        "share_level": policy.share_level,
    })


# --- Cost endpoints ---


@gateway_router.get("/cost/summary")
async def get_cost_summary(
    request: Request,
    period: str | None = None,
    edict_id: str | None = None,
):
    cm: CostManager = request.app.state.cost_manager
    summary = cm.get_summary(period=period, edict_id=edict_id)
    return ApiResponse(success=True, data=summary.model_dump())


@gateway_router.get("/cost/records")
async def get_cost_records(
    request: Request,
    edict_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    cm: CostManager = request.app.state.cost_manager
    records, total = cm.get_records(edict_id=edict_id, limit=limit, offset=offset)
    return ApiResponse(
        success=True,
        data=records,
        metadata={"total": total, "limit": limit, "offset": offset},
    )


@gateway_router.get("/cost/budget")
async def get_cost_budget(request: Request, scope: str = "global"):
    cm: CostManager = request.app.state.cost_manager
    status = cm.get_budget(scope)
    if not status:
        return ApiResponse(success=True, data=None)
    return ApiResponse(success=True, data=status.model_dump())


@gateway_router.put("/cost/budget", response_model=ApiResponse)
async def set_cost_budget(request: Request):
    body = await request.json()
    cm: CostManager = request.app.state.cost_manager
    cm.set_budget(
        scope=body.get("scope", "global"),
        budget_cny=body["budget_cny"],
        period=body.get("period", "monthly"),
    )
    return ApiResponse(success=True, data={"scope": body.get("scope", "global")})


@gateway_router.get("/cost/export")
async def export_cost_records(
    request: Request,
    period: str | None = None,
    edict_id: str | None = None,
):
    cm: CostManager = request.app.state.cost_manager
    records, _ = cm.get_records(edict_id=edict_id, limit=10000)
    summary = cm.get_summary(period=period, edict_id=edict_id)
    return ApiResponse(
        success=True,
        data={"summary": summary.model_dump(), "records": records},
    )


# --- Provider endpoints ---


@gateway_router.get("/providers")
async def list_providers(request: Request):
    storage: Storage = request.app.state.storage
    providers = storage.list_providers()
    return ApiResponse(success=True, data=providers)


@gateway_router.post("/providers", response_model=ApiResponse, status_code=201)
async def create_provider(request: Request):
    storage: Storage = request.app.state.storage
    body = await request.json()
    if not body.get("name") or not body.get("model"):
        raise HTTPException(status_code=400, detail="name and model are required")
    from datetime import UTC, datetime
    body.setdefault("created_at", datetime.now(UTC).isoformat())
    storage.save_provider(body)
    return ApiResponse(success=True, data=body)


@gateway_router.put("/providers/{name}", response_model=ApiResponse)
async def update_provider(name: str, request: Request):
    storage: Storage = request.app.state.storage
    existing = storage.get_provider(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    body = await request.json()
    storage.update_provider(name, body)
    updated = storage.get_provider(name)
    return ApiResponse(success=True, data=updated)


@gateway_router.delete("/providers/{name}", response_model=ApiResponse)
async def delete_provider(name: str, request: Request):
    storage: Storage = request.app.state.storage
    deleted = storage.delete_provider(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return ApiResponse(success=True, data={"name": name})


@gateway_router.get("/providers/{name}/status")
async def get_provider_status(name: str, request: Request):
    storage: Storage = request.app.state.storage
    provider = storage.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return ApiResponse(success=True, data=provider)


# --- Plugin endpoints ---


@gateway_router.get("/plugins")
async def list_plugins(request: Request):
    storage: Storage = request.app.state.storage
    plugins = storage.list_plugins()
    return ApiResponse(success=True, data=plugins)


@gateway_router.get("/plugins/{name}")
async def get_plugin(name: str, request: Request):
    storage: Storage = request.app.state.storage
    plugin = storage.get_plugin(name)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    return ApiResponse(success=True, data=plugin)


@gateway_router.post("/plugins/install", response_model=ApiResponse, status_code=201)
async def install_plugin(request: Request):
    storage: Storage = request.app.state.storage
    body = await request.json()
    if not body.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    storage.save_plugin(body)
    return ApiResponse(success=True, data=body)


@gateway_router.put("/plugins/{name}/status", response_model=ApiResponse)
async def update_plugin_status(name: str, request: Request):
    storage: Storage = request.app.state.storage
    plugin = storage.get_plugin(name)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    body = await request.json()
    storage.update_plugin_status(name, body.get("status", "active"))
    return ApiResponse(success=True, data={"name": name, "status": body.get("status", "active")})


# --- Config endpoints ---


@gateway_router.get("/agent-config", response_model=ApiResponse)
async def get_agent_config(request: Request):
    cm: ConfigManager = request.app.state.config_manager
    state = cm.agent_config
    data = AgentConfig(
        agent_max_iterations=state.agent_max_iterations,
        agent_timeout_seconds=state.agent_timeout_seconds,
        skills_char_budget=state.skills_char_budget,
    )
    return ApiResponse(success=True, data=data.model_dump())


@gateway_router.put("/agent-config", response_model=ApiResponse)
async def update_agent_config(body: AgentConfigUpdateRequest, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    updates = body.model_dump(exclude_none=True)
    if not updates:
        state = cm.agent_config
    else:
        state = cm.update_agent_config(**updates)
        logger.info("Agent config updated: %s", list(updates.keys()))
    data = AgentConfig(
        agent_max_iterations=state.agent_max_iterations,
        agent_timeout_seconds=state.agent_timeout_seconds,
        skills_char_budget=state.skills_char_budget,
    )
    return ApiResponse(success=True, data=data.model_dump())


def _state_to_config(s: LLMConfigState) -> LLMConfig:
    return LLMConfig(
        name=s.name,
        model=s.model,
        api_key_masked=ConfigManager.mask_api_key(s.api_key),
        api_base=s.api_base,
        max_retries=s.max_retries,
        temperature=s.temperature,
        top_p=s.top_p,
        max_tokens=s.max_tokens,
        enabled=s.enabled,
    )


# --- Legacy single-config endpoints (operate on active config) ---


@gateway_router.get("/config", response_model=ApiResponse)
async def get_config(request: Request):
    cm: ConfigManager = request.app.state.config_manager
    return ApiResponse(success=True, data=_state_to_config(cm.state).model_dump())


@gateway_router.put("/config", response_model=ApiResponse)
async def update_config(body: LLMConfigUpdateRequest, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return ApiResponse(success=True, data=_state_to_config(cm.state).model_dump())
    cm.update(**updates)
    logger.info("LLM config updated: %s", list(updates.keys()))
    return ApiResponse(success=True, data=_state_to_config(cm.state).model_dump())


# --- Multi-config endpoints ---


@gateway_router.get("/configs", response_model=ApiResponse)
async def list_configs(request: Request):
    cm: ConfigManager = request.app.state.config_manager
    configs, active_name = cm.list_configs()
    resp = LLMConfigListResponse(
        configs=[_state_to_config(c) for c in configs],
        active_name=active_name,
    )
    return ApiResponse(success=True, data=resp.model_dump())


@gateway_router.post("/configs", response_model=ApiResponse, status_code=201)
async def create_config(body: LLMConfigCreateRequest, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    state = LLMConfigState(
        name=body.name,
        model=body.model,
        api_key=body.api_key,
        api_base=body.api_base,
        max_retries=body.max_retries,
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
        enabled=body.enabled,
    )
    try:
        cm.add_config(state)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ApiResponse(success=True, data=_state_to_config(state).model_dump())


@gateway_router.put("/configs/{name}", response_model=ApiResponse)
async def update_named_config(name: str, body: LLMConfigUpdateRequest, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    updates = body.model_dump(exclude_none=True)
    try:
        new_state = cm.update_config(name, **updates) if updates else cm.get_config(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    return ApiResponse(success=True, data=_state_to_config(new_state).model_dump())


@gateway_router.delete("/configs/{name}", response_model=ApiResponse)
async def delete_named_config(name: str, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    try:
        cm.delete_config(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(success=True, data={"name": name})


@gateway_router.put("/configs/{name}/activate", response_model=ApiResponse)
async def activate_config(name: str, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    try:
        cm.set_active(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    configs, active_name = cm.list_configs()
    resp = LLMConfigListResponse(
        configs=[_state_to_config(c) for c in configs],
        active_name=active_name,
    )
    return ApiResponse(success=True, data=resp.model_dump())


# --- DAG endpoints (Phase 3) ---


@gateway_router.get("/dag/by-edict/{edict_id}")
async def get_dag_by_edict(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    dag = storage.get_dag_by_edict(edict_id)
    if not dag:
        raise HTTPException(status_code=404, detail=f"No DAG for edict '{edict_id}'")
    return ApiResponse(success=True, data=dag.model_dump(mode="json"))


@gateway_router.get("/dag/{dag_id}")
async def get_dag(dag_id: str, request: Request):
    storage: Storage = request.app.state.storage
    dag = storage.get_dag_execution(dag_id)
    if not dag:
        raise HTTPException(status_code=404, detail=f"DAG '{dag_id}' not found")
    return ApiResponse(success=True, data=dag.model_dump(mode="json"))


@gateway_router.post("/dag/{dag_id}/cancel", response_model=ApiResponse)
async def cancel_dag(dag_id: str, request: Request):
    executor: Executor = request.app.state.executor
    try:
        cancelled = await executor.cancel_dag(dag_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(success=True, data={"dag_id": dag_id, "cancelled_nodes": cancelled})


@gateway_router.post("/dag/{dag_id}/retry", response_model=ApiResponse)
async def retry_dag(dag_id: str, request: Request):
    executor: Executor = request.app.state.executor
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    from_node_ids = body.get("from_node_ids")
    try:
        reset_ids = await executor.retry_dag(dag_id, from_node_ids=from_node_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(success=True, data={"dag_id": dag_id, "reset_node_ids": reset_ids})


# --- Worker endpoints (Phase 3) ---


@gateway_router.get("/workers")
async def list_workers(request: Request):
    pool: WorkerPool = request.app.state.worker_pool
    return ApiResponse(success=True, data={"active": pool.list_active()})


@gateway_router.get("/workers/status")
async def get_workers_status(request: Request):
    pool: WorkerPool = request.app.state.worker_pool
    lane_manager: LaneManager = request.app.state.lane_manager
    pool_status = pool.status()
    return ApiResponse(success=True, data={
        "pool": {
            "active_count": pool_status.active_count,
            "max_concurrency": pool_status.max_concurrency,
            "pending_count": pool_status.pending_count,
            "completed_count": pool_status.completed_count,
            "failed_count": pool_status.failed_count,
        },
        "lanes": lane_manager.status(),
    })


# --- Consultation endpoints (Phase 3) ---


@gateway_router.post("/consultations", response_model=ApiResponse, status_code=202)
async def create_consultation(request: Request):
    import asyncio
    from tianshu.consultation.models import ConsultationResponse

    consultation: ConsultationSession = request.app.state.consultation
    body = await request.json()
    req = ConsultationRequest(**body)

    placeholder = ConsultationResponse(request=req, status="running")
    consultation._sessions[placeholder.id] = placeholder

    async def _run() -> None:
        result = await consultation.start(req)
        result.id = placeholder.id
        consultation._sessions[placeholder.id] = result

    asyncio.create_task(_run())
    return ApiResponse(success=True, data={"id": placeholder.id, "status": "running"})


@gateway_router.get("/consultations/{consultation_id}")
async def get_consultation(consultation_id: str, request: Request):
    consultation: ConsultationSession = request.app.state.consultation
    result = consultation.get(consultation_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Consultation '{consultation_id}' not found")
    return ApiResponse(success=True, data=result.model_dump(mode="json"))


# --- Persona endpoints (Phase 3) ---


@gateway_router.get("/personas")
async def list_personas(request: Request):
    selector: OfficialSelector = request.app.state.official_selector
    personas = selector.list_all()
    return ApiResponse(success=True, data=[
        {
            "id": p.id,
            "name": p.name,
            "department": p.department,
            "tools_allowed": p.tools_allowed,
            "tools_denied": p.tools_denied,
            "skills_allowed": p.skills_allowed,
            "tool_tier_max": p.tool_tier_max,
            "can_delegate": p.can_delegate,
            "delegates_to": p.delegates_to,
        }
        for p in personas
    ])


@gateway_router.post("/personas", response_model=ApiResponse, status_code=201)
async def create_persona(request: Request):
    from tianshu.persona.loader import PersonaLoader
    from tianshu.persona.model import AgentPersona

    loader: PersonaLoader = request.app.state.persona_loader
    body = await request.json()
    if not body.get("id") or not body.get("name") or not body.get("department"):
        raise HTTPException(status_code=400, detail="id, name, department are required")

    # Check duplicate
    if loader.get(body["id"]):
        raise HTTPException(status_code=409, detail=f"Persona '{body['id']}' already exists")

    personas_dir = loader._dir
    persona = AgentPersona(
        id=body["id"],
        name=body["name"],
        department=body["department"],
        soul_path=personas_dir / body["id"] / "SOUL.md",
        role_path=personas_dir / body["id"] / "ROLE.md",
        memory_path=personas_dir / body["id"] / "MEMORY.md",
        tools_allowed=body.get("tools_allowed", []),
        tools_denied=body.get("tools_denied", []),
        skills_allowed=body.get("skills_allowed", []),
        tool_tier_max=body.get("tool_tier_max", 0),
        can_delegate=body.get("can_delegate", False),
        delegates_to=body.get("delegates_to", []),
    )
    loader.save(persona)
    return ApiResponse(success=True, data={
        "id": persona.id,
        "name": persona.name,
        "department": persona.department,
        "tools_allowed": persona.tools_allowed,
        "tools_denied": persona.tools_denied,
        "skills_allowed": persona.skills_allowed,
        "tool_tier_max": persona.tool_tier_max,
        "can_delegate": persona.can_delegate,
        "delegates_to": persona.delegates_to,
    })


@gateway_router.put("/personas/{persona_id}", response_model=ApiResponse)
async def update_persona(persona_id: str, request: Request):
    from tianshu.persona.loader import PersonaLoader

    loader: PersonaLoader = request.app.state.persona_loader
    storage: Storage = request.app.state.storage

    existing = loader.get(persona_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")

    body = await request.json()
    storage.update_persona(persona_id, **body)
    # Reload from DB to refresh in-memory cache
    loader.load_all()

    updated = loader.get(persona_id)
    return ApiResponse(success=True, data={
        "id": updated.id,
        "name": updated.name,
        "department": updated.department,
        "tools_allowed": updated.tools_allowed,
        "tools_denied": updated.tools_denied,
        "skills_allowed": updated.skills_allowed,
        "tool_tier_max": updated.tool_tier_max,
        "can_delegate": updated.can_delegate,
        "delegates_to": updated.delegates_to,
    })


@gateway_router.delete("/personas/{persona_id}", response_model=ApiResponse)
async def delete_persona(persona_id: str, request: Request):
    from tianshu.persona.loader import PersonaLoader

    loader: PersonaLoader = request.app.state.persona_loader
    existing = loader.get(persona_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")

    loader.delete(persona_id)
    return ApiResponse(success=True, data={"id": persona_id})


@gateway_router.get("/memorials/by-persona/{persona_id}")
async def list_memorials_by_persona(
    persona_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    storage: Storage = request.app.state.storage
    grouped, total = storage.list_memorials_by_persona(persona_id, limit=limit, offset=offset)
    return ApiResponse(
        success=True,
        data=grouped,
        metadata={"total": total, "limit": limit, "offset": offset},
    )


@gateway_router.get("/personas/{persona_id}/metrics")
async def get_persona_metrics(persona_id: str, request: Request):
    evaluator: PerformanceEvaluator = request.app.state.evaluator
    metrics = evaluator.evaluate(persona_id)
    return ApiResponse(success=True, data=metrics.model_dump())


# --- Skills endpoints (藏兵阁) ---


@gateway_router.get("/skills")
async def list_skills(request: Request):
    from tianshu.skills.loader import SkillsLoader
    loader: SkillsLoader = request.app.state.skills_loader
    skills = loader.list_all_metadata()
    return ApiResponse(success=True, data=skills)


@gateway_router.get("/skills/{name}")
async def get_skill(name: str, request: Request):
    from tianshu.skills.loader import SkillsLoader
    loader: SkillsLoader = request.app.state.skills_loader
    skill = loader.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return ApiResponse(success=True, data=skill)


@gateway_router.put("/skills/{name}", response_model=ApiResponse)
async def update_skill(name: str, request: Request):
    from tianshu.skills.loader import SkillsLoader
    loader: SkillsLoader = request.app.state.skills_loader
    body = await request.json()
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="content is required")
    try:
        skill = loader.save_skill(name, content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return ApiResponse(success=True, data=skill)


@gateway_router.post("/skills", response_model=ApiResponse, status_code=201)
async def create_skill(request: Request):
    from tianshu.skills.loader import SkillsLoader
    loader: SkillsLoader = request.app.state.skills_loader
    body = await request.json()
    name = body.get("name")
    content = body.get("content", "")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        skill = loader.create_skill(name, content)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ApiResponse(success=True, data=skill)


@gateway_router.delete("/skills/{name}", response_model=ApiResponse)
async def delete_skill(name: str, request: Request):
    from tianshu.skills.loader import SkillsLoader
    loader: SkillsLoader = request.app.state.skills_loader
    # Check if it's a builtin skill
    skill = loader.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    if skill["source"] == "builtin":
        raise HTTPException(status_code=403, detail="Cannot delete builtin skills")
    deleted = loader.delete_skill(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return ApiResponse(success=True, data={"name": name})


# --- Tools endpoints (藏兵阁) ---


@gateway_router.get("/tools")
async def list_tools(request: Request):
    from tianshu.tools.registry import ToolRegistry
    registry: ToolRegistry = request.app.state.tool_registry
    persona_loader = request.app.state.persona_loader
    definitions = registry.list_definitions()

    # Build tool -> personas mapping
    all_personas = list(persona_loader._personas.values())
    result = []
    for defn in definitions:
        personas = []
        for p in all_personas:
            if p.tools_allowed and defn.name in p.tools_allowed:
                personas.append(p.id)
            elif not p.tools_denied or defn.name not in p.tools_denied:
                if defn.tier <= p.tool_tier_max:
                    personas.append(p.id)
        result.append({
            "name": defn.name,
            "description": defn.description,
            "tier": defn.tier,
            "parameters": defn.parameters,
            "personas": personas,
        })
    return ApiResponse(success=True, data=result)


# --- System Prompt endpoints (藏兵阁) ---


_PROMPT_FILE_WHITELIST = {"SOUL.md", "ROLE.md", "COURT.md", "MEMORY.md"}


@gateway_router.get("/system-prompt/files")
async def list_prompt_files(request: Request):
    from pathlib import Path
    personas_dir: Path = request.app.state.personas_dir
    result = []
    if not personas_dir.is_dir():
        return ApiResponse(success=True, data=result)
    for persona_dir in sorted(personas_dir.iterdir()):
        if not persona_dir.is_dir():
            continue
        persona_id = persona_dir.name
        for md_file in sorted(persona_dir.glob("*.md")):
            if md_file.name in _PROMPT_FILE_WHITELIST:
                stat = md_file.stat()
                from datetime import datetime, UTC
                result.append({
                    "persona_id": persona_id,
                    "filename": md_file.name,
                    "path": str(md_file),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                })
    return ApiResponse(success=True, data=result)


@gateway_router.get("/system-prompt/files/{persona_id}/{filename}")
async def get_prompt_file(persona_id: str, filename: str, request: Request):
    from pathlib import Path
    if filename not in _PROMPT_FILE_WHITELIST:
        raise HTTPException(status_code=400, detail=f"File '{filename}' is not in whitelist")
    personas_dir: Path = request.app.state.personas_dir
    file_path = personas_dir / persona_id / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {persona_id}/{filename}")
    content = file_path.read_text(encoding="utf-8")
    return ApiResponse(success=True, data={"persona_id": persona_id, "filename": filename, "content": content})


@gateway_router.put("/system-prompt/files/{persona_id}/{filename}", response_model=ApiResponse)
async def update_prompt_file(persona_id: str, filename: str, request: Request):
    from pathlib import Path
    if filename not in _PROMPT_FILE_WHITELIST:
        raise HTTPException(status_code=400, detail=f"File '{filename}' is not in whitelist")
    personas_dir: Path = request.app.state.personas_dir
    file_path = personas_dir / persona_id / filename
    if not file_path.parent.is_dir():
        raise HTTPException(status_code=404, detail=f"Persona directory '{persona_id}' not found")
    body = await request.json()
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="content is required")
    file_path.write_text(content, encoding="utf-8")
    return ApiResponse(success=True, data={"persona_id": persona_id, "filename": filename, "size": len(content)})


@gateway_router.get("/system-prompt/preview/{persona_id}")
async def preview_system_prompt(persona_id: str, request: Request):
    from tianshu.persona.prompt_builder import PromptBuilder
    prompt_builder: PromptBuilder = request.app.state.prompt_builder
    persona_loader = request.app.state.persona_loader
    persona = persona_loader.get(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")
    # Build a dummy edict for preview
    from tianshu.models.edict import Edict
    dummy_edict = Edict(title="Preview", goal="System prompt preview")
    preview = prompt_builder.build(dummy_edict, persona=persona)
    return ApiResponse(success=True, data={"persona_id": persona_id, "prompt": preview})
