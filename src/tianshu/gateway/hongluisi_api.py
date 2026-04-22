"""鸿胪寺相关 API — 权威的运行时状态查询。

当前提供：engine provider key 的真实绑定快照（启动期捕获）。
"""

from __future__ import annotations

from fastapi import APIRouter

from tianshu.tools.hongluisi.engine_registry import (
    get_provider_sources,
    get_registered_fetch_engines,
    get_registered_search_providers,
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
