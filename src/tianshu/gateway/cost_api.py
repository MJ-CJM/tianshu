"""/api/cost 路由：花费汇总、明细、预算、导出。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from tianshu.cost.manager import CostManager
from tianshu.cost.models import CostBudgetUpdate
from tianshu.models import ApiResponse

cost_router = APIRouter(prefix="/cost", tags=["cost"])


# --- Cost endpoints ---


@cost_router.get("/summary")
def get_cost_summary(
    request: Request,
    period: str | None = None,
    edict_id: str | None = None,
):
    cm: CostManager = request.app.state.cost_manager
    summary = cm.get_summary(period=period, edict_id=edict_id)
    return ApiResponse(success=True, data=summary.model_dump())


@cost_router.get("/records")
def get_cost_records(
    request: Request,
    edict_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    cm: CostManager = request.app.state.cost_manager
    records, total = cm.get_records(edict_id=edict_id, limit=limit, offset=offset)
    return ApiResponse(
        success=True,
        data=records,
        metadata={"total": total, "limit": limit, "offset": offset},
    )


@cost_router.get("/budget")
def get_cost_budget(request: Request, scope: str = "global"):
    cm: CostManager = request.app.state.cost_manager
    status = cm.get_budget(scope)
    if not status:
        return ApiResponse(success=True, data=None)
    return ApiResponse(success=True, data=status.model_dump())


@cost_router.put("/budget", response_model=ApiResponse)
async def set_cost_budget(body: CostBudgetUpdate, request: Request):
    cm: CostManager = request.app.state.cost_manager
    cm.set_budget(
        scope=body.scope,
        budget_cny=body.budget_cny,
        period=body.period,
        reset_at=body.reset_at.isoformat() if body.reset_at is not None else None,
    )
    status = cm.get_budget(body.scope)
    return ApiResponse(
        success=True,
        data=status.model_dump(mode="json") if status is not None else None,
    )


@cost_router.get("/export")
def export_cost_records(
    request: Request,
    period: str | None = None,
    edict_id: str | None = None,
):
    cm: CostManager = request.app.state.cost_manager
    records, _ = cm.get_records(edict_id=edict_id, limit=10000)
    summary = cm.get_summary(period=period, edict_id=edict_id)
    return ApiResponse(
        success=True,
        data={"summary": summary.model_dump(), "records": records},
    )
