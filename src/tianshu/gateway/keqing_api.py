"""客卿与影子快照路由(迭代 3.5「客卿」)。

- 客卿:列出可用外部执行器(供边建敕选择);
- 影子快照:列出某 edict 的快照 + 一键 revert(放手四保险③)。

revert 是危险动作(覆盖工作区文件),但影子仓独立于用户 .git,且回滚本身
留一个新快照节点(可再向前),故属可逆操作;不设批红门,但留事件账本。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from tianshu.executor.keqing import list_adapters
from tianshu.models import ApiResponse

logger = logging.getLogger(__name__)

keqing_router = APIRouter(tags=["keqing"])


@keqing_router.get("/keqing/agents")
def get_keqing_agents():
    """可用客卿 backend 列表(前端执行器下拉:native + keqing:<agent>)。"""
    agents = ["native"] + [f"keqing:{name}" for name in list_adapters()]
    return ApiResponse(success=True, data=agents)


@keqing_router.get("/edicts/{edict_id}/snapshots")
def list_snapshots(request: Request, edict_id: str):
    storage = request.app.state.storage
    return ApiResponse(success=True, data=storage.list_shadow_snapshots(edict_id))


class RevertRequest(BaseModel):
    sha: str


@keqing_router.post("/edicts/{edict_id}/snapshots/revert")
async def revert_snapshot(request: Request, edict_id: str, body: RevertRequest):
    from tianshu.executor.shadow_snapshot import ShadowSnapshot

    storage = request.app.state.storage
    work_tree = storage.get_shadow_work_tree(edict_id)
    if not work_tree:
        return ApiResponse(success=False, data=None, error="no shadow snapshots for this edict")

    from pathlib import Path

    shadow = ShadowSnapshot(Path(work_tree), edict_id)
    ok = shadow.revert(body.sha)
    if not ok:
        return ApiResponse(success=False, data=None, error="revert failed (see logs)")

    bus = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        from tianshu.models.events import make_event

        await bus.emit(
            make_event(
                "shadow.reverted",
                edict_id=edict_id,
                producer="keqing",
                payload={"sha": body.sha},
            )
        )
    return ApiResponse(success=True, data={"reverted_to": body.sha})
