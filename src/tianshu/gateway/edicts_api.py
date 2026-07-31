"""/api/edicts 路由：敕令 CRUD、生命周期（pause/resume）、计划审批、follow-up、outer-loop 决策、监督报告。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tianshu.application.edict_detail import EdictDetailNotFound, EdictDetailUnavailable
from tianshu.application.edicts import (
    IdempotencyConflict,
    SubmitEdictCommand,
    validate_idempotency_key,
)
from tianshu.authz import can_access_submitter, scoped_submitter
from tianshu.executor.capabilities import (
    MandatoryCapabilityMismatch,
    get_executor_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.workspace_policy import workspace_policy_mismatches
from tianshu.executor.workspace_runtime import WORKSPACE_MAIN_SOURCE_ID
from tianshu.gateway.auth import get_auth_context
from tianshu.gateway.decisions_api import (
    raise_decision_error,
    raise_decision_service_error,
    require_owned_decision,
)
from tianshu.gateway.ownership import require_owned_edict
from tianshu.governance.decision_service import DecisionServiceError
from tianshu.models import (
    ApiResponse,
    Edict,
    EdictCreateRequest,
    EdictStatus,
    EdictStatusUpdateRequest,
    EdictUpdateRequest,
    FollowUpRequest,
    TaskStatus,
)
from tianshu.models.api import ParseEdictRequest
from tianshu.models.decision import DecisionKind, ResolveDecisionCommand
from tianshu.models.edict import (
    EdictRuntime,
    EdictSchedule,
    LongRunningScheduleError,
    PolicyProfilePayload,
    title_from_goal,
    validate_long_running_schedule,
)
from tianshu.models.governance_contract import (
    AcceptancePolicyV1,
    LegacyEdictGovernanceMapper,
    RequestedGovernanceContractV1,
    acceptance_policy_to_legacy,
)
from tianshu.storage import EdictArchiveConflict, Storage

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


def _validate_user_schedule(schedule: EdictSchedule) -> None:
    """Reject incomplete or surprising user schedules before persistence."""
    if schedule.type == "immediate":
        return
    if schedule.type == "once":
        if schedule.at is None:
            raise HTTPException(422, {"code": "schedule_time_required"})
        if schedule.at.astimezone(UTC) <= datetime.now(UTC):
            raise HTTPException(422, {"code": "schedule_time_must_be_future"})
        return
    if schedule.type == "cron":
        if not schedule.cron:
            raise HTTPException(422, {"code": "cron_expression_required"})
        return
    if not schedule.interval_seconds or schedule.interval_seconds < 1:
        raise HTTPException(422, {"code": "interval_seconds_required"})


def _validate_long_running_request(body: EdictCreateRequest) -> None:
    contract = body.governance_contract
    outer_loop = body.acceptance is not None or (
        contract is not None and contract.acceptance != AcceptancePolicyV1()
    )
    try:
        validate_long_running_schedule(
            body.schedule,
            outer_loop=outer_loop,
            execution_profile=body.execution_profile,
        )
    except LongRunningScheduleError as exc:
        raise HTTPException(
            422,
            {
                "code": exc.code,
                "detail": str(exc),
            },
        ) from exc


def _workspace_source_id(request: Request) -> str:
    del request
    return WORKSPACE_MAIN_SOURCE_ID


def _idempotency_key_from_request(body: EdictCreateRequest, request: Request) -> str:
    header_key = request.headers.get("Idempotency-Key")
    body_key = body.idempotency_key
    if header_key is None and body_key is None:
        raise HTTPException(422, {"code": "idempotency_key_required"})
    for source, key in (("header", header_key), ("body", body_key)):
        if key is None:
            continue
        try:
            validate_idempotency_key(key)
        except ValueError as exc:
            raise HTTPException(
                422,
                {
                    "code": "invalid_idempotency_key",
                    "source": source,
                    "reason": str(exc),
                },
            ) from exc
    if header_key is not None and body_key is not None and header_key != body_key:
        raise HTTPException(
            422,
            {
                "code": "idempotency_key_mismatch",
                "header": header_key,
                "body": body_key,
            },
        )
    if header_key is not None:
        return header_key
    assert body_key is not None
    return body_key


def _runtime_from_request(body: EdictCreateRequest, request: Request) -> EdictRuntime:
    agent_cfg = request.app.state.config_manager.agent_config
    rt_data: dict = {
        "timeout_seconds": agent_cfg.agent_timeout_seconds,
        "max_iterations": agent_cfg.agent_max_iterations,
        "max_concurrency": agent_cfg.agent_max_concurrency,
        "retry_limit": agent_cfg.agent_retry_limit,
    }
    if agent_cfg.agent_token_budget:
        rt_data["token_budget"] = agent_cfg.agent_token_budget
    if agent_cfg.agent_cost_budget_cny:
        rt_data["cost_budget_cny"] = agent_cfg.agent_cost_budget_cny

    contract = body.governance_contract
    if contract is not None:
        executor_options = {item.name: item.value for item in contract.executor.config}
        rt_data.update(
            {
                "executor": contract.executor.adapter_id,
                "executor_model": contract.executor.model,
                "timeout_seconds": contract.budget.wall_clock_seconds,
                "max_iterations": contract.budget.max_iterations,
                "max_concurrency": contract.budget.max_concurrency,
                "retry_limit": contract.budget.retry_limit,
                "token_budget": contract.budget.token_limit,
                "cost_budget_cny": (
                    float(contract.budget.cost_limit_cny)
                    if contract.budget.cost_limit_cny is not None
                    else None
                ),
                "approval_required_tools": list(contract.permissions.approval_required_tools),
                "api_request_hosts": contract.network.allowed_hosts,
                "api_request_write_hosts": contract.network.write_hosts,
                "fetch_engine_override": executor_options.get("fetch_engine_override"),
                "search_provider_override": executor_options.get("search_provider_override"),
            }
        )
        permissions = contract.permissions
        if (
            permissions.policy_profile_name
            or permissions.allowed_paths
            or permissions.allowed_bash_prefixes
            or permissions.tier_overrides
            or permissions.auto_approve_max_tier != 1
            or permissions.expires_after_seconds is not None
        ):
            rt_data["policy_profile"] = PolicyProfilePayload(
                allowed_paths=list(permissions.allowed_paths),
                allowed_bash_prefixes=list(permissions.allowed_bash_prefixes),
                tier_overrides=dict(permissions.tier_overrides),
                auto_approve_max_tier=permissions.auto_approve_max_tier,
                expires_after_seconds=permissions.expires_after_seconds,
                template_name=permissions.policy_profile_name,
            )
    if body.runtime:
        rt_data.update(
            {
                key: value
                for key, value in body.runtime.model_dump(exclude_unset=True).items()
                if value is not None
            }
        )
    outer_loop_requested = body.acceptance is not None or (
        body.governance_contract is not None
        and body.governance_contract.acceptance != AcceptancePolicyV1()
    )
    if outer_loop_requested:
        if body.governance_contract is not None and body.governance_contract.budget.retry_limit < 1:
            raise HTTPException(
                422,
                {
                    "code": "outer_loop_recovery_required",
                    "detail": "深度任务至少需要一次租约恢复机会",
                },
            )
        # 长程任务默认必须能从一次租约丢失或进程重启中恢复。普通任务仍沿用全局配置。
        rt_data["retry_limit"] = max(1, int(rt_data.get("retry_limit") or 0))
    runtime = EdictRuntime(**rt_data)
    _validate_network_runtime(runtime)
    return runtime


def _requested_contract_from_body(
    body: EdictCreateRequest,
    request: Request,
    runtime: EdictRuntime,
) -> RequestedGovernanceContractV1:
    if body.governance_contract is not None:
        return body.governance_contract
    legacy = Edict(
        goal=body.goal,
        context=body.context,
        constraints=body.constraints or [],
        output_format=body.output_format,
        review_policy=body.review_policy or "never",
        runtime=runtime,
        acceptance=body.acceptance,
    )
    return LegacyEdictGovernanceMapper.from_edict(
        legacy,
        default_workspace_id=_workspace_source_id(request),
    )


def _execution_mode_from_body(
    body: EdictCreateRequest,
    contract: RequestedGovernanceContractV1,
) -> Literal["single", "outer_loop"]:
    if body.acceptance is not None or contract.acceptance != AcceptancePolicyV1():
        return "outer_loop"
    return "single"


def _governance_preview(
    contract: RequestedGovernanceContractV1,
    *,
    execution_mode: Literal["single", "outer_loop"],
) -> dict:
    try:
        manifest = get_executor_manifest(contract.executor.adapter_id)
    except KeyError as exc:
        raise HTTPException(
            422,
            {
                "code": "unknown_executor_adapter",
                "adapter_id": contract.executor.adapter_id,
            },
        ) from exc
    probe = probe_host_capabilities()
    workspace_mismatches = workspace_policy_mismatches(
        contract.workspace,
        contract.recovery,
    )
    if workspace_mismatches:
        return {
            "compatible": False,
            "requested_contract": contract.model_dump(mode="json"),
            "requested_contract_hash": contract.content_hash,
            "effective_contract": None,
            "mandatory_mismatches": [],
            "workspace_policy_mismatches": [item.as_dict() for item in workspace_mismatches],
            "execution_mode": execution_mode,
            "execution_mode_mismatches": [],
            "advisory_gaps": [],
            "executor_level": manifest.level.value,
            "experimental": manifest.experimental,
            "manifest_hash": manifest.content_hash,
            "runtime_probe_id": probe.probe_id,
        }
    if execution_mode not in manifest.execution_modes:
        return {
            "compatible": False,
            "requested_contract": contract.model_dump(mode="json"),
            "requested_contract_hash": contract.content_hash,
            "effective_contract": None,
            "mandatory_mismatches": [],
            "workspace_policy_mismatches": [],
            "execution_mode": execution_mode,
            "execution_mode_mismatches": [
                {
                    "adapter_id": manifest.adapter_id,
                    "requested_mode": execution_mode,
                    "supported_modes": list(manifest.execution_modes),
                }
            ],
            "advisory_gaps": [],
            "executor_level": manifest.level.value,
            "experimental": manifest.experimental,
            "manifest_hash": manifest.content_hash,
            "runtime_probe_id": probe.probe_id,
        }
    try:
        effective = resolve_governance_contract(contract, manifest, probe)
    except MandatoryCapabilityMismatch as exc:
        return {
            "compatible": False,
            "requested_contract": contract.model_dump(mode="json"),
            "requested_contract_hash": contract.content_hash,
            "effective_contract": None,
            "mandatory_mismatches": [item.model_dump(mode="json") for item in exc.mismatches],
            "workspace_policy_mismatches": [],
            "execution_mode": execution_mode,
            "execution_mode_mismatches": [],
            "advisory_gaps": [],
            "executor_level": manifest.level.value,
            "experimental": manifest.experimental,
            "manifest_hash": manifest.content_hash,
            "runtime_probe_id": probe.probe_id,
        }
    return {
        "compatible": True,
        "requested_contract": contract.model_dump(mode="json"),
        "requested_contract_hash": contract.content_hash,
        "effective_contract": effective.model_dump(mode="json"),
        "mandatory_mismatches": [],
        "workspace_policy_mismatches": [],
        "execution_mode": execution_mode,
        "execution_mode_mismatches": [],
        "advisory_gaps": list(effective.unsupported_advisory),
        "executor_level": manifest.level.value,
        "experimental": manifest.experimental,
        "manifest_hash": manifest.content_hash,
        "runtime_probe_id": probe.probe_id,
    }


@edicts_router.post("", response_model=ApiResponse, status_code=202)
async def create_edict(body: EdictCreateRequest, request: Request, response: Response):
    storage: Storage = request.app.state.storage
    auth = get_auth_context(request)
    submitter = auth.principal.id
    idempotency_key = _idempotency_key_from_request(body, request)
    # 已接受请求的重放必须仍能返回原结果，即使一次性执行时间此刻已过去。
    if storage.find_edict_by_idempotency_key(submitter, idempotency_key) is None:
        _validate_user_schedule(body.schedule)
        _validate_long_running_request(body)

    runtime = _runtime_from_request(body, request)
    requested_contract = _requested_contract_from_body(body, request, runtime)
    execution_mode = _execution_mode_from_body(body, requested_contract)

    preview = _governance_preview(requested_contract, execution_mode=execution_mode)
    if not preview["compatible"]:
        mode_mismatches = preview["execution_mode_mismatches"]
        workspace_mismatches = preview["workspace_policy_mismatches"]
        raise HTTPException(
            422,
            {
                "code": (
                    "governance_workspace_policy_mismatch"
                    if workspace_mismatches
                    else (
                        "governance_execution_mode_mismatch"
                        if mode_mismatches
                        else "governance_capability_mismatch"
                    )
                ),
                "mismatches": (
                    workspace_mismatches or mode_mismatches or preview["mandatory_mismatches"]
                ),
            },
        )

    title = title_from_goal(body.goal, body.title)
    edict_kwargs: dict = {
        "title": title,
        "goal": body.goal,
        "context": requested_contract.objective.context,
        "submitter": submitter,
        "constraints": list(requested_contract.objective.constraints),
        "output_format": requested_contract.objective.output_format,
        "review_policy": requested_contract.permissions.review_policy,
        "schedule": body.schedule,
        "runtime": runtime,
        "governance_contract": requested_contract,
        "idempotency_key": idempotency_key,
    }
    if body.priority:
        edict_kwargs["priority"] = body.priority
    # 六科给事中·封驳(迭代 7):提交预检——超长封还 / 成本超阈升 plan_review 票拟(D9)
    from tianshu.executor.liuke import Liuke

    precheck = Liuke(storage, request.app.state.config_manager).precheck(
        body.goal, runtime.cost_budget_cny
    )
    if precheck.verdict == "reject":
        raise HTTPException(422, f"六科封还:{precheck.reason}")
    if precheck.verdict == "plan_review":
        edict_kwargs["plan_review"] = True
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
    elif requested_contract.acceptance != AcceptancePolicyV1():
        edict_kwargs["acceptance"] = acceptance_policy_to_legacy(requested_contract.acceptance)
    if body.acceptance is not None or requested_contract.acceptance != AcceptancePolicyV1():
        # API 继续接受三个历史 profile，但新建深度任务统一落为可检查点模式。
        edict_kwargs["execution_profile"] = "checkpointed"
    elif body.execution_profile != "foreground":
        edict_kwargs["execution_profile"] = body.execution_profile
    edict = Edict(**edict_kwargs)
    logger.debug(
        "[API] Edict %s: submitted goal=%.100s, priority=%s, assigned=%s",
        edict.id,
        edict.goal,
        edict.priority,
        edict.assigned_persona_id,
    )

    service = request.app.state.edict_application_service
    command = SubmitEdictCommand(
        edict=edict,
        idempotency_key=idempotency_key,
        requested_contract=requested_contract,
        extra_payload={"via": "http"},
    )
    try:
        result = service.submit(
            command,
            auth=auth,
            producer=f"gateway:{submitter}",
            correlation_id=auth.correlation_id,
        )
    except IdempotencyConflict as conflict:
        raise HTTPException(
            409,
            {
                "code": "idempotency_conflict",
                "idempotency_key": conflict.idempotency_key,
            },
        ) from conflict

    response.status_code = 200 if result.deduplicated else 202

    return ApiResponse(
        success=True,
        data=result.edict.model_dump(mode="json"),
        metadata={
            "deduplicated": result.deduplicated,
            "idempotency_key": idempotency_key,
            "request_hash": result.request_hash,
            "event_id": result.event_id,
            "memorial_id": result.memorial.id,
        },
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
    client = pm.get_client_for_slot("edict_parse")

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


@edicts_router.post("/governance/preview", response_model=ApiResponse)
def preview_governance_contract(body: EdictCreateRequest, request: Request):
    _validate_user_schedule(body.schedule)
    _validate_long_running_request(body)
    runtime = _runtime_from_request(body, request)
    contract = _requested_contract_from_body(body, request, runtime)
    execution_mode = _execution_mode_from_body(body, contract)
    return ApiResponse(
        success=True,
        data=_governance_preview(contract, execution_mode=execution_mode),
    )


@edicts_router.get("")
def list_edicts(
    request: Request,
    status: EdictStatus | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    storage: Storage = request.app.state.storage
    auth = get_auth_context(request)
    edicts, total = storage.list_edicts(
        status=status.value if status else None,
        search=search or None,
        limit=limit,
        offset=offset,
        submitter=scoped_submitter(auth),
    )
    return ApiResponse(
        success=True,
        data=[e.model_dump(mode="json") for e in edicts],
        metadata={"total": total, "limit": limit, "offset": offset},
    )


@edicts_router.get("/{edict_id}")
def get_edict(edict_id: str, request: Request):
    edict = require_owned_edict(request, edict_id)
    return ApiResponse(success=True, data=edict.model_dump(mode="json"))


@edicts_router.get("/{edict_id}/detail")
def get_edict_detail(edict_id: str, request: Request) -> dict[str, object]:
    auth = get_auth_context(request)
    not_found_detail = {
        "code": "edict_detail_not_found",
        "message": "edict detail not found",
        "correlation_id": auth.correlation_id,
    }
    require_owned_edict(
        request,
        edict_id,
        context=auth,
        not_found_detail=not_found_detail,
    )
    try:
        snapshot = request.app.state.edict_detail_service.get_snapshot(auth, edict_id)
    except EdictDetailNotFound:
        raise HTTPException(404, not_found_detail) from None
    except EdictDetailUnavailable:
        raise HTTPException(
            503,
            {
                "code": "edict_detail_unavailable",
                "message": "edict detail sources are unavailable",
                "correlation_id": auth.correlation_id,
            },
        ) from None
    return {
        "data": snapshot.model_dump(mode="json"),
        "correlation_id": auth.correlation_id,
    }


@edicts_router.patch("/{edict_id}", response_model=ApiResponse)
def update_edict(edict_id: str, body: EdictUpdateRequest, request: Request):
    storage: Storage = request.app.state.storage
    edict = require_owned_edict(request, edict_id)
    if edict.status != EdictStatus.OPEN:
        raise HTTPException(status_code=400, detail="只有未结案的敕令可以编辑")
    contract = edict.governance_contract
    if contract is not None:
        objective_changed = (body.goal is not None and body.goal != contract.objective.goal) or (
            body.context is not None and body.context != contract.objective.context
        )
        if objective_changed:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "governance_contract_frozen",
                    "message": "goal/context are frozen by the requested governance contract",
                },
            )
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
    assert updated is not None
    return ApiResponse(success=True, data=updated.model_dump(mode="json"))


@edicts_router.delete("/{edict_id}", response_model=ApiResponse)
async def delete_edict(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    require_owned_edict(request, edict_id)
    # DELETE is a user-facing tombstone operation.  Edict identity,
    # idempotency records, execution events and governance evidence remain
    # addressable; only normal list views hide the archived record.
    try:
        cancelled_job_ids = storage.tombstone_edict(edict_id)
    except EdictArchiveConflict:
        raise HTTPException(
            status_code=409,
            detail="无法删除尚有未结束执行的敕令，请先取消执行",
        ) from None
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found") from None
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None and cancelled_job_ids:
        await scheduler.discard_cancelled_jobs(cancelled_job_ids)
    logger.info("[API] Edict %s: archived with governance history retained", edict_id)
    return ApiResponse(success=True, data={"id": edict_id, "archived": True})


@edicts_router.post("/{edict_id}/pause", response_model=ApiResponse)
def pause_edict(edict_id: str, request: Request):
    """请求深度任务在当前轮结束后暂停。"""
    storage: Storage = request.app.state.storage
    edict = require_owned_edict(request, edict_id)
    if edict.acceptance is None:
        raise HTTPException(
            status_code=409,
            detail="普通任务不支持暂停；可取消任务，或创建深度任务以使用轮次边界暂停",
        )
    if edict.status is not EdictStatus.OPEN or not storage.has_unfinished_memorials(edict_id):
        raise HTTPException(status_code=409, detail="只有尚未结束的开放任务可以暂停")
    phase = edict.runtime.lifecycle_phase
    if phase == "complete":
        raise HTTPException(status_code=409, detail="cannot pause a completed edict")
    if phase == "winding_down":
        raise HTTPException(
            status_code=409,
            detail="cannot pause a winding_down edict; let it finish or wait for completion",
        )
    if phase == "paused":
        return ApiResponse(
            success=True,
            data={
                "id": edict_id,
                "lifecycle_phase": "paused",
                "effective_after": "current_round",
            },
        )
    storage.update_edict_lifecycle_phase(edict_id, "paused")
    storage.append_event(
        edict_id,
        None,
        "edict.lifecycle.changed",
        {
            "from_phase": phase,
            "to_phase": "paused",
            "reason": "user_request_after_current_round",
        },
    )
    return ApiResponse(
        success=True,
        data={
            "id": edict_id,
            "lifecycle_phase": "paused",
            "effective_after": "current_round",
        },
    )


class SteerRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


@edicts_router.post("/{edict_id}/steer", response_model=ApiResponse, status_code=202)
async def steer_edict(edict_id: str, body: SteerRequest, request: Request):
    """向运行中的长任务中途注入一条 steer 指示——下一轮 actor 边界吸收(迭代 5)。"""
    from datetime import UTC, datetime

    from ulid import ULID

    storage: Storage = request.app.state.storage
    edict = require_owned_edict(request, edict_id)
    if edict.status != EdictStatus.OPEN or edict.acceptance is None:
        raise HTTPException(status_code=409, detail="只有运行中的深度任务可以补充要求")
    if edict.runtime.lifecycle_phase != "active" or not storage.has_active_memorials(edict_id):
        raise HTTPException(status_code=409, detail="深度任务当前未运行，无法补充下一轮要求")
    storage.save_steer(str(ULID()), edict_id, body.note.strip(), datetime.now(UTC).isoformat())
    bus = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        from tianshu.models.events import make_event

        await bus.emit(
            make_event(
                "edict.steer.submitted",
                edict_id=edict_id,
                producer="gateway",
                payload={"note": body.note[:200]},
            )
        )
    return ApiResponse(success=True, data={"id": edict_id, "steered": True})


@edicts_router.post("/{edict_id}/resume", response_model=ApiResponse)
def resume_edict(edict_id: str, request: Request):
    """恢复一个 paused 状态的 edict 为 active。complete/winding_down 状态返回 409。幂等：已 active 直接返回 200。"""
    storage: Storage = request.app.state.storage
    edict = require_owned_edict(request, edict_id)
    if edict.acceptance is None:
        raise HTTPException(status_code=409, detail="普通任务不支持暂停或恢复")
    if edict.status is not EdictStatus.OPEN or not storage.has_unfinished_memorials(edict_id):
        raise HTTPException(status_code=409, detail="只有尚未结束的开放任务可以恢复")
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
    require_owned_edict(request, edict_id)
    memorial = storage.get_memorial_by_edict(edict_id)
    return ApiResponse(
        success=True,
        data=memorial.model_dump(mode="json") if memorial else None,
    )


@edicts_router.get("/{edict_id}/memorials")
def list_edict_memorials(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    require_owned_edict(request, edict_id)
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
        require_owned_edict(request, edict_id)
        memorial = storage.get_memorial_by_edict(edict_id)
        data[edict_id] = memorial.model_dump(mode="json") if memorial else None
    return ApiResponse(success=True, data=data)


class PlanReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_request_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_reason(self):
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


def _resolve_plan_review(
    edict_id: str,
    body: PlanReviewDecisionRequest,
    request: Request,
    *,
    action: Literal["approve", "reject"],
) -> ApiResponse:
    context = get_auth_context(request)
    require_owned_edict(request, edict_id, context=context)
    existing = require_owned_decision(request, context, body.decision_request_id)
    if existing.request.edict_id != edict_id:
        raise_decision_error(context, 422, "decision_identity_conflict")
    if existing.request.kind is not DecisionKind.PLAN_REVIEW:
        raise_decision_error(context, 422, "invalid_decision_kind")
    try:
        resolution = request.app.state.decision_service.resolve(
            body.decision_request_id,
            ResolveDecisionCommand(
                action=action,
                reason=body.reason.strip(),
                payload={"schema_version": 1},
                expected_version=body.expected_version,
            ),
            auth=context,
        )
    except DecisionServiceError as error:
        raise_decision_service_error(context, error)
    record = require_owned_decision(request, context, body.decision_request_id)
    return ApiResponse(
        success=True,
        data={
            "action": resolution.action,
            "decision_request_id": resolution.decision_request_id,
            "status": record.request.status.value,
            "version": record.request.version,
            "resolution": resolution.model_dump(mode="json"),
            "record": record.model_dump(mode="json"),
        },
    )


@edicts_router.post("/{edict_id}/plan/approve", response_model=ApiResponse)
def approve_plan(edict_id: str, body: PlanReviewDecisionRequest, request: Request):
    """Resolve a pending durable plan review; projection triggers execution later."""
    return _resolve_plan_review(edict_id, body, request, action="approve")


@edicts_router.post("/{edict_id}/plan/reject", response_model=ApiResponse)
def reject_plan(edict_id: str, body: PlanReviewDecisionRequest, request: Request):
    """Resolve a pending durable plan review as rejected."""
    return _resolve_plan_review(edict_id, body, request, action="reject")


@edicts_router.post("/{edict_id}/follow-up", response_model=ApiResponse, status_code=202)
async def follow_up_edict(edict_id: str, body: FollowUpRequest, request: Request):
    edict = require_owned_edict(request, edict_id)
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is None:
        raise HTTPException(422, {"code": "idempotency_key_required"})
    try:
        validate_idempotency_key(idempotency_key)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_idempotency_key"}) from exc

    if edict.status != EdictStatus.OPEN:
        raise HTTPException(status_code=400, detail="敕令已结案，无法继续")

    runtime_override_dict: dict | None = None
    if body.runtime_override is not None:
        _validate_network_runtime(body.runtime_override)
        rt_data = {
            k: v
            for k, v in body.runtime_override.model_dump(exclude_unset=True).items()
            if v is not None
        }
        runtime_override_dict = rt_data or None

    from tianshu.application.managed_run_ingress import ManagedRunBusy, ManagedRunCommand

    try:
        result = await request.app.state.managed_run_ingress.start(
            ManagedRunCommand(
                edict_id=edict_id,
                idempotency_key=f"api:{idempotency_key}",
                instruction=body.instruction,
                runtime_override=runtime_override_dict,
                acceptance_override=body.acceptance_override,
                event_type="followup.submitted",
                event_payload={
                    "instruction": body.instruction,
                    "has_runtime_override": runtime_override_dict is not None,
                    "has_acceptance_override": body.acceptance_override is not None,
                },
            )
        )
    except ManagedRunBusy as exc:
        raise HTTPException(
            status_code=409,
            detail="尚有奏折正在执行，请等待完成后再下达指令",
        ) from exc

    return ApiResponse(success=True, data=result.memorial.model_dump(mode="json"))


@edicts_router.patch("/{edict_id}/status", response_model=ApiResponse)
def update_edict_status(edict_id: str, body: EdictStatusUpdateRequest, request: Request):
    storage: Storage = request.app.state.storage
    edict = require_owned_edict(request, edict_id)
    storage.update_edict_status(edict_id, body.status.value)
    if body.status.value == "cancelled":
        for memorial in storage.list_memorials_by_edict(edict_id):
            if memorial.dag_node_id is None and memorial.status not in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                request.app.state.fenced_run_completion.cancel_root(
                    memorial.id,
                    reason="edict status cancellation",
                )
        storage.update_edict_lifecycle_phase(edict_id, "complete")
    storage.append_event(edict_id, None, "edict.closed", {"status": body.status.value})
    edict.status = body.status
    return ApiResponse(success=True, data=edict.model_dump(mode="json"))


@edicts_router.get("/{edict_id}/events")
def get_events(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    require_owned_edict(request, edict_id)
    events = storage.get_events(edict_id)
    return ApiResponse(success=True, data=events)


@edicts_router.get("/{edict_id}/iterations")
def get_outer_loop_iterations(edict_id: str, request: Request):
    """长任务 outer loop 的迭代记录（仅 acceptance != None 的 edict 有数据）。"""
    storage: Storage = request.app.state.storage
    require_owned_edict(request, edict_id)
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
    context = get_auth_context(request)
    storage: Storage = request.app.state.storage
    items = [
        item
        for item in am.list_pending_outer_loop()
        if (
            (edict := storage.get_edict(str(item["edict_id"]))) is not None
            and can_access_submitter(context, edict.submitter)
        )
    ]
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

    context = get_auth_context(request)
    require_owned_edict(request, edict_id, context=context)
    am = request.app.state.approval_manager
    new_acceptance = (
        AcceptanceCriteria.model_validate(body.new_acceptance) if body.new_acceptance else None
    )
    decision = HumanDecision(
        action=body.action,
        feedback=body.feedback,
        new_acceptance=new_acceptance,
    )
    try:
        triggered = am.submit_outer_loop_decision(
            edict_id,
            decision,
            auth=context,
        )
    except DecisionServiceError as exc:
        raise HTTPException(409, "Outer-loop decision is no longer active") from exc
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
    require_owned_edict(request, edict_id)
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
    require_owned_edict(request, edict_id)
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
    require_owned_edict(request, edict_id)
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
