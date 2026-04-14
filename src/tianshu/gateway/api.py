"""Gateway API routes — event-driven edition."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.providers.manager import ProviderManager
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
    if body.assigned_persona_id:
        persona_loader = request.app.state.persona_loader
        if not persona_loader.get(body.assigned_persona_id):
            raise HTTPException(400, f"Persona '{body.assigned_persona_id}' not found")
        edict_kwargs["assigned_persona_id"] = body.assigned_persona_id
    if body.planner_persona_id:
        persona_loader = request.app.state.persona_loader
        if not persona_loader.get(body.planner_persona_id):
            raise HTTPException(400, f"Planner persona '{body.planner_persona_id}' not found")
        edict_kwargs["planner_persona_id"] = body.planner_persona_id
    if body.plan_review:
        edict_kwargs["plan_review"] = True
    edict = Edict(**edict_kwargs)
    storage.save_edict(edict)
    logger.debug(
        "[API] Edict %s: submitted goal=%.100s, priority=%s, schedule=%s, assigned=%s",
        edict.id, edict.goal, edict.priority, edict.schedule.type, edict.assigned_persona_id,
    )

    memorial = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.SUBMITTED)
    storage.save_memorial(memorial)

    # Fire-and-forget: 不阻塞 API 响应，事件链在后台异步执行
    event_bus.fire(
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
    return ApiResponse(
        success=True,
        data=memorial.model_dump(mode="json") if memorial else None,
    )


@gateway_router.get("/edicts/{edict_id}/memorials")
async def list_edict_memorials(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    memorials = storage.list_memorials_by_edict(edict_id)
    return ApiResponse(success=True, data=[m.model_dump(mode="json") for m in memorials])


@gateway_router.post("/edicts/{edict_id}/plan/approve", response_model=ApiResponse)
async def approve_plan(edict_id: str, request: Request):
    """Approve a pending plan and trigger execution."""
    storage: Storage = request.app.state.storage
    event_bus: EventBus = request.app.state.event_bus
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(404, "Edict not found")

    events = storage.get_events(edict_id)
    plan_event = None
    for e in reversed(events):
        if e["event_type"] == "plan.pending_review":
            plan_event = e
            break
    if not plan_event:
        raise HTTPException(400, "No pending plan to approve")

    plan_payload = plan_event.get("payload", {})
    memorial_id = plan_event.get("memorial_id")

    # Restore memorial status
    if memorial_id:
        memorial = storage.get_memorial(memorial_id)
        if memorial and memorial.status == TaskStatus.NEEDS_REVIEW:
            memorial.status = TaskStatus.PLANNING
            storage.update_memorial(memorial)

    # Record approval event
    storage.append_event(edict_id, memorial_id, "plan.approved", {
        "actor": "human",
        "plan": plan_payload.get("plan", {}),
    })

    # Fire-and-forget: 不阻塞 API 响应
    event_bus.fire(
        make_event(
            "plan.completed",
            edict_id=edict_id,
            memorial_id=memorial_id,
            producer="planner",
            payload=plan_payload,
        )
    )
    return ApiResponse(success=True, data={"status": "approved"})


@gateway_router.post("/edicts/{edict_id}/plan/reject", response_model=ApiResponse)
async def reject_plan(edict_id: str, request: Request):
    """Reject a pending plan."""
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(404, "Edict not found")

    events = storage.get_events(edict_id)
    plan_event = None
    for e in reversed(events):
        if e["event_type"] == "plan.pending_review":
            plan_event = e
            break
    if not plan_event:
        raise HTTPException(400, "No pending plan to reject")

    memorial_id = plan_event.get("memorial_id")
    if memorial_id:
        memorial = storage.get_memorial(memorial_id)
        if memorial:
            memorial.status = TaskStatus.FAILED
            memorial.error = "规划方案被驳回"
            storage.update_memorial(memorial)

    storage.append_event(edict_id, memorial_id, "plan.rejected", {
        "actor": "human",
    })
    return ApiResponse(success=True, data={"status": "rejected"})


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
    pm: ProviderManager = request.app.state.provider_manager
    pm.sync_from_config(state)
    return ApiResponse(success=True, data=_state_to_config(state).model_dump())


@gateway_router.put("/configs/{name}", response_model=ApiResponse)
async def update_named_config(name: str, body: LLMConfigUpdateRequest, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    updates = body.model_dump(exclude_none=True)
    try:
        new_state = cm.update_config(name, **updates) if updates else cm.get_config(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    if new_state:
        pm: ProviderManager = request.app.state.provider_manager
        pm.sync_from_config(new_state)
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
    pm: ProviderManager = request.app.state.provider_manager
    pm.unregister(name)
    return ApiResponse(success=True, data={"name": name})


@gateway_router.put("/configs/{name}/activate", response_model=ApiResponse)
async def activate_config(name: str, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    try:
        cm.set_active(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    pm: ProviderManager = request.app.state.provider_manager
    pm.sync_all()
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


# --- Department endpoints ---


@gateway_router.get("/departments")
async def list_departments(request: Request):
    storage: Storage = request.app.state.storage
    departments = storage.list_departments()
    return ApiResponse(success=True, data=departments)


@gateway_router.post("/departments", response_model=ApiResponse, status_code=201)
async def create_department(request: Request):
    storage: Storage = request.app.state.storage
    body = await request.json()
    if not body.get("id") or not body.get("name"):
        raise HTTPException(status_code=400, detail="id and name are required")
    if storage.get_department(body["id"]):
        raise HTTPException(status_code=409, detail=f"Department '{body['id']}' already exists")
    storage.save_department(body)
    dept = storage.get_department(body["id"])
    return ApiResponse(success=True, data=dept)


@gateway_router.put("/departments/{dept_id}", response_model=ApiResponse)
async def update_department(dept_id: str, request: Request):
    storage: Storage = request.app.state.storage
    existing = storage.get_department(dept_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")
    body = await request.json()
    storage.update_department(dept_id, **body)
    updated = storage.get_department(dept_id)
    return ApiResponse(success=True, data=updated)


@gateway_router.delete("/departments/{dept_id}", response_model=ApiResponse)
async def delete_department(dept_id: str, request: Request):
    storage: Storage = request.app.state.storage
    existing = storage.get_department(dept_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")
    deleted = storage.delete_department(dept_id)
    if not deleted:
        raise HTTPException(status_code=409, detail="Cannot delete department with assigned personas")
    return ApiResponse(success=True, data={"id": dept_id})


# --- Persona endpoints (Phase 3) ---


@gateway_router.get("/personas")
async def list_personas(request: Request):
    selector: OfficialSelector = request.app.state.official_selector
    storage: Storage = request.app.state.storage
    personas = selector.list_all()
    # Build department name lookup
    departments = storage.list_departments()
    dept_name_map = {d["id"]: d["name"] for d in departments}
    return ApiResponse(success=True, data=[
        {
            "id": p.id,
            "name": p.name,
            "department": p.department,
            "department_name": dept_name_map.get(p.department),
            "tools_allowed": p.tools_allowed,
            "tools_denied": p.tools_denied,
            "skills_allowed": p.skills_allowed,
            "tool_tier_max": p.tool_tier_max,
            "can_delegate": p.can_delegate,
            "delegates_to": p.delegates_to,
            "llm_config_name": p.llm_config_name,
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

    # Validate department exists
    storage: Storage = request.app.state.storage
    if not storage.get_department(body["department"]):
        raise HTTPException(status_code=400, detail=f"Department '{body['department']}' does not exist")

    # Validate llm_config_name FK if provided
    llm_config_name = body.get("llm_config_name") or None
    if llm_config_name:
        from tianshu.config_manager import ConfigManager
        config_manager: ConfigManager = request.app.state.config_manager
        if not config_manager.get_config(llm_config_name):
            raise HTTPException(
                status_code=400,
                detail=f"LLM config '{llm_config_name}' does not exist",
            )

    personas_dir = loader._dir
    persona_dir = personas_dir / body["id"]
    persona_dir.mkdir(parents=True, exist_ok=True)

    soul_path = persona_dir / "SOUL.md"
    role_path = persona_dir / "ROLE.md"

    # Create default SOUL.md / ROLE.md if not present
    if not soul_path.exists():
        dept_label = body["department"]
        soul_path.write_text(
            f"---\nname: {body['name']}\ndepartment: {dept_label}\n---\n\n"
            f"# {body['name']}\n\n"
            f"你是{body['name']}，隶属{dept_label}。\n",
            encoding="utf-8",
        )
    if not role_path.exists():
        role_path.write_text(
            f"# {body['name']} — 职责\n\n"
            f"作为{body['department']}的官员，你负责执行交办的任务。\n",
            encoding="utf-8",
        )

    persona = AgentPersona(
        id=body["id"],
        name=body["name"],
        department=body["department"],
        soul_path=soul_path,
        role_path=role_path,
        memory_path=persona_dir / "MEMORY.md",
        tools_allowed=body.get("tools_allowed", []),
        tools_denied=body.get("tools_denied", []),
        skills_allowed=body.get("skills_allowed", []),
        tool_tier_max=body.get("tool_tier_max", 0),
        can_delegate=body.get("can_delegate", False),
        delegates_to=body.get("delegates_to", []),
        llm_config_name=llm_config_name,
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
        "llm_config_name": persona.llm_config_name,
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
    # Validate department FK if changing department
    if "department" in body and not storage.get_department(body["department"]):
        raise HTTPException(status_code=400, detail=f"Department '{body['department']}' does not exist")
    # Validate llm_config_name FK if provided
    if "llm_config_name" in body and body["llm_config_name"]:
        from tianshu.config_manager import ConfigManager
        config_manager: ConfigManager = request.app.state.config_manager
        if not config_manager.get_config(body["llm_config_name"]):
            raise HTTPException(
                status_code=400,
                detail=f"LLM config '{body['llm_config_name']}' does not exist",
            )
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
        "llm_config_name": updated.llm_config_name,
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


# --- EventBus introspection endpoints (运维监控台) ---

@gateway_router.get("/event-bus/handlers")
async def list_event_bus_handlers(request: Request):
    """List all registered event handlers with priorities."""
    event_bus: EventBus = request.app.state.event_bus
    result = {}
    for event_type, entries in event_bus._handlers.items():
        result[event_type] = [
            {"handler": e.handler.__qualname__, "priority": e.priority}
            for e in entries
        ]
    return ApiResponse(success=True, data=result)


@gateway_router.get("/event-bus/stats")
async def get_event_bus_stats(request: Request):
    """Get event type distribution from storage."""
    storage: Storage = request.app.state.storage
    stats = storage.get_event_stats()
    return ApiResponse(success=True, data=stats)


@gateway_router.get("/event-bus/recent")
async def get_recent_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get recent events across all edicts."""
    storage: Storage = request.app.state.storage
    events = storage.get_recent_events(limit=limit)
    return ApiResponse(success=True, data=events)


# --- Hooks introspection endpoints (运维监控台) ---

@gateway_router.get("/hooks/registry")
async def list_hooks_registry(request: Request):
    """List all registered hooks with handler info and priorities."""
    from tianshu.executor.hooks import HookRegistry
    hook_registry: HookRegistry = request.app.state.hook_registry
    result = {}
    for hook_type, entries in hook_registry._hooks.items():
        result[hook_type.value] = [
            {"handler": e.handler.__qualname__, "priority": e.priority}
            for e in entries
        ]
    return ApiResponse(success=True, data=result)


# --- Notification channel endpoints (通政司·驿传) ---

@gateway_router.get("/notifications/channels")
async def list_notification_channels(request: Request):
    """List registered notification channels with rate limit info."""
    from tianshu.notifier.channel_registry import ChannelRegistry
    registry: ChannelRegistry = request.app.state.channel_registry
    channels = []
    for name in registry.list_channels():
        channel = registry.get(name)
        rpm = registry._rate_limits.get(name, 10)
        recent_sends = len(registry._send_log.get(name, []))
        channels.append({
            "name": name,
            "type": type(channel).__name__,
            "rpm_limit": rpm,
            "recent_sends": recent_sends,
        })
    return ApiResponse(success=True, data=channels)


# --- Memory maintenance endpoints (文渊阁·整编) ---

@gateway_router.post("/memory/compact", response_model=ApiResponse)
async def compact_memory(request: Request):
    """Trigger memory compaction for a persona."""
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    persona_id = body.get("persona_id")
    if not persona_id:
        raise HTTPException(status_code=400, detail="persona_id is required")
    max_age_days = body.get("max_age_days", 7)
    entries = mm.list_by_persona(persona_id, limit=200)
    from datetime import UTC, datetime, timedelta
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    old_entries = [e for e in entries if e.created_at < cutoff.isoformat()]
    if len(old_entries) <= 3:
        return ApiResponse(success=True, data={
            "status": "skipped",
            "reason": f"Only {len(old_entries)} entries older than {max_age_days} days (need >3)",
        })
    result = await mm._compactor.compact(persona_id, old_entries)
    return ApiResponse(success=True, data={
        "status": "completed",
        "original_count": result.original_count,
        "compacted_count": result.compacted_count,
        "summary": result.summary,
        "tokens_saved": result.tokens_saved,
    })


@gateway_router.post("/memory/reflect", response_model=ApiResponse)
async def trigger_reflection(request: Request):
    """Trigger memory reflection for a persona."""
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    persona_id = body.get("persona_id")
    if not persona_id:
        raise HTTPException(status_code=400, detail="persona_id is required")
    if not mm._reflector.can_reflect(persona_id):
        return ApiResponse(success=True, data={
            "status": "cooldown",
            "reason": "Reflection cooldown active (1 hour between reflections)",
        })
    observations = [
        e for e in mm.list_by_persona(persona_id, limit=50)
        if e.category == "observation"
    ]
    if not observations:
        return ApiResponse(success=True, data={
            "status": "skipped",
            "reason": "No observations to reflect on",
        })
    insights = await mm._reflector.reflect(persona_id, observations)
    for insight in insights:
        mm.store_to_index(insight)
    return ApiResponse(success=True, data={
        "status": "completed",
        "insights_generated": len(insights),
        "insights": [i.content for i in insights],
    })


@gateway_router.get("/memory/stats")
async def get_memory_stats(request: Request):
    """Get memory statistics per persona."""
    mm: MemoryManager = request.app.state.memory_manager
    from pathlib import Path
    stats = {}
    for persona_dir in sorted(mm._memory_dir.iterdir()):
        if not persona_dir.is_dir():
            continue
        pid = persona_dir.name
        entries = mm.list_by_persona(pid, limit=500)
        total_chars = sum(len(e.content) for e in entries)
        by_category = {}
        for e in entries:
            by_category[e.category] = by_category.get(e.category, 0) + 1
        md_files = list(persona_dir.glob("**/*.md"))
        md_size = sum(f.stat().st_size for f in md_files)
        stats[pid] = {
            "entry_count": len(entries),
            "total_chars": total_chars,
            "estimated_tokens": total_chars // 4,
            "by_category": by_category,
            "markdown_files": len(md_files),
            "markdown_size_bytes": md_size,
        }
    return ApiResponse(success=True, data=stats)


# --- Prompt layer visualization (翰林院·拟旨) ---

@gateway_router.get("/system-prompt/layers/{persona_id}")
async def get_prompt_layers(persona_id: str, request: Request):
    """Get system prompt breakdown by layer."""
    from tianshu.persona.prompt_builder import PromptBuilder
    prompt_builder: PromptBuilder = request.app.state.prompt_builder
    persona_loader = request.app.state.persona_loader
    persona = persona_loader.get(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")
    from tianshu.models.edict import Edict
    dummy_edict = Edict(title="Preview", goal="Layer analysis")
    layers = prompt_builder.build_layers(dummy_edict, persona=persona)
    return ApiResponse(success=True, data=layers)


# --- Official routing rules (吏部·铨选) ---

@gateway_router.get("/routing/rules")
async def get_routing_rules(request: Request):
    """Get official routing rules and keyword mappings."""
    selector: OfficialSelector = request.app.state.official_selector
    personas = selector.list_all()
    delegation = []
    for p in personas:
        if p.can_delegate and p.delegates_to:
            delegation.append({
                "from_id": p.id,
                "from_name": p.name,
                "delegates_to": p.delegates_to,
            })
    return ApiResponse(success=True, data={
        "default_map": selector.get_default_map(),
        "keyword_map": selector.get_keyword_map(),
        "delegation_chains": delegation,
    })


# --- Audit rules management (刑部·律典) ---

@gateway_router.get("/audit/rules")
async def get_audit_rules(request: Request):
    """Get configured audit rules and review policies."""
    rules = [
        {
            "id": "token_budget",
            "name": "Token 预算检查",
            "description": "检查 Token 用量是否超过敕令预算限制",
            "enabled": True,
            "severity": "flag",
        },
        {
            "id": "execution_error",
            "name": "执行错误检查",
            "description": "检查执行过程中是否有错误发生",
            "enabled": True,
            "severity": "flag",
        },
        {
            "id": "empty_result",
            "name": "空结果检查",
            "description": "检查执行结果是否为空（无结果且无错误）",
            "enabled": True,
            "severity": "flag",
        },
    ]
    review_policies = [
        {"value": "never", "label": "从不审计", "description": "跳过所有审计流程"},
        {"value": "on_failure", "label": "失败时审计", "description": "仅在执行失败时触发审计"},
        {"value": "on_flag", "label": "标记时审计", "description": "规则标记后触发 LLM 深度审阅"},
        {"value": "always", "label": "始终审计", "description": "无论结果如何都强制人工复核"},
    ]
    return ApiResponse(success=True, data={
        "rules": rules,
        "review_policies": review_policies,
    })


# --- Planner stats ---


@gateway_router.get("/planner/stats")
async def get_planner_stats(request: Request):
    """Get planning statistics: total edicts, passthrough count, DAG count, avg tasks."""
    storage: Storage = request.app.state.storage
    with storage._lock:
        total_edicts = storage._conn.execute("SELECT COUNT(*) FROM edicts").fetchone()[0]
        dag_count = storage._conn.execute("SELECT COUNT(*) FROM dag_executions").fetchone()[0]
        avg_tasks_row = storage._conn.execute(
            "SELECT AVG(node_count) FROM (SELECT dag_execution_id, COUNT(*) AS node_count FROM dag_nodes GROUP BY dag_execution_id)"
        ).fetchone()
        avg_tasks = round(avg_tasks_row[0], 1) if avg_tasks_row[0] else 0

        # Recent planning history: edicts + whether they have DAGs
        recent_rows = storage._conn.execute(
            """SELECT e.id, e.title, e.goal, e.assigned_persona_id, e.planner_persona_id,
                      e.created_at,
                      d.id AS dag_id,
                      (SELECT COUNT(*) FROM dag_nodes dn WHERE dn.dag_execution_id = d.id) AS node_count
               FROM edicts e
               LEFT JOIN dag_executions d ON d.edict_id = e.id
               ORDER BY e.created_at DESC
               LIMIT 20""",
        ).fetchall()

    passthrough_count = total_edicts - dag_count
    history = []
    for r in recent_rows:
        keys = r.keys()
        history.append({
            "edict_id": r["id"],
            "title": r["title"],
            "goal": r["goal"],
            "assigned_persona_id": r["assigned_persona_id"] if "assigned_persona_id" in keys else None,
            "planner_persona_id": r["planner_persona_id"] if "planner_persona_id" in keys else None,
            "plan_type": "dag" if r["dag_id"] else "passthrough",
            "task_count": r["node_count"] or 1,
            "created_at": r["created_at"],
        })

    return ApiResponse(success=True, data={
        "total_edicts": total_edicts,
        "passthrough_count": passthrough_count,
        "dag_count": dag_count,
        "avg_tasks_per_dag": avg_tasks,
        "recent_history": history,
    })


# --- Policy endpoints (Spec Section 6) ---


@gateway_router.get("/edicts/{edict_id}/policy_events")
async def list_policy_events(edict_id: str, request: Request):
    """Return policy.* + hook.* + tool.approval_required + decree.* events for an edict."""
    storage: Storage = request.app.state.storage
    rows = storage.get_events(edict_id)
    data = []
    for row in rows:
        typ = row.get("event_type") or ""
        if not (
            typ.startswith("policy.")
            or typ.startswith("hook.")
            or typ == "tool.approval_required"
            or typ.startswith("decree.")
        ):
            continue
        data.append({
            "id": row.get("id"),
            "memorial_id": row.get("memorial_id"),
            "type": typ,
            "payload": row.get("payload") or {},
            "created_at": row.get("created_at"),
        })
    return ApiResponse(success=True, data={"events": data})


@gateway_router.get("/policy/session_rules")
async def list_session_rules(request: Request, scope: str = "always"):
    """List session rules by scope. scope = 'edict' | 'always'."""
    store = getattr(request.app.state, "session_rule_store", None)
    if store is None:
        return ApiResponse(success=True, data={"rules": []})
    rules = await store.list_by_scope(scope=scope)
    data = [
        {
            "rule_id": r.rule_id,
            "tool_name": r.tool_name,
            "arg_fingerprint": r.arg_fingerprint,
            "scope": r.scope,
            "edict_id": r.edict_id,
            "granted_at": r.granted_at.isoformat(),
            "granted_by_decree_id": r.granted_by_decree_id,
            "source": r.source,
            "reason": r.reason,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in rules
    ]
    return ApiResponse(success=True, data={"rules": data})


@gateway_router.delete("/policy/session_rules/{rule_id}", response_model=ApiResponse)
async def revoke_session_rule(rule_id: str, request: Request):
    """Manually revoke a session rule."""
    store = getattr(request.app.state, "session_rule_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="SessionRuleStore not configured")
    await store.revoke(rule_id)
    storage: Storage = request.app.state.storage
    try:
        storage.append_event(
            "",
            None,
            "policy.session_rule_revoked",
            {"rule_id": rule_id, "source": "manual"},
        )
    except Exception:
        logger.exception("failed to append policy.session_rule_revoked event")
    return ApiResponse(success=True, data={"rule_id": rule_id, "revoked": True})


@gateway_router.get("/policy/stats")
async def policy_stats(request: Request):
    """Aggregate today's allow/deny/require_approval/approved/rejected counts."""
    import json as _json

    storage: Storage = request.app.state.storage
    conn = storage._conn
    stats = {"allow": 0, "deny": 0, "require_approval": 0, "approved": 0, "rejected": 0}
    rows = conn.execute(
        """
        SELECT event_type, payload_json FROM events
        WHERE date(created_at) = date('now')
          AND event_type IN ('policy.decision', 'decree.approved', 'decree.rejected')
        """
    ).fetchall()
    for row in rows:
        typ = row[0]
        payload = row[1]
        if typ == "decree.approved":
            stats["approved"] += 1
        elif typ == "decree.rejected":
            stats["rejected"] += 1
        elif typ == "policy.decision":
            try:
                parsed = _json.loads(payload) if isinstance(payload, str) else (payload or {})
                verdict = parsed.get("verdict", "")
                if verdict in stats:
                    stats[verdict] += 1
            except Exception:
                pass
    return ApiResponse(success=True, data=stats)


@gateway_router.get("/policy/templates")
async def list_policy_templates():
    """List built-in PolicyProfile templates."""
    from tianshu.tools.policy_profile import BUILTIN_TEMPLATES

    data = [
        {
            "name": name,
            "allowed_paths": list(p.allowed_paths),
            "allowed_bash_prefixes": list(p.allowed_bash_prefixes),
            "tier_overrides": dict(p.tier_overrides),
            "auto_approve_max_tier": p.auto_approve_max_tier,
        }
        for name, p in BUILTIN_TEMPLATES.items()
    ]
    return ApiResponse(success=True, data={"templates": data})
