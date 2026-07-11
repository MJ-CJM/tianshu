"""执行域路由：memorials / decrees / approvals / scheduler / workers / dag / planner。无统一 prefix，路径写全。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.executor import Executor
from tianshu.executor.lanes import LaneManager
from tianshu.executor.worker_pool import WorkerPool
from tianshu.gateway.auth import get_auth_context
from tianshu.models import ApiResponse, Decree, DecreeCreateRequest, TaskStatus, ToolDecisionRequest
from tianshu.scheduler.scheduler import Scheduler
from tianshu.storage import Storage

execution_router = APIRouter(tags=["execution"])


# --- Memorial endpoints ---


@execution_router.get("/memorials")
def list_memorials(
    request: Request,
    status: TaskStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    storage: Storage = request.app.state.storage
    memorials, total = storage.list_memorials(
        status=status.value if status else None, limit=limit, offset=offset
    )
    return ApiResponse(
        success=True,
        data=[m.model_dump(mode="json") for m in memorials],
        metadata={"total": total, "limit": limit, "offset": offset},
    )


@execution_router.get("/memorials/{memorial_id}")
def get_memorial(memorial_id: str, request: Request):
    storage: Storage = request.app.state.storage
    memorial = storage.get_memorial(memorial_id)
    if not memorial:
        raise HTTPException(status_code=404, detail=f"Memorial '{memorial_id}' not found")
    return ApiResponse(success=True, data=memorial.model_dump(mode="json"))


# --- Scheduler endpoints ---


@execution_router.get("/scheduler/jobs")
async def list_scheduler_jobs(request: Request):
    scheduler: Scheduler = request.app.state.scheduler
    jobs = await scheduler.list_jobs()
    return ApiResponse(success=True, data=jobs)


@execution_router.delete("/scheduler/jobs/{job_id}", response_model=ApiResponse)
async def cancel_scheduler_job(job_id: str, request: Request):
    scheduler: Scheduler = request.app.state.scheduler
    await scheduler.cancel(job_id)
    return ApiResponse(success=True, data={"job_id": job_id})


# --- Decree (approval) endpoints ---


@execution_router.post("/decrees", response_model=ApiResponse, status_code=201)
async def create_decree(body: DecreeCreateRequest, request: Request):
    approval_manager: ApprovalManager = request.app.state.approval_manager
    actor = get_auth_context(request).principal.id

    decree = Decree(
        memorial_id=body.memorial_id,
        action=body.action,
        comment=body.comment,
        amended_goal=body.amended_goal,
        actor=actor,
    )

    try:
        await approval_manager.submit_decree(decree)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return ApiResponse(success=True, data=decree.model_dump(mode="json"))


@execution_router.get("/approvals/pending_tool_calls", response_model=ApiResponse)
async def list_pending_tool_calls(request: Request):
    """Return in-memory pending tool-call approvals awaited by PolicyHook.

    Used by 御书房 to render mid-execution approval cards. The state is sourced
    from `ApprovalManager._pending` (authoritative) and enriched with the latest
    `tool.approval_required` event payload for each memorial.
    """
    approval_manager: ApprovalManager = request.app.state.approval_manager
    items = approval_manager.list_pending_tool_calls()
    return ApiResponse(success=True, data={"items": items})


# --- Mid-execution tool approval endpoints (PolicyHook integration) ---


@execution_router.post(
    "/approvals/tool_decision",
    response_model=ApiResponse,
    status_code=201,
)
async def submit_tool_decision(body: ToolDecisionRequest, request: Request):
    """Approve or reject a pending tool-call without mutating memorial status."""
    approval_manager: ApprovalManager = request.app.state.approval_manager
    actor = get_auth_context(request).principal.id
    try:
        decree = await approval_manager.submit_tool_decision(
            memorial_id=body.memorial_id,
            action=body.action,
            comment=body.comment,
            grant_scope=body.grant_scope,
            grant_reason=body.grant_reason,
            actor=actor,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return ApiResponse(success=True, data=decree.model_dump(mode="json"))


# --- DAG endpoints (Phase 3) ---


@execution_router.get("/dag/by-edict/{edict_id}")
def get_dag_by_edict(edict_id: str, request: Request):
    storage: Storage = request.app.state.storage
    dag = storage.get_dag_by_edict(edict_id)
    if not dag:
        raise HTTPException(status_code=404, detail=f"No DAG for edict '{edict_id}'")
    return ApiResponse(success=True, data=dag.model_dump(mode="json"))


@execution_router.get("/dag/{dag_id}")
def get_dag(dag_id: str, request: Request):
    storage: Storage = request.app.state.storage
    dag = storage.get_dag_execution(dag_id)
    if not dag:
        raise HTTPException(status_code=404, detail=f"DAG '{dag_id}' not found")
    return ApiResponse(success=True, data=dag.model_dump(mode="json"))


@execution_router.post("/dag/{dag_id}/cancel", response_model=ApiResponse)
async def cancel_dag(dag_id: str, request: Request):
    executor: Executor = request.app.state.executor
    try:
        cancelled = await executor.cancel_dag(dag_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data={"dag_id": dag_id, "cancelled_nodes": cancelled})


@execution_router.post("/dag/{dag_id}/retry", response_model=ApiResponse)
async def retry_dag(dag_id: str, request: Request):
    executor: Executor = request.app.state.executor
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    from_node_ids = body.get("from_node_ids")
    try:
        reset_ids = await executor.retry_dag(dag_id, from_node_ids=from_node_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(success=True, data={"dag_id": dag_id, "reset_node_ids": reset_ids})


# --- Worker endpoints (Phase 3) ---


@execution_router.get("/workers")
async def list_workers(request: Request):
    pool: WorkerPool = request.app.state.worker_pool
    return ApiResponse(success=True, data={"active": pool.list_active()})


@execution_router.get("/workers/status")
async def get_workers_status(request: Request):
    pool: WorkerPool = request.app.state.worker_pool
    lane_manager: LaneManager = request.app.state.lane_manager
    pool_status = pool.status()
    return ApiResponse(
        success=True,
        data={
            "pool": {
                "active_count": pool_status.active_count,
                "max_concurrency": pool_status.max_concurrency,
                "pending_count": pool_status.pending_count,
                "completed_count": pool_status.completed_count,
                "failed_count": pool_status.failed_count,
            },
            "lanes": lane_manager.status(),
        },
    )


@execution_router.get("/memorials/by-persona/{persona_id}")
def list_memorials_by_persona(
    persona_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    storage: Storage = request.app.state.storage
    grouped, total = storage.list_memorials_by_persona(persona_id, limit=limit, offset=offset)
    return ApiResponse(
        success=True,
        data=grouped,
        metadata={"total": total, "limit": limit, "offset": offset},
    )


# --- Planner stats ---


@execution_router.get("/planner/stats")
def get_planner_stats(request: Request):
    """Get planning statistics: total edicts, passthrough count, DAG count, avg tasks."""
    storage: Storage = request.app.state.storage
    with storage._lock:
        total_edicts = storage._conn.execute("SELECT COUNT(*) FROM edicts").fetchone()[0]
        dag_count = storage._conn.execute("SELECT COUNT(*) FROM dag_executions").fetchone()[0]
        avg_tasks_row = storage._conn.execute(
            "SELECT AVG(node_count) FROM (SELECT dag_execution_id, COUNT(*) AS node_count FROM dag_nodes GROUP BY dag_execution_id)"
        ).fetchone()
        avg_tasks = round(avg_tasks_row[0], 1) if avg_tasks_row[0] else 0

        # Recent planning history: edicts + whether they have DAGs
        recent_rows = storage._conn.execute(
            """SELECT e.id, e.title, e.goal, e.assigned_persona_id, e.planner_persona_id,
                      e.created_at,
                      d.id AS dag_id,
                      (SELECT COUNT(*) FROM dag_nodes dn WHERE dn.dag_execution_id = d.id) AS node_count
               FROM edicts e
               LEFT JOIN dag_executions d ON d.edict_id = e.id
               ORDER BY e.created_at DESC
               LIMIT 20""",
        ).fetchall()

    passthrough_count = total_edicts - dag_count
    history = []
    for r in recent_rows:
        keys = r.keys()
        history.append(
            {
                "edict_id": r["id"],
                "title": r["title"],
                "goal": r["goal"],
                "assigned_persona_id": r["assigned_persona_id"]
                if "assigned_persona_id" in keys
                else None,
                "planner_persona_id": r["planner_persona_id"]
                if "planner_persona_id" in keys
                else None,
                "plan_type": "dag" if r["dag_id"] else "passthrough",
                "task_count": r["node_count"] or 1,
                "created_at": r["created_at"],
            }
        )

    return ApiResponse(
        success=True,
        data={
            "total_edicts": total_edicts,
            "passthrough_count": passthrough_count,
            "dag_count": dag_count,
            "avg_tasks_per_dag": avg_tasks,
            "recent_history": history,
        },
    )
