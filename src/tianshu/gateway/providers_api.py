"""Provider 与插件路由（providers + plugins）：CRUD、状态、三维定价、插件安装与状态。无统一 prefix，路径写全。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tianshu.models import ApiResponse
from tianshu.storage import Storage

providers_router = APIRouter(tags=["providers"])


# --- Provider endpoints ---


@providers_router.get("/providers")
async def list_providers(request: Request):
    storage: Storage = request.app.state.storage
    providers = storage.list_providers()
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
async def delete_provider(name: str, request: Request):
    storage: Storage = request.app.state.storage
    deleted = storage.delete_provider(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return ApiResponse(success=True, data={"name": name})


@providers_router.get("/providers/{name}/status")
async def get_provider_status(name: str, request: Request):
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
async def update_provider_pricing(
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
async def reset_provider_pricing(name: str, request: Request):
    """重置 provider 三维价格为 NULL（落 _DEFAULT_PRICING）。"""
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
async def get_default_pricing_table(request: Request):
    """返回 _DEFAULT_PRICING 全部条目（用于户部账房"查看默认价表"展示）。"""
    from tianshu.cost.tracker import _DEFAULT_PRICING, _FALLBACK_PRICING

    rows = [
        {"model": model, "miss": p[0], "hit": p[1], "out": p[2]}
        for model, p in _DEFAULT_PRICING.items()
    ]
    return ApiResponse(
        success=True,
        data={
            "entries": rows,
            "fallback": {
                "miss": _FALLBACK_PRICING[0],
                "hit": _FALLBACK_PRICING[1],
                "out": _FALLBACK_PRICING[2],
            },
        },
    )


@providers_router.get("/providers/{name}/pricing/effective", response_model=ApiResponse)
async def get_effective_pricing(name: str, request: Request):
    """返回当前生效价 + 来源（custom / default / mixed）。"""
    storage: Storage = request.app.state.storage
    if not storage.get_provider(name):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    pm = request.app.state.provider_manager
    return ApiResponse(success=True, data=pm.get_pricing_with_source(name))


# --- Plugin endpoints ---


@providers_router.get("/plugins")
async def list_plugins(request: Request):
    storage: Storage = request.app.state.storage
    plugins = storage.list_plugins()
    return ApiResponse(success=True, data=plugins)


@providers_router.get("/plugins/{name}")
async def get_plugin(name: str, request: Request):
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
