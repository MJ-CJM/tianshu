"""分级急停路由(锦衣卫,迭代 3「深防御」)。

放手四保险的"急刹车":engage 收紧 / resume 放开 / status 查看。engage/resume
经 EstopManager 落库留痕。批红级危险动作:自身不设审批门(急停就是要立即
生效),但全部操作留事件账本(producer="estop")。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from tianshu.models import ApiResponse

logger = logging.getLogger(__name__)

estop_router = APIRouter(tags=["estop"])


class EngageRequest(BaseModel):
    kill_all: bool | None = None
    network_kill: bool | None = None
    freeze_tools: list[str] | None = None
    reason: str | None = None


class ResumeRequest(BaseModel):
    kill_all: bool = False
    network_kill: bool = False
    unfreeze_tools: list[str] | None = None
    all_clear: bool = False


def _manager(request: Request):
    return getattr(request.app.state, "estop_manager", None)


async def _emit(request: Request, action: str, state: dict) -> None:
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        return
    from tianshu.models.events import make_event

    await bus.emit(
        make_event(
            f"estop.{action}",
            edict_id=None,
            producer="estop",
            payload=state,
        )
    )


@estop_router.get("/estop")
def get_estop(request: Request):
    mgr = _manager(request)
    if mgr is None:
        return ApiResponse(success=True, data={"engaged": False, "available": False})
    return ApiResponse(success=True, data={**mgr.status().to_dict(), "available": True})


@estop_router.post("/estop/engage")
async def engage_estop(request: Request, body: EngageRequest):
    mgr = _manager(request)
    if mgr is None:
        return ApiResponse(success=False, data=None, error="estop manager unavailable")
    state = mgr.engage(
        kill_all=body.kill_all,
        network_kill=body.network_kill,
        freeze_tools=body.freeze_tools,
        reason=body.reason,
    )
    await _emit(request, "engaged", state.to_dict())
    return ApiResponse(success=True, data=state.to_dict())


@estop_router.post("/estop/resume")
async def resume_estop(request: Request, body: ResumeRequest):
    mgr = _manager(request)
    if mgr is None:
        return ApiResponse(success=False, data=None, error="estop manager unavailable")
    state = mgr.resume(
        kill_all=body.kill_all,
        network_kill=body.network_kill,
        unfreeze_tools=body.unfreeze_tools,
        all_clear=body.all_clear,
    )
    await _emit(request, "resumed", state.to_dict())
    return ApiResponse(success=True, data=state.to_dict())
