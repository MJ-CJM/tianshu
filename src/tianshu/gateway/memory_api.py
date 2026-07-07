"""记忆相关路由（memory + memory-palace）：persona 记忆 CRUD、访问策略、整编（compact/reflect）、记忆宫殿检索。无统一 prefix，路径写全。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.memory.manager import MemoryManager
from tianshu.memory.models import MemoryQuery
from tianshu.models import ApiResponse

memory_router = APIRouter(tags=["memory"])


# --- Memory endpoints ---


@memory_router.post("/memory/recall", response_model=ApiResponse)
async def recall_memory(request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    source = body.pop("source", "sqlite")
    query = MemoryQuery(**body)
    entries = mm.recall(query, source=source)
    return ApiResponse(
        success=True,
        data=[e.model_dump(mode="json") for e in entries],
    )


@memory_router.post("/memory/sync", response_model=ApiResponse)
async def sync_memory_index(request: Request):
    """Manually trigger SQLite index rebuild from Markdown files."""
    mm: MemoryManager = request.app.state.memory_manager
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    persona_id = body.get("persona_id")
    if persona_id:
        count = mm.sync_index(persona_id)
        results = {persona_id: count}
    else:
        results = mm.sync_all_indices()
    return ApiResponse(success=True, data=results)


@memory_router.delete("/memory/{entry_id}", response_model=ApiResponse)
def delete_memory(entry_id: str, request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    deleted = mm.delete(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Memory entry '{entry_id}' not found")
    return ApiResponse(success=True, data={"id": entry_id})


@memory_router.post("/memory/batch-delete", response_model=ApiResponse)
async def batch_delete_memory(request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    entry_ids = body.get("entry_ids", [])
    if not entry_ids:
        raise HTTPException(status_code=400, detail="entry_ids is required")
    deleted = mm.delete_batch(entry_ids)
    return ApiResponse(success=True, data={"deleted": deleted})


@memory_router.get("/memory/policies")
def get_memory_policies(request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    policies = {}
    for pid, policy in mm._access_control._policies.items():
        policies[pid] = {
            "persona_id": policy.persona_id,
            "can_read": policy.can_read,
            "can_write": policy.can_write,
            "share_level": policy.share_level,
        }
    return ApiResponse(success=True, data=policies)


@memory_router.put("/memory/policies/{persona_id}", response_model=ApiResponse)
async def update_memory_policy(persona_id: str, request: Request):
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    from tianshu.memory.access_control import MemoryAccessPolicy

    policy = MemoryAccessPolicy(
        persona_id=persona_id,
        can_read=body.get("can_read", []),
        can_write=body.get("can_write", []),
        share_level=body.get("share_level", "private"),
    )
    mm._access_control.set_policy(policy)
    return ApiResponse(
        success=True,
        data={
            "persona_id": persona_id,
            "can_read": policy.can_read,
            "can_write": policy.can_write,
            "share_level": policy.share_level,
        },
    )


# --- Memory maintenance endpoints (文渊阁·整编) ---


@memory_router.post("/memory/compact", response_model=ApiResponse)
async def compact_memory(request: Request):
    """Trigger memory compaction for a persona."""
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    persona_id = body.get("persona_id")
    if not persona_id:
        raise HTTPException(status_code=400, detail="persona_id is required")
    max_age_days = body.get("max_age_days", 7)
    entries = mm.list_by_persona(persona_id, limit=200)
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    old_entries = [e for e in entries if e.created_at < cutoff.isoformat()]
    if len(old_entries) <= 3:
        return ApiResponse(
            success=True,
            data={
                "status": "skipped",
                "reason": f"Only {len(old_entries)} entries older than {max_age_days} days (need >3)",
            },
        )
    result = await mm._compactor.compact(persona_id, old_entries)
    return ApiResponse(
        success=True,
        data={
            "status": "completed",
            "original_count": result.original_count,
            "compacted_count": result.compacted_count,
            "summary": result.summary,
            "tokens_saved": result.tokens_saved,
        },
    )


@memory_router.post("/memory/reflect", response_model=ApiResponse)
async def trigger_reflection(request: Request):
    """Trigger memory reflection for a persona."""
    mm: MemoryManager = request.app.state.memory_manager
    body = await request.json()
    persona_id = body.get("persona_id")
    if not persona_id:
        raise HTTPException(status_code=400, detail="persona_id is required")
    if not mm._reflector.can_reflect(persona_id):
        return ApiResponse(
            success=True,
            data={
                "status": "cooldown",
                "reason": "Reflection cooldown active (1 hour between reflections)",
            },
        )
    observations = [
        e for e in mm.list_by_persona(persona_id, limit=50) if e.category == "observation"
    ]
    if not observations:
        return ApiResponse(
            success=True,
            data={
                "status": "skipped",
                "reason": "No observations to reflect on",
            },
        )
    insights = await mm._reflector.reflect(persona_id, observations)
    for insight in insights:
        mm.store_to_index(insight)
    return ApiResponse(
        success=True,
        data={
            "status": "completed",
            "insights_generated": len(insights),
            "insights": [i.content for i in insights],
        },
    )


@memory_router.get("/memory/stats")
def get_memory_stats(request: Request):
    """Get memory statistics per persona."""
    mm: MemoryManager = request.app.state.memory_manager
    stats = {}
    for persona_dir in sorted(mm._memory_dir.iterdir()):
        if not persona_dir.is_dir():
            continue
        pid = persona_dir.name
        entries = mm.list_by_persona(pid, limit=500)
        total_chars = sum(len(e.content) for e in entries)
        by_category = {}
        for e in entries:
            by_category[e.category] = by_category.get(e.category, 0) + 1
        md_files = list(persona_dir.glob("**/*.md"))
        md_size = sum(f.stat().st_size for f in md_files)
        stats[pid] = {
            "entry_count": len(entries),
            "total_chars": total_chars,
            "estimated_tokens": total_chars // 4,
            "by_category": by_category,
            "markdown_files": len(md_files),
            "markdown_size_bytes": md_size,
        }
    return ApiResponse(success=True, data=stats)


# NOTE: 参数路由必须注册在 /memory/policies、/memory/stats 等静态段之后，
# 否则 {persona_id} 会把它们全部吞掉（历史 bug：stats/policies 曾不可达）。
@memory_router.get("/memory/{persona_id}")
def get_persona_memory(
    persona_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
):
    mm: MemoryManager = request.app.state.memory_manager
    # Auto-sync once per persona per session (won't re-sync after user deletes)
    mm.auto_sync_if_needed(persona_id)
    entries = mm.list_by_persona(persona_id, limit=limit)
    return ApiResponse(
        success=True,
        data=[e.model_dump(mode="json") for e in entries],
    )


# ---- Memory Palace API ----
# NOTE: dedicated /memory-palace/* prefix to avoid being swallowed by the
# earlier-registered /memory/{persona_id} route.


@memory_router.get("/memory-palace/search")
async def memory_search(
    request: Request,
    query: str = Query(..., description="Search query"),
    wing: str | None = Query(None, description="Filter by wing (persona ID)"),
    room: str | None = Query(None, description="Filter by room"),
    n_results: int = Query(10, ge=1, le=100, description="Max results"),
):
    """Search the Memory Palace drawer store via BM25."""
    drawer_store = getattr(request.app.state, "drawer_store", None)
    if not drawer_store:
        return ApiResponse(success=False, error="Memory Palace not initialized")

    results = await drawer_store.search(query, wing=wing, room=room, n_results=n_results)
    return ApiResponse(
        success=True,
        data={
            "query": query,
            "filters": {"wing": wing, "room": room},
            "results": [
                {
                    "drawer_id": r.drawer_id,
                    "content": r.content,
                    "wing": r.wing,
                    "room": r.room,
                    "score": r.score,
                    "matched_via": r.matched_via,
                }
                for r in results
            ],
        },
    )


@memory_router.get("/memory-palace/l1")
async def memory_l1(
    request: Request,
    wing: str = Query(..., description="Wing to generate L1 for"),
):
    """Get L1 critical facts for a specific wing."""
    drawer_store = getattr(request.app.state, "drawer_store", None)
    memory_config = getattr(request.app.state, "memory_config", None)
    if not drawer_store:
        return ApiResponse(success=False, error="Memory Palace not initialized")

    from tianshu.memory.config import MemoryConfig
    from tianshu.memory.layers import MemoryStack

    config = memory_config or MemoryConfig()
    stack = MemoryStack(store=drawer_store, config=config)
    l1 = await stack.get_l1(wing)
    return ApiResponse(success=True, data={"wing": wing, "l1": l1})
