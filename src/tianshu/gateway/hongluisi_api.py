"""鸿胪寺相关 API — 权威的运行时状态查询。

当前提供：engine provider key 的真实绑定快照（启动期捕获）
+ 系统级引擎偏好 (fetch_chain / search_provider / fallback_mode)。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tianshu.tools.hongluisi.engine_registry import (
    get_provider_sources,
    get_registered_fetch_engines,
    get_registered_search_providers,
    rebuild_engines,
)
from tianshu.tools.policy_profile import (
    get_system_engine_overrides,
    set_system_engine_overrides,
)

hongluisi_router = APIRouter(prefix="/hongluisi", tags=["hongluisi"])


@hongluisi_router.get("/engine-status")
def engine_status() -> dict:
    """返回启动时真正绑到的 provider key 来源 + 已注册的 fetch/search engines。

    UI 用这份权威数据显示 `Key: DB / env / —`，避免仅凭 DB 有记录就误判。
    新增/修改 provider key 后，需要重启后端这里才会刷新。
    """
    return {
        "providers": get_provider_sources(),  # {"jina": "db"|"env"|"none", ...}
        "fetch_engines": sorted(get_registered_fetch_engines()),
        "search_providers": sorted(get_registered_search_providers()),
    }


class EnginePreferencesPayload(BaseModel):
    fetch_chain: list[str] = Field(default_factory=list)
    search_provider: str | None = None  # "tavily"|"jina"|"duckduckgo"|null
    fallback_mode: str | None = None  # "none"|"on_error_or_empty"|null
    scrapling_dynamic_enabled: bool = False
    scrapling_stealthy_enabled: bool = False


@hongluisi_router.get("/engine-preferences")
def get_engine_preferences(request: Request) -> dict:
    """返回当前系统级引擎覆盖 + 浏览器引擎开关。空字段表示沿用 profile 预设。"""
    overrides = get_system_engine_overrides()
    prefs = request.app.state.storage.get_engine_preferences()
    return {
        **overrides,
        "scrapling_dynamic_enabled": prefs["scrapling_dynamic_enabled"],
        "scrapling_stealthy_enabled": prefs["scrapling_stealthy_enabled"],
    }


@hongluisi_router.patch("/engine-preferences")
def update_engine_preferences(
    body: EnginePreferencesPayload, request: Request
) -> dict:
    """live 更新：写 DB + 刷缓存 + rebuild 引擎。无需重启。"""
    storage = request.app.state.storage
    ALLOWED_FETCH = {
        "local", "jina", "firecrawl",
        "scrapling", "scrapling_dynamic", "scrapling_stealthy",
    }
    ALLOWED_SEARCH = {"tavily", "jina", "duckduckgo", None, ""}
    ALLOWED_FALLBACK = {"none", "on_error_or_empty", None, ""}
    bad_fetch = [e for e in body.fetch_chain if e not in ALLOWED_FETCH]
    if bad_fetch:
        raise HTTPException(400, f"unknown fetch engine(s): {bad_fetch}")
    if body.search_provider not in ALLOWED_SEARCH:
        raise HTTPException(400, f"unknown search provider: {body.search_provider}")
    if body.fallback_mode not in ALLOWED_FALLBACK:
        raise HTTPException(400, f"unknown fallback mode: {body.fallback_mode}")

    storage.set_engine_preferences(
        fetch_chain=body.fetch_chain,
        search_provider=body.search_provider or None,
        fallback_mode=body.fallback_mode or None,
        scrapling_dynamic_enabled=body.scrapling_dynamic_enabled,
        scrapling_stealthy_enabled=body.scrapling_stealthy_enabled,
    )
    set_system_engine_overrides(
        fetch_chain=body.fetch_chain,
        search_provider=body.search_provider or "",
        fallback_mode=body.fallback_mode or "",
    )
    # 浏览器引擎开关影响引擎注册，需 rebuild
    rebuild_engines()

    overrides = get_system_engine_overrides()
    return {
        **overrides,
        "scrapling_dynamic_enabled": body.scrapling_dynamic_enabled,
        "scrapling_stealthy_enabled": body.scrapling_stealthy_enabled,
    }
