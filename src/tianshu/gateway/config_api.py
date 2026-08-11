"""配置相关路由（agent-config + config + configs）：Agent 运行参数、单/多 LLM 配置 CRUD 与激活。无统一 prefix，路径写全。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from tianshu.bootstrap.wiring_storage import WORKSPACE_DIR_SETTING_KEY
from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.models import (
    AgentConfig,
    AgentConfigUpdateRequest,
    ApiResponse,
    LLMConfig,
    LLMConfigCreateRequest,
    LLMConfigListResponse,
    LLMConfigUpdateRequest,
)
from tianshu.providers.manager import ProviderManager
from tianshu.tools.path_utils import is_sensitive_path

logger = logging.getLogger(__name__)

config_router = APIRouter(tags=["config"])


# --- Workspace 全局边界 ---


def _validate_workspace_dir(raw: str) -> Path:
    """校验用户提交的工作区根，返回展开后的绝对路径。

    这是所有官员的默认活动边界，配错等于取消隔离，故校验从严：
    必须是已存在的目录、不能是文件系统根、不能落在凭证目录里。
    """
    value = raw.strip()
    if not value:
        raise HTTPException(status_code=400, detail="工作区路径不能为空")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="必须是绝对路径（可用 ~ 表示用户目录）")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise HTTPException(status_code=400, detail="不能设为文件系统根目录，等同于取消工作区隔离")
    if is_sensitive_path(resolved):
        raise HTTPException(status_code=400, detail="不能设为凭证目录（.ssh/.aws 等）")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"目录不存在：{resolved}")
    return resolved


@config_router.get("/workspace", response_model=ApiResponse)
def get_workspace_dir(request: Request):
    """当前生效的工作区根，以及待重启才生效的已保存值。"""
    storage = request.app.state.storage
    settings = request.app.state.settings
    saved = storage.get_app_setting(WORKSPACE_DIR_SETTING_KEY)
    effective = str(Path(settings.workspace_dir).expanduser().resolve())
    return ApiResponse(
        success=True,
        data={
            "workspace_dir": saved if isinstance(saved, str) else None,
            "effective": effective,
            "pending_restart": bool(
                isinstance(saved, str)
                and saved.strip()
                and str(Path(saved).expanduser().resolve()) != effective
            ),
        },
    )


@config_router.put("/workspace", response_model=ApiResponse)
def update_workspace_dir(body: dict, request: Request):
    """保存工作区根。重启后生效——工具注册时已闭包捕获旧路径。"""
    resolved = _validate_workspace_dir(str(body.get("workspace_dir", "")))
    storage = request.app.state.storage
    storage.set_app_setting(WORKSPACE_DIR_SETTING_KEY, str(resolved))
    settings = request.app.state.settings
    effective = str(Path(settings.workspace_dir).expanduser().resolve())
    return ApiResponse(
        success=True,
        data={
            "workspace_dir": str(resolved),
            "effective": effective,
            "pending_restart": str(resolved) != effective,
        },
    )


# --- Config endpoints ---


def _state_to_agent_config(state) -> AgentConfig:
    return AgentConfig(
        agent_max_iterations=state.agent_max_iterations,
        agent_timeout_seconds=state.agent_timeout_seconds,
        agent_max_concurrency=state.agent_max_concurrency,
        agent_retry_limit=state.agent_retry_limit,
        agent_token_budget=state.agent_token_budget,
        agent_cost_budget_cny=state.agent_cost_budget_cny,
        skills_char_budget=state.skills_char_budget,
        # Compatibility field: automatic review cannot safely activate a skill yet.
        skill_review_enabled=False,
        skill_review_interval=state.skill_review_interval,
        fallback_llm_config_name=state.fallback_llm_config_name,
        keqing_default_models=state.keqing_default_models,
        # Credential gateway is not wired into production execution yet.
        # Keep the response field for compatibility, but never claim it is active.
        keqing_gateway_enabled=False,
        keqing_per_run_budget_cny=state.keqing_per_run_budget_cny,
        keqing_model_allowlist=state.keqing_model_allowlist,
        task_slots=state.task_slots,
    )


@config_router.get("/agent-config", response_model=ApiResponse)
def get_agent_config(request: Request):
    cm: ConfigManager = request.app.state.config_manager
    return ApiResponse(success=True, data=_state_to_agent_config(cm.agent_config).model_dump())


@config_router.put("/agent-config", response_model=ApiResponse)
def update_agent_config(body: AgentConfigUpdateRequest, request: Request):
    cm: ConfigManager = request.app.state.config_manager
    if body.skill_review_enabled is True:
        raise HTTPException(
            status_code=409,
            detail="automatic skill review is unavailable until governed skill activation is wired",
        )
    if body.keqing_gateway_enabled is True:
        raise HTTPException(
            status_code=409,
            detail="Keqing credential gateway is experimental and unavailable in this release",
        )
    updates = body.model_dump(exclude_none=True)
    if not updates:
        state = cm.agent_config
    else:
        state = cm.update_agent_config(**updates)
        logger.info("Agent config updated: %s", list(updates.keys()))
    return ApiResponse(success=True, data=_state_to_agent_config(state).model_dump())


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
        provider_id=s.provider_id,
    )


# --- Legacy single-config endpoints (operate on active config) ---


def _require_writable_provider_config(request: Request) -> None:
    """demo 档位下 provider 配置只读：runtime 掩蔽生效时写面一律 409。"""
    cm: ConfigManager = request.app.state.config_manager
    if cm.runtime_locked:
        raise HTTPException(
            status_code=409,
            detail="provider config is read-only under demo profile",
        )


@config_router.get("/config", response_model=ApiResponse, deprecated=True)
def get_config(request: Request, response: Response):
    """[deprecated] 单配置读面；请改用 GET /configs（多配置视图）。"""
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</api/configs>; rel="successor-version"'
    cm: ConfigManager = request.app.state.config_manager
    return ApiResponse(success=True, data=_state_to_config(cm.state).model_dump())


@config_router.put("/config", response_model=ApiResponse, deprecated=True)
def update_config(body: LLMConfigUpdateRequest, request: Request, response: Response):
    """[deprecated] 单配置写面（只改内存态的 legacy 怪癖保留）；请改用 PUT /configs/{name}。"""
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</api/configs>; rel="successor-version"'
    _require_writable_provider_config(request)
    cm: ConfigManager = request.app.state.config_manager
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return ApiResponse(success=True, data=_state_to_config(cm.state).model_dump())
    cm.update(**updates)
    logger.info("LLM config updated: %s", list(updates.keys()))
    return ApiResponse(success=True, data=_state_to_config(cm.state).model_dump())


# --- Multi-config endpoints ---


@config_router.get("/configs", response_model=ApiResponse)
def list_configs(request: Request):
    cm: ConfigManager = request.app.state.config_manager
    configs, active_name = cm.list_configs()
    resp = LLMConfigListResponse(
        configs=[_state_to_config(c) for c in configs],
        active_name=active_name,
    )
    return ApiResponse(success=True, data=resp.model_dump())


@config_router.post("/configs", response_model=ApiResponse, status_code=201)
def create_config(body: LLMConfigCreateRequest, request: Request):
    _require_writable_provider_config(request)
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
        provider_id=body.provider_id,
    )
    try:
        cm.add_config(state)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    pm: ProviderManager = request.app.state.provider_manager
    pm.sync_from_config(state)
    return ApiResponse(success=True, data=_state_to_config(state).model_dump())


@config_router.put("/configs/{name}", response_model=ApiResponse)
def update_named_config(name: str, body: LLMConfigUpdateRequest, request: Request):
    _require_writable_provider_config(request)
    cm: ConfigManager = request.app.state.config_manager
    updates = body.model_dump(exclude_none=True)
    try:
        new_state = cm.update_config(name, **updates) if updates else cm.get_config(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found") from None
    if new_state:
        pm: ProviderManager = request.app.state.provider_manager
        pm.sync_from_config(new_state)
    return ApiResponse(success=True, data=_state_to_config(new_state).model_dump())


@config_router.delete("/configs/{name}", response_model=ApiResponse)
def delete_named_config(name: str, request: Request):
    _require_writable_provider_config(request)
    cm: ConfigManager = request.app.state.config_manager
    try:
        cm.delete_config(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    pm: ProviderManager = request.app.state.provider_manager
    pm.unregister(name)
    return ApiResponse(success=True, data={"name": name})


@config_router.put("/configs/{name}/activate", response_model=ApiResponse)
def activate_config(name: str, request: Request):
    _require_writable_provider_config(request)
    cm: ConfigManager = request.app.state.config_manager
    try:
        cm.set_active(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found") from None
    pm: ProviderManager = request.app.state.provider_manager
    pm.sync_all()
    configs, active_name = cm.list_configs()
    resp = LLMConfigListResponse(
        configs=[_state_to_config(c) for c in configs],
        active_name=active_name,
    )
    return ApiResponse(success=True, data=resp.model_dump())
