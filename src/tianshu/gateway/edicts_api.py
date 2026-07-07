"""/api/edicts 路由：敕令 CRUD、生命周期（pause/resume）、计划审批、follow-up、outer-loop 决策、监督报告。"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from tianshu.bus.event_bus import EventBus
from tianshu.edict_ops import submit_new_edict
from tianshu.executor.executor import Executor
from tianshu.gateway._helpers import _build_history
from tianshu.models import (
    ApiResponse,
    Edict,
    EdictCreateRequest,
    EdictStatus,
    EdictStatusUpdateRequest,
    EdictUpdateRequest,
    FollowUpRequest,
    Memorial,
    TaskStatus,
    make_event,
)
from tianshu.models.api import ParseEdictRequest
from tianshu.models.edict import title_from_goal
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

edicts_router = APIRouter(prefix="/edicts", tags=["edicts"])


# --- Edict endpoints ---


def _validate_network_runtime(runtime: object) -> None:
    """api_request_write_hosts 必须是 api_request_hosts 的子集。"""
    allow = set(getattr(runtime, "api_request_hosts", None) or [])
    write = set(getattr(runtime, "api_request_write_hosts", None) or [])
    extra = write - allow
    if extra:
        raise HTTPException(
            400,
            f"api_request_write_hosts must be ⊆ api_request_hosts; extra: {sorted(extra)}",
        )


@edicts_router.post("", response_model=ApiResponse, status_code=202)
async def create_edict(body: EdictCreateRequest, request: Request):
    storage: Storage = request.app.state.storage
    event_bus: EventBus = request.app.state.event_bus

    # Idempotency check: (submitter, idempotency_key) dedup
    if body.idempotency_key:
        existing = storage.find_edict_by_idempotency_key(
            body.submitter,
            body.idempotency_key,
        )
        if existing:
            return ApiResponse(
                success=True,
                data=existing.model_dump(mode="json"),
                metadata={"deduplicated": True},
            )

    title = title_from_goal(body.goal, body.title)
    edict_kwargs: dict = {"title": title, "goal": body.goal, "context": body.context}
    if body.idempotency_key:
        edict_kwargs["idempotency_key"] = body.idempotency_key
    if body.submitter:
        edict_kwargs["submitter"] = body.submitter
    if body.priority:
        edict_kwargs["priority"] = body.priority
    if body.review_policy:
        edict_kwargs["review_policy"] = body.review_policy
    if body.constraints:
        edict_kwargs["constraints"] = body.constraints
    if body.output_format:
        edict_kwargs["output_format"] = body.output_format
    if body.runtime is not None:
        _validate_network_runtime(body.runtime)
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
    if body.acceptance is not None:
        edict_kwargs["acceptance"] = body.acceptance
    if body.execution_profile != "foreground":
        edict_kwargs["execution_profile"] = body.execution_profile
    edict = Edict(**edict_kwargs)
    logger.debug(
        "[API] Edict %s: submitted goal=%.100s, priority=%s, assigned=%s",
        edict.id,
        edict.goal,
        edict.priority,
        edict.assigned_persona_id,
    )

    # Fire-and-forget: 不阻塞 API 响应，事件链在后台异步执行
    submit_new_edict(storage, event_bus, edict, producer="gateway")

    return ApiResponse(
        success=True,
        data=edict.model_dump(mode="json"),
    )


@edicts_router.post("/parse", response_model=ApiResponse)
async def parse_edict_nl(body: ParseEdictRequest, request: Request):
    """自然语言 → 敕令草稿（只读，不落库）。前端拿去预填表单。"""
    from tianshu.gateway.edict_parse import (
        ParseEdictError,
        build_parse_messages,
        coerce_draft,
        parse_llm_json,
    )

    pm = request.app.state.provider_manager
    try:
        client = pm.get_client(config_name_override="deepseek-flash")
    except Exception:
        client = pm.get_client()

    messages = build_parse_messages(body.text)
    try:
        resp = await client.chat(messages)
        raw = parse_llm_json(resp.content or "")
    except ParseEdictError as e:
        raise HTTPException(422, f"没太理解这句话，请换种说法或手动填写：{e}") from e
    except Exception as e:
        raise HTTPException(422, f"解析服务暂不可用：{type(e).__name__}") from e

    draft, notes = coerce_draft(raw)
    return ApiResponse(success=True, data={"draft": draft, "notes": notes})


@edicts_router.get("")
def list_edicts(
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


@edicts_router.get("/{edict_id}")
def get_edict(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    return ApiResponse(success=True, data=edict.model_dump(mode="json"))


@edicts_router.patch("/{edict_id}", response_model=ApiResponse)
def update_edict(edict_id: str, body: EdictUpdateRequest, request: Request):
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    if edict.status != EdictStatus.OPEN:
        raise HTTPException(status_code=400, detail="只有进行中的敕令可以编辑")
    storage.update_edict(edict_id, title=body.title, goal=body.goal, context=body.context)
    storage.append_event(
        edict_id,
        None,
        "edict.updated",
        {
            "goal": body.goal,
            "context": body.context,
        },
    )
    updated = storage.get_edict(edict_id)
    return ApiResponse(success=True, data=updated.model_dump(mode="json"))


@edicts_router.delete("/{edict_id}", response_model=ApiResponse)
def delete_edict(edict_id: str, request: Request):
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


@edicts_router.post("/{edict_id}/pause", response_model=ApiResponse)
def pause_edict(edict_id: str, request: Request):
    """暂停一个 active 状态的 edict。complete/winding_down 状态返回 409。幂等：已 paused 直接返回 200。"""
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    phase = edict.runtime.lifecycle_phase
    if phase == "complete":
        raise HTTPException(status_code=409, detail="cannot pause a completed edict")
    if phase == "winding_down":
        raise HTTPException(
            status_code=409,
            detail="cannot pause a winding_down edict; let it finish or wait for completion",
        )
    if phase == "paused":
        return ApiResponse(success=True, data={"id": edict_id, "lifecycle_phase": "paused"})
    storage.update_edict_lifecycle_phase(edict_id, "paused")
    storage.append_event(
        edict_id,
        None,
        "edict.lifecycle.changed",
        {
            "from_phase": phase,
            "to_phase": "paused",
            "reason": "user_request",
        },
    )
    return ApiResponse(success=True, data={"id": edict_id, "lifecycle_phase": "paused"})


@edicts_router.post("/{edict_id}/resume", response_model=ApiResponse)
def resume_edict(edict_id: str, request: Request):
    """恢复一个 paused 状态的 edict 为 active。complete/winding_down 状态返回 409。幂等：已 active 直接返回 200。"""
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    phase = edict.runtime.lifecycle_phase
    if phase == "complete":
        raise HTTPException(status_code=409, detail="cannot resume a completed edict")
    if phase == "winding_down":
        raise HTTPException(
            status_code=409,
            detail="cannot resume a winding_down edict; it must finish first",
        )
    if phase == "active":
        return ApiResponse(success=True, data={"id": edict_id, "lifecycle_phase": "active"})
    storage.update_edict_lifecycle_phase(edict_id, "active")
    storage.append_event(
        edict_id,
        None,
        "edict.lifecycle.changed",
        {
            "from_phase": phase,
            "to_phase": "active",
            "reason": "user_request",
        },
    )
    return ApiResponse(success=True, data={"id": edict_id, "lifecycle_phase": "active"})


@edicts_router.get("/{edict_id}/memorial")
def get_memorial_by_edict(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    if not storage.get_edict(edict_id):
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    memorial = storage.get_memorial_by_edict(edict_id)
    return ApiResponse(
        success=True,
        data=memorial.model_dump(mode="json") if memorial else None,
    )


@edicts_router.get("/{edict_id}/memorials")
def list_edict_memorials(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    memorials = storage.list_memorials_by_edict(edict_id)
    return ApiResponse(success=True, data=[m.model_dump(mode="json") for m in memorials])


MAX_LATEST_MEMORIALS_BATCH = 200


class LatestMemorialsRequest(BaseModel):
    edict_ids: list[str]


@edicts_router.post("/latest-memorials")
def get_latest_memorials_batch(body: LatestMemorialsRequest, request: Request):
    """批量取多个 edict 的最新奏折——御书房合并后消除 useEdictLatestMemorials 的 N+1 请求。"""
    if len(body.edict_ids) > MAX_LATEST_MEMORIALS_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"edict_ids exceeds max of {MAX_LATEST_MEMORIALS_BATCH}",
        )
    storage: Storage = request.app.state.storage
    data: dict[str, dict | None] = {}
    for edict_id in body.edict_ids:
        memorial = storage.get_memorial_by_edict(edict_id)
        data[edict_id] = memorial.model_dump(mode="json") if memorial else None
    return ApiResponse(success=True, data=data)


@edicts_router.post("/{edict_id}/plan/approve", response_model=ApiResponse)
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
    storage.append_event(
        edict_id,
        memorial_id,
        "plan.approved",
        {
            "actor": "human",
            "plan": plan_payload.get("plan", {}),
        },
    )

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


@edicts_router.post("/{edict_id}/plan/reject", response_model=ApiResponse)
def reject_plan(edict_id: str, request: Request):
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

    storage.append_event(
        edict_id,
        memorial_id,
        "plan.rejected",
        {
            "actor": "human",
        },
    )
    return ApiResponse(success=True, data={"status": "rejected"})


@edicts_router.post("/{edict_id}/follow-up", response_model=ApiResponse, status_code=202)
async def follow_up_edict(edict_id: str, body: FollowUpRequest, request: Request):
    storage: Storage = request.app.state.storage
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

    runtime_override_dict: dict | None = None
    if body.runtime_override is not None:
        _validate_network_runtime(body.runtime_override)
        rt_data = {k: v for k, v in body.runtime_override.model_dump().items() if v is not None}
        runtime_override_dict = rt_data or None

    memorial = Memorial(
        edict_id=edict_id,
        instruction=body.instruction,
        status=TaskStatus.SUBMITTED,
        runtime_override=runtime_override_dict,
        acceptance_override=body.acceptance_override,
    )
    storage.save_memorial(memorial)
    storage.append_event(
        edict.id,
        memorial.id,
        "followup.submitted",
        {
            "instruction": body.instruction,
            "has_runtime_override": runtime_override_dict is not None,
            "has_acceptance_override": body.acceptance_override is not None,
        },
    )

    import asyncio

    task = asyncio.create_task(
        executor.execute_edict(
            edict, memorial=memorial, history=history, user_content=body.instruction
        )
    )
    executor.running_tasks.add(task)
    task.add_done_callback(executor.running_tasks.discard)

    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))


@edicts_router.patch("/{edict_id}/status", response_model=ApiResponse)
def update_edict_status(edict_id: str, body: EdictStatusUpdateRequest, request: Request):
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
    storage.update_edict_status(edict_id, body.status.value)
    if body.status.value == "cancelled":
        storage.update_edict_lifecycle_phase(edict_id, "complete")
    storage.append_event(edict_id, None, "edict.closed", {"status": body.status.value})
    edict.status = body.status
    return ApiResponse(success=True, data=edict.model_dump(mode="json"))


@edicts_router.get("/{edict_id}/events")
def get_events(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    events = storage.get_events(edict_id)
    return ApiResponse(success=True, data=events)


@edicts_router.get("/{edict_id}/iterations")
def get_outer_loop_iterations(edict_id: str, request: Request):
    """长任务 outer loop 的迭代记录（仅 acceptance != None 的 edict 有数据）。"""
    storage: Storage = request.app.state.storage
    rows = storage.get_outer_loop_iterations(edict_id)
    return ApiResponse(success=True, data=rows)


class OuterLoopDecisionRequest(BaseModel):
    action: Literal["continue", "accept_as_is", "abort", "modify_acceptance"]
    feedback: str | None = None
    new_acceptance: dict | None = None


@edicts_router.get("/outer-loop/pending")
async def list_outer_loop_pending(request: Request):
    """所有 L3 待审批的长任务列表（含 best_output / critic_feedback / 轮数等）。"""
    am = request.app.state.approval_manager
    items = am.list_pending_outer_loop()
    return ApiResponse(success=True, data=items)


@edicts_router.post("/{edict_id}/outer-loop/decide")
async def submit_outer_loop_decision_api(
    edict_id: str,
    body: OuterLoopDecisionRequest,
    request: Request,
):
    """前端 L3 审批 Modal 提交决策入口。"""
    from tianshu.executor.orchestrator.human_decision import HumanDecision
    from tianshu.models.acceptance import AcceptanceCriteria

    am = request.app.state.approval_manager
    new_acceptance = (
        AcceptanceCriteria.model_validate(body.new_acceptance) if body.new_acceptance else None
    )
    decision = HumanDecision(
        action=body.action,
        feedback=body.feedback,
        new_acceptance=new_acceptance,
    )
    triggered = am.submit_outer_loop_decision(edict_id, decision)
    if not triggered:
        raise HTTPException(
            status_code=404,
            detail=f"Edict '{edict_id}' is not awaiting an outer-loop decision",
        )
    return ApiResponse(success=True, data={"edict_id": edict_id, "action": body.action})


@edicts_router.get("/{edict_id}/supervision-reports")
def get_supervision_reports(edict_id: str, request: Request):
    """长任务终态后由所有 critic persona 生成的监督报告列表（4 章节 × N 监督官）。"""
    storage: Storage = request.app.state.storage
    rows = storage.get_supervision_reports(edict_id)
    if not rows:
        return ApiResponse(success=True, data=[])
    import json

    reports = [json.loads(r["report_json"]) for r in rows]
    return ApiResponse(success=True, data=reports)


@edicts_router.get("/{edict_id}/supervision-report")
def get_supervision_report_legacy(edict_id: str, request: Request):
    """兼容旧 endpoint —— 返第一个监督报告（已废弃，建议用 /supervision-reports）。"""
    storage: Storage = request.app.state.storage
    row = storage.get_supervision_report(edict_id)
    if not row:
        raise HTTPException(status_code=404, detail="supervision report not found")
    import json

    report = json.loads(row["report_json"])
    return ApiResponse(success=True, data=report)


@edicts_router.get("/{edict_id}/policy_events")
def list_policy_events(edict_id: str, request: Request):
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
        data.append(
            {
                "id": row.get("id"),
                "memorial_id": row.get("memorial_id"),
                "type": typ,
                "payload": row.get("payload") or {},
                "created_at": row.get("created_at"),
            }
        )
    return ApiResponse(success=True, data={"events": data})
