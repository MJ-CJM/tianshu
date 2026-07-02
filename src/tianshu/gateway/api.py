"""Gateway 兜底 router：WebSocket 通知 + 会诊（consultations）。"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from tianshu.consultation.models import ConsultationRequest
from tianshu.consultation.session import ConsultationSession
from tianshu.models import ApiResponse
from tianshu.notifier.notifier import Notifier

gateway_router = APIRouter()


# --- WebSocket endpoint ---


@gateway_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, request: Request = None):
    notifier: Notifier = websocket.app.state.notifier
    await websocket.accept()
    notifier.register_ws(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        notifier.unregister_ws(websocket)


# --- Consultation endpoints (Phase 3) ---


@gateway_router.post("/consultations", response_model=ApiResponse, status_code=202)
async def create_consultation(request: Request):
    import asyncio

    from tianshu.consultation.models import ConsultationResponse

    consultation: ConsultationSession = request.app.state.consultation
    body = await request.json()
    req = ConsultationRequest(**body)

    placeholder = ConsultationResponse(request=req, status="running")
    consultation._sessions[placeholder.id] = placeholder

    async def _run() -> None:
        result = await consultation.start(req)
        result.id = placeholder.id
        consultation._sessions[placeholder.id] = result

    asyncio.create_task(_run())
    return ApiResponse(success=True, data={"id": placeholder.id, "status": "running"})


@gateway_router.get("/consultations/{consultation_id}")
async def get_consultation(consultation_id: str, request: Request):
    consultation: ConsultationSession = request.app.state.consultation
    result = consultation.get(consultation_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Consultation '{consultation_id}' not found")
    return ApiResponse(success=True, data=result.model_dump(mode="json"))
