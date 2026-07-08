"""平台级回归评测与失败归因路由(迭代 2「证明」,只读)。

跑批是离线活(起沙箱逐条回放、花 LLM 钱),入口只在 CLI `tianshu evals run`;
这里只暴露台账读取与失败归因分布,喂 web 报告面板与审计面板。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.evals import aggregate_failure_distribution
from tianshu.models import ApiResponse
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

evals_router = APIRouter(tags=["evals"])


@evals_router.get("/evals/runs")
def list_eval_runs(request: Request, limit: int = Query(50, ge=1, le=200)):
    storage: Storage = request.app.state.storage
    return ApiResponse(success=True, data=storage.list_platform_eval_runs(limit=limit))


@evals_router.get("/evals/runs/{run_id}")
def get_eval_run(request: Request, run_id: str):
    storage: Storage = request.app.state.storage
    run = storage.get_platform_eval_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"eval run not found: {run_id}")
    run["failure_distribution"] = aggregate_failure_distribution(run.get("goal_results", []))
    return ApiResponse(success=True, data=run)


@evals_router.get("/evals/sets")
def list_eval_sets(request: Request):
    storage: Storage = request.app.state.storage
    return ApiResponse(success=True, data=storage.list_eval_sets())


@evals_router.get("/evals/failure-distribution")
def get_failure_distribution(request: Request, days: int | None = Query(None, ge=1, le=365)):
    """主库 failed memorial 的归因分布(审计面板/太医诊断消费)。"""
    storage: Storage = request.app.state.storage
    return ApiResponse(success=True, data=storage.failure_reason_distribution(days=days))
