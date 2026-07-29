"""Provider 与插件路由（providers + plugins）：CRUD、状态、三维定价、插件安装与状态。无统一 prefix，路径写全。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tianshu.models import ApiResponse
from tianshu.storage import Storage

providers_router = APIRouter(tags=["providers"])


# --- Provider endpoints ---


@providers_router.get("/providers")
def list_providers(request: Request):
    storage: Storage = request.app.state.storage
    providers = storage.list_providers()
    # 附带生效价与来源/计费方式：三维自定义列全 NULL 时前端不再显示 "-/-/-"，
    # 而是目录价（或订阅归零）+ 来源标记。
    pm = getattr(request.app.state, "provider_manager", None)
    if pm is not None:
        providers = [
            {**row, "pricing_effective": pm.get_pricing_with_source(row["name"])}
            for row in providers
        ]
    return ApiResponse(success=True, data=providers)


@providers_router.post("/providers", response_model=ApiResponse, status_code=201)
async def create_provider(request: Request):
    storage: Storage = request.app.state.storage
    body = await request.json()
    if not body.get("name") or not body.get("model"):
        raise HTTPException(status_code=400, detail="name and model are required")
    from datetime import UTC, datetime

    body.setdefault("created_at", datetime.now(UTC).isoformat())
    storage.save_provider(body)
    return ApiResponse(success=True, data=body)


@providers_router.put("/providers/{name}", response_model=ApiResponse)
async def update_provider(name: str, request: Request):
    storage: Storage = request.app.state.storage
    existing = storage.get_provider(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    body = await request.json()
    storage.update_provider(name, body)
    updated = storage.get_provider(name)
    return ApiResponse(success=True, data=updated)


@providers_router.delete("/providers/{name}", response_model=ApiResponse)
def delete_provider(name: str, request: Request):
    storage: Storage = request.app.state.storage
    deleted = storage.delete_provider(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return ApiResponse(success=True, data={"name": name})


@providers_router.get("/providers/{name}/status")
def get_provider_status(name: str, request: Request):
    storage: Storage = request.app.state.storage
    provider = storage.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return ApiResponse(success=True, data=provider)


# --- Provider pricing endpoints (3 维：input miss / input hit / output) ---


class ProviderPricingUpdateRequest(BaseModel):
    cost_per_1k_prompt: float | None = Field(None, ge=0)
    cost_per_1k_cache_read: float | None = Field(None, ge=0)
    cost_per_1k_completion: float | None = Field(None, ge=0)


@providers_router.put("/providers/{name}/pricing", response_model=ApiResponse)
def update_provider_pricing(
    name: str,
    body: ProviderPricingUpdateRequest,
    request: Request,
):
    """部分更新 provider 三维价格。body 里的 None 字段保持不变（不清零）。"""
    storage: Storage = request.app.state.storage
    provider = storage.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        storage.update_provider(name, updates)
    pm = request.app.state.provider_manager
    return ApiResponse(success=True, data=pm.get_pricing_with_source(name))


@providers_router.delete("/providers/{name}/pricing", response_model=ApiResponse)
def reset_provider_pricing(name: str, request: Request):
    """重置 provider 三维价格为 NULL（落 models.dev 目录默认价）。"""
    storage: Storage = request.app.state.storage
    provider = storage.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    storage.update_provider(
        name,
        {
            "cost_per_1k_prompt": None,
            "cost_per_1k_cache_read": None,
            "cost_per_1k_completion": None,
        },
    )
    pm = request.app.state.provider_manager
    return ApiResponse(success=True, data=pm.get_pricing_with_source(name))


@providers_router.get("/providers/pricing/defaults", response_model=ApiResponse)
def get_default_pricing_table(request: Request):
    """默认价来源已切换为 models.dev 目录快照；返回快照状态 + 兜底价。"""
    from tianshu.cost.tracker import _FALLBACK_PRICING
    from tianshu.providers.model_catalog import default_catalog

    catalog = getattr(request.app.state, "model_catalog", None) or default_catalog()
    return ApiResponse(
        success=True,
        data={
            **catalog.status(),
            "fallback": {
                "miss": _FALLBACK_PRICING[0],
                "hit": _FALLBACK_PRICING[1],
                "out": _FALLBACK_PRICING[2],
            },
        },
    )


@providers_router.get("/providers/{name}/pricing/effective", response_model=ApiResponse)
def get_effective_pricing(name: str, request: Request):
    """返回当前生效价 + 来源（custom / default / mixed）。"""
    storage: Storage = request.app.state.storage
    if not storage.get_provider(name):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    pm = request.app.state.provider_manager
    return ApiResponse(success=True, data=pm.get_pricing_with_source(name))


# --- Plugin endpoints ---


@providers_router.get("/plugins")
def list_plugins(request: Request):
    storage: Storage = request.app.state.storage
    plugins = storage.list_plugins()
    return ApiResponse(success=True, data=plugins)


@providers_router.get("/plugins/{name}")
def get_plugin(name: str, request: Request):
    storage: Storage = request.app.state.storage
    plugin = storage.get_plugin(name)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    return ApiResponse(success=True, data=plugin)


@providers_router.post("/plugins/install", response_model=ApiResponse, status_code=201)
async def install_plugin(request: Request):
    storage: Storage = request.app.state.storage
    body = await request.json()
    if not body.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    storage.save_plugin(body)
    return ApiResponse(success=True, data=body)


@providers_router.put("/plugins/{name}/status", response_model=ApiResponse)
async def update_plugin_status(name: str, request: Request):
    storage: Storage = request.app.state.storage
    plugin = storage.get_plugin(name)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    body = await request.json()
    storage.update_plugin_status(name, body.get("status", "active"))
    return ApiResponse(success=True, data={"name": name, "status": body.get("status", "active")})
