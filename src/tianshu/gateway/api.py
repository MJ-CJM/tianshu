"""Gateway 兜底 router：WebSocket 通知 + 会诊（consultations）。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from tianshu.consultation.models import ConsultationRequest, RoundRequest
from tianshu.consultation.session import ConsultationSession
from tianshu.gateway.auth import AuthService, get_auth_context
from tianshu.models import ApiResponse
from tianshu.notifier.notifier import Notifier

gateway_router = APIRouter()
WS_AUTH_REVALIDATE_INTERVAL_SECONDS = 1.0


# --- WebSocket endpoint ---


@gateway_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, request: Request = None):
    notifier: Notifier = websocket.app.state.notifier
    auth_service: AuthService = websocket.app.state.auth_service
    auth_context = get_auth_context(websocket)
    await websocket.accept()
    notifier.register_ws(websocket)
    monitor_task: asyncio.Task[None] | None = None
    try:
        if not auth_service.is_context_active(auth_context):
            await websocket.close(
                code=4401,
                reason="credential_expired_or_revoked",
            )
            return

        async def monitor_credential() -> None:
            while True:
                await asyncio.sleep(WS_AUTH_REVALIDATE_INTERVAL_SECONDS)
                if auth_service.is_context_active(auth_context):
                    continue
                await websocket.close(
                    code=4401,
                    reason="credential_expired_or_revoked",
                )
                return

        monitor_task = asyncio.create_task(monitor_credential())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if monitor_task is not None:
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task
        notifier.unregister_ws(websocket)


# --- Consultation endpoints (Phase 3) ---


@gateway_router.post("/consultations", response_model=ApiResponse, status_code=202)
async def create_consultation(request: Request):
    consultation: ConsultationSession = request.app.state.consultation
    body = await request.json()
    req = ConsultationRequest(**body)

    pending = consultation.create_pending(req)
    _spawn_consultation(request.app, consultation, pending.id)
    return ApiResponse(success=True, data={"id": pending.id, "status": pending.status})


@gateway_router.get("/consultations")
def list_consultations(
    request: Request,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    consultation: ConsultationSession = request.app.state.consultation
    limit = max(1, min(limit, 100))
    items = consultation.list_recent(status=status, limit=limit, offset=max(0, offset))
    return ApiResponse(
        success=True,
        data=[item.model_dump(mode="json") for item in items],
    )


@gateway_router.get("/consultations/{consultation_id}")
def get_consultation(consultation_id: str, request: Request):
    consultation: ConsultationSession = request.app.state.consultation
    result = consultation.get(consultation_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Consultation '{consultation_id}' not found")
    return ApiResponse(success=True, data=result.model_dump(mode="json"))


@gateway_router.post("/consultations/{consultation_id}/rounds", status_code=202)
async def append_consultation_round(consultation_id: str, request: Request):
    """追问一轮：participant_ids 为空则沿用首轮全体（issue #55）。"""
    consultation: ConsultationSession = request.app.state.consultation
    body = await request.json()
    round_request = RoundRequest(**body)
    if not round_request.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt must not be empty")

    try:
        round_ = consultation.append_round(consultation_id, round_request)
    except ValueError as exc:
        # 廷议不存在 → 404；上一轮还没跑完 → 409
        status = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    _spawn_consultation(request.app, consultation, consultation_id)
    return ApiResponse(
        success=True,
        data={"id": round_.id, "round_index": round_.round_index, "status": round_.status},
    )


@gateway_router.put("/consultations/{consultation_id}/verdict")
async def set_consultation_verdict(consultation_id: str, request: Request):
    """落裁决——LLM 只出票拟，最终决定由用户写下（issue #55）。"""
    consultation: ConsultationSession = request.app.state.consultation
    body = await request.json()
    verdict = str(body.get("verdict", "")).strip()
    if not verdict:
        raise HTTPException(status_code=422, detail="verdict must not be empty")

    updated = consultation.set_verdict(consultation_id, verdict)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Consultation '{consultation_id}' not found")
    return ApiResponse(success=True, data=updated.model_dump(mode="json"))


def _spawn_consultation(app, consultation: ConsultationSession, consultation_id: str) -> None:
    """后台跑一场廷议。

    task 必须被强引用：CPython 只对运行中的 task 保留弱引用，丢引用会被 GC 中途
    回收，廷议永远停在 running（issue #52）。done callback 负责摘引用并把逃逸异常
    落到 failed，否则异常会被静默吞掉。
    """
    tasks: set[asyncio.Task] = app.state.consultation_tasks

    task = asyncio.create_task(consultation.run(consultation_id))
    tasks.add(task)

    def _on_done(finished: asyncio.Task) -> None:
        tasks.discard(finished)
        if finished.cancelled():
            consultation.mark_failed(consultation_id, "consultation task cancelled")
            return
        exc = finished.exception()
        if exc is not None:
            consultation.mark_failed(consultation_id, f"{type(exc).__name__}: {exc}")

    task.add_done_callback(_on_done)
