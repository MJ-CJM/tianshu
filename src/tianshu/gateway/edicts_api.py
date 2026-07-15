"""/api/edicts 路由：敕令 CRUD、生命周期（pause/resume）、计划审批、follow-up、outer-loop 决策、监督报告。"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from tianshu.application.edicts import (
    EdictApplicationService,
    IdempotencyConflict,
    SubmitEdictCommand,
    validate_idempotency_key,
)
from tianshu.executor.capabilities import (
    MandatoryCapabilityMismatch,
    get_executor_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.executor import Executor
from tianshu.executor.workspace_policy import workspace_policy_mismatches
from tianshu.executor.workspace_runtime import WORKSPACE_MAIN_SOURCE_ID
from tianshu.gateway._helpers import _build_history
from tianshu.gateway.auth import get_auth_context
from tianshu.governance.decision_service import DecisionServiceError
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
)
from tianshu.models.api import ParseEdictRequest
from tianshu.models.edict import EdictRuntime, PolicyProfilePayload, title_from_goal
from tianshu.models.governance_contract import (
    AcceptancePolicyV1,
    LegacyEdictGovernanceMapper,
    RequestedGovernanceContractV1,
    acceptance_policy_to_legacy,
)
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

    service = getattr(request.app.state, "edict_application_service", None)
    if service is None:
        service = EdictApplicationService(storage)
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


@edicts_router.post("/governance/preview", response_model=ApiResponse)
def preview_governance_contract(body: EdictCreateRequest, request: Request):
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


class SteerRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


@edicts_router.post("/{edict_id}/steer", response_model=ApiResponse, status_code=202)
async def steer_edict(edict_id: str, body: SteerRequest, request: Request):
    """向运行中的长任务中途注入一条 steer 指示——下一轮 actor 边界吸收(迭代 5)。"""
    from datetime import UTC, datetime

    from ulid import ULID

    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(status_code=404, detail=f"Edict '{edict_id}' not found")
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
def approve_plan(edict_id: str, request: Request):
    """Resolve a pending durable plan review; projection triggers execution later."""
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(404, "Edict not found")
    try:
        resolution = request.app.state.approval_manager.submit_plan_review_decision(
            edict_id,
            action="approve",
            auth=get_auth_context(request),
        )
    except ValueError as exc:
        raise HTTPException(400, "No pending plan to approve") from exc
    except DecisionServiceError as exc:
        raise HTTPException(409, "Plan review is no longer active") from exc
    return ApiResponse(
        success=True,
        data={
            "status": "approved",
            "decision_request_id": resolution.decision_request_id,
        },
    )


@edicts_router.post("/{edict_id}/plan/reject", response_model=ApiResponse)
def reject_plan(edict_id: str, request: Request):
    """Resolve a pending durable plan review as rejected."""
    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if not edict:
        raise HTTPException(404, "Edict not found")
    try:
        resolution = request.app.state.approval_manager.submit_plan_review_decision(
            edict_id,
            action="reject",
            auth=get_auth_context(request),
        )
    except ValueError as exc:
        raise HTTPException(400, "No pending plan to reject") from exc
    except DecisionServiceError as exc:
        raise HTTPException(409, "Plan review is no longer active") from exc
    return ApiResponse(
        success=True,
        data={
            "status": "rejected",
            "decision_request_id": resolution.decision_request_id,
        },
    )


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
        rt_data = {
            k: v
            for k, v in body.runtime_override.model_dump(exclude_unset=True).items()
            if v is not None
        }
        runtime_override_dict = rt_data or None

    memorial = Memorial(
        edict_id=edict_id,
        instruction=body.instruction,
        status=TaskStatus.SUBMITTED,
        parent_memorial_id=next(
            (item.id for item in reversed(prev_memorials) if item.dag_node_id is None),
            None,
        ),
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
    try:
        triggered = am.submit_outer_loop_decision(
            edict_id,
            decision,
            auth=get_auth_context(request),
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
