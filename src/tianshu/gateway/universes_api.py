"""/api/universes 路由：平行位面 CRUD、代码变体提案与晋升、评估记录。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.models import ApiResponse
from tianshu.storage import Storage

universes_router = APIRouter(prefix="/universes", tags=["universes"])


# --- Universe (平行位面) endpoints ---


@universes_router.get("")
async def list_universes(request: Request):
    mgr = request.app.state.universe_manager
    return ApiResponse(success=True, data=mgr.list())


@universes_router.get("/_diff")
async def diff_universes(request: Request, a: str = Query(...), b: str = Query(...)):
    mgr = request.app.state.universe_manager
    return ApiResponse(success=True, data=mgr.diff(a, b))


@universes_router.get("/_status")
async def universe_status(request: Request):
    config_manager = request.app.state.config_manager
    enabled = config_manager.agent_config.parallel_universe_enabled
    return ApiResponse(success=True, data={"enabled": enabled})


@universes_router.post("/enable", response_model=ApiResponse)
async def enable_parallel_universe(request: Request):
    config_manager = request.app.state.config_manager
    mgr = request.app.state.universe_manager
    config_manager.update_agent_config(parallel_universe_enabled=True)
    genesis = mgr.ensure_genesis()
    return ApiResponse(success=True, data=genesis)


@universes_router.post("/feedback", response_model=ApiResponse)
async def universe_feedback(request: Request):
    storage: Storage = request.app.state.storage
    body = await request.json()
    memorial_id = body.get("memorial_id")
    score = int(body.get("score", 0))
    if not memorial_id:
        raise HTTPException(status_code=400, detail="memorial_id required")
    storage.set_memorial_feedback(memorial_id, score)
    mem = storage.get_memorial(memorial_id)
    uid = getattr(mem, "universe_id", None) if mem else None
    if uid:
        from tianshu.universe.fitness import compute_fitness

        cm = request.app.state.config_manager
        stats = storage.universe_memorial_stats(uid)
        storage.update_universe_fitness(
            uid,
            compute_fitness(stats, weights=cm.agent_config.universe_fitness_weights),
        )
    return ApiResponse(success=True, data={"universe_id": uid, "score": max(-1, min(1, score))})


@universes_router.post("/evolve", response_model=ApiResponse)
async def trigger_evolve(request: Request):
    evolver = request.app.state.universe_evolver
    result = await evolver.run(trigger_source="manual")
    return ApiResponse(success=True, data=result.to_dict())


@universes_router.post("/propose-code", response_model=ApiResponse)
async def propose_code_variant(request: Request):
    evolver = request.app.state.universe_evolver
    body = await request.json()
    target_path = (body or {}).get("target_path")
    hypothesis = (body or {}).get("hypothesis")
    if not target_path or not hypothesis:
        raise HTTPException(status_code=400, detail="target_path and hypothesis required")
    result = await evolver.propose_code_variant(
        target_path=target_path,
        hypothesis=hypothesis,
        parent_id=(body or {}).get("parent_id"),
    )
    return ApiResponse(success=True, data=result)


@universes_router.delete("/{universe_id}", response_model=ApiResponse)
async def delete_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    try:
        result = mgr.delete(universe_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=result)


@universes_router.get("/{universe_id}")
async def get_universe(universe_id: str, request: Request):
    storage: Storage = request.app.state.storage
    uni = storage.get_universe(universe_id)
    if not uni:
        raise HTTPException(status_code=404, detail="universe not found")
    return ApiResponse(success=True, data=uni)


@universes_router.post("/{universe_id}/branch", response_model=ApiResponse)
async def branch_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    body = await request.json()
    name = (body or {}).get("name") or "新位面"
    try:
        uni = mgr.branch(universe_id, name, description=(body or {}).get("description", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=uni)


@universes_router.post("/{universe_id}/switch", response_model=ApiResponse)
async def switch_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    try:
        uni = mgr.switch(universe_id)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=uni)


@universes_router.post("/{universe_id}/archive", response_model=ApiResponse)
async def archive_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    try:
        uni = mgr.archive(universe_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=uni)


@universes_router.post("/{universe_id}/restore", response_model=ApiResponse)
async def restore_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    try:
        uni = mgr.restore(universe_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=uni)


@universes_router.post("/{universe_id}/promote-code", response_model=ApiResponse)
async def promote_code_variant(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    try:
        uni = mgr.promote_code_variant(universe_id)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data=uni)


@universes_router.get("/{universe_id}/code-diff", response_model=ApiResponse)
async def code_diff_universe(universe_id: str, request: Request):
    mgr = request.app.state.universe_manager
    try:
        diff = mgr.code_diff(universe_id)
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data={"diff": diff})


@universes_router.get("/{universe_id}/eval-runs", response_model=ApiResponse)
async def list_eval_runs(universe_id: str, request: Request):
    storage: Storage = request.app.state.storage
    return ApiResponse(success=True, data=storage.list_variant_eval_runs(universe_id))
