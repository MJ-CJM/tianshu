"""模型供应商注册表路由（model-providers + model-catalog）。

面向 web 的四步配置流：选 profile → 录 key（加密入库 / $ENV 引用）→
从目录选模型 → 连通性测试。无统一 prefix，路径写全。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tianshu.models import ApiResponse

logger = logging.getLogger(__name__)

model_providers_router = APIRouter(tags=["model-providers"])


def _registry(request: Request):
    registry = getattr(request.app.state, "model_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="model registry not wired")
    return registry


def _require_writable(request: Request) -> None:
    """demo 档位下 provider 配置只读：runtime 掩蔽生效时写面一律 409。"""
    cm = getattr(request.app.state, "config_manager", None)
    if cm is not None and cm.runtime_locked:
        raise HTTPException(
            status_code=409,
            detail="provider config is read-only under demo profile",
        )


def _refresh_config_keys(request: Request) -> None:
    cm = getattr(request.app.state, "config_manager", None)
    if cm is not None:
        cm.refresh_resolved_keys()


# --- profiles（内置声明清单，UI 下拉数据源）---


@model_providers_router.get("/model-providers/profiles", response_model=ApiResponse)
def list_profiles(request: Request):
    registry = _registry(request)
    data = [
        {
            "id": p.id,
            "display_name": p.display_name,
            "api_protocol": p.api_protocol,
            "default_base_url": p.default_base_url,
            "key_env": p.key_env,
            "billing": p.billing,
            "has_catalog": bool(p.models_dev_id),
            "notes": p.notes,
        }
        for p in registry.list_profiles()
    ]
    return ApiResponse(success=True, data=data)


# --- provider 实例 CRUD ---


class ModelProviderCreateRequest(BaseModel):
    profile_id: str
    id: str = ""
    display_name: str = ""
    base_url: str = ""
    api_key: str = ""  # 字面量（加密入库）或 "$ENV:VAR_NAME" 引用；空 = 落 profile env


class ModelProviderUpdateRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    enabled: bool | None = None


class ModelProviderKeyRequest(BaseModel):
    api_key: str = Field(..., description="字面量或 $ENV:VAR_NAME；空串清除")


@model_providers_router.get("/model-providers", response_model=ApiResponse)
def list_model_providers(request: Request):
    registry = _registry(request)
    return ApiResponse(success=True, data=registry.list_providers())


@model_providers_router.post("/model-providers", response_model=ApiResponse, status_code=201)
def create_model_provider(body: ModelProviderCreateRequest, request: Request):
    _require_writable(request)
    registry = _registry(request)
    try:
        view = registry.create_provider(
            profile_id=body.profile_id,
            provider_id=body.id,
            display_name=body.display_name,
            base_url=body.base_url,
            api_key=body.api_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _refresh_config_keys(request)
    return ApiResponse(success=True, data=view)


@model_providers_router.put("/model-providers/{provider_id}", response_model=ApiResponse)
def update_model_provider(provider_id: str, body: ModelProviderUpdateRequest, request: Request):
    _require_writable(request)
    registry = _registry(request)
    try:
        view = registry.update_provider(
            provider_id,
            display_name=body.display_name,
            base_url=body.base_url,
            enabled=body.enabled,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"provider '{provider_id}' not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=view)


@model_providers_router.put("/model-providers/{provider_id}/key", response_model=ApiResponse)
def set_model_provider_key(provider_id: str, body: ModelProviderKeyRequest, request: Request):
    _require_writable(request)
    registry = _registry(request)
    try:
        view = registry.set_key(provider_id, body.api_key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"provider '{provider_id}' not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _refresh_config_keys(request)
    logger.info(
        "[model-providers] key updated for '%s' (source=%s)", provider_id, view["key_source"]
    )
    return ApiResponse(success=True, data=view)


@model_providers_router.delete("/model-providers/{provider_id}", response_model=ApiResponse)
def delete_model_provider(provider_id: str, request: Request):
    _require_writable(request)
    registry = _registry(request)
    try:
        registry.delete_provider(provider_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"provider '{provider_id}' not found") from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return ApiResponse(success=True, data={"id": provider_id})


# --- 模型目录 ---


@model_providers_router.get("/model-providers/{provider_id}/models", response_model=ApiResponse)
def list_provider_models(provider_id: str, request: Request, q: str = ""):
    registry = _registry(request)
    if registry.get_provider_row(provider_id) is None:
        raise HTTPException(status_code=404, detail=f"provider '{provider_id}' not found")
    needle = q.strip().lower()
    models = []
    for m in registry.models_for(provider_id):
        if needle and needle not in m.id.lower() and needle not in m.name.lower():
            continue
        pricing = registry.pricing_cny(provider_id, m.id)
        models.append(
            {
                "id": m.id,
                "name": m.name,
                "context_window": m.context_window,
                "max_output_tokens": m.max_output_tokens,
                "tool_call": m.tool_call,
                "reasoning": m.reasoning,
                "vision": m.vision,
                "pricing_cny_per_1k": (
                    {"miss": pricing[0], "hit": pricing[1], "out": pricing[2]}
                    if pricing is not None
                    else None
                ),
                "release_date": m.release_date,
            }
        )
    return ApiResponse(success=True, data=models)


class ConnectivityTestRequest(BaseModel):
    model: str


@model_providers_router.post("/model-providers/{provider_id}/test", response_model=ApiResponse)
async def test_model_provider(provider_id: str, body: ConnectivityTestRequest, request: Request):
    registry = _registry(request)
    if not body.model.strip():
        raise HTTPException(status_code=400, detail="model is required")
    try:
        result = await registry.test_connectivity(provider_id, body.model.strip())
    except KeyError:
        raise HTTPException(status_code=404, detail=f"provider '{provider_id}' not found") from None
    return ApiResponse(success=True, data=result)


# --- catalog 状态与刷新 ---


@model_providers_router.get("/model-catalog/status", response_model=ApiResponse)
def catalog_status(request: Request):
    registry = _registry(request)
    return ApiResponse(success=True, data=registry.catalog().status())


@model_providers_router.post("/model-catalog/refresh", response_model=ApiResponse)
def catalog_refresh(request: Request):
    _require_writable(request)
    registry = _registry(request)
    try:
        status = registry.catalog().refresh()
    except Exception as e:  # noqa: BLE001 - 网络失败以 502 呈现，保留旧快照
        raise HTTPException(status_code=502, detail=f"models.dev refresh failed: {e}") from e
    return ApiResponse(success=True, data=status)
