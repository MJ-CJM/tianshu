"""WebSocket authentication and connection lifecycle contracts."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tianshu.config import TianshuSettings
from tianshu.gateway import gateway_router
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.storage import Storage


def _settings() -> TianshuSettings:
    token_hash = hashlib.sha256(b"bootstrap-token-for-tests").hexdigest()
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url="https://tianshu.example.com",
        allowed_hosts="tianshu.example.com",
        allowed_origins="https://tianshu.example.com",
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=f"sha256:{token_hash}",
    )


class RecordingNotifier:
    def __init__(self) -> None:
        self.registered: list = []
        self.unregistered: list = []
        self.principal_ids: list[str] = []

    def register_ws(self, websocket) -> None:
        self.registered.append(websocket)
        self.principal_ids.append(websocket.state.auth_context.principal.id)

    def unregister_ws(self, websocket) -> None:
        self.unregistered.append(websocket)


def _app(storage: Storage) -> tuple[FastAPI, AuthService, RecordingNotifier]:
    settings = _settings()
    service = AuthService(storage, settings)
    notifier = RecordingNotifier()
    app = FastAPI()
    app.state.settings = settings
    app.state.auth_service = service
    app.state.public_webhook_paths = set()
    app.state.notifier = notifier
    app.include_router(gateway_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app, service, notifier


def test_secure_remote_websocket_rejects_anonymous_and_query_token(storage: Storage) -> None:
    app, _, _ = _app(storage)
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        with pytest.raises(WebSocketDisconnect) as anonymous, client.websocket_connect(
            "/api/ws",
            headers={
                "Host": "tianshu.example.com",
                "Origin": "https://tianshu.example.com",
                "X-Forwarded-Proto": "https",
            },
        ):
            pass
        with pytest.raises(WebSocketDisconnect) as query_token, client.websocket_connect(
            "/api/ws?token=bootstrap-token-for-tests",
            headers={
                "Host": "tianshu.example.com",
                "Origin": "https://tianshu.example.com",
                "X-Forwarded-Proto": "https",
            },
        ):
            pass

    assert anonymous.value.code == 4401
    assert query_token.value.code == 4401


def test_websocket_accepts_bearer_and_http_only_session_cookie(storage: Storage) -> None:
    app, service, notifier = _app(storage)
    session = service.create_session("bootstrap-token-for-tests")
    assert session is not None
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        with client.websocket_connect(
            "/api/ws",
            headers={
                "Origin": "https://tianshu.example.com",
                "Host": "tianshu.example.com",
                "Authorization": "Bearer bootstrap-token-for-tests",
                "X-Forwarded-Proto": "https",
            },
        ) as websocket:
            websocket.send_text("ping")
        with client.websocket_connect(
            "/api/ws",
            headers={
                "Origin": "https://tianshu.example.com",
                "Host": "tianshu.example.com",
                "Cookie": f"tianshu_access={session.access_token}",
                "X-Forwarded-Proto": "https",
            },
        ) as websocket:
            websocket.send_text("ping")

    assert notifier.principal_ids == ["user:owner", "user:owner"]
    assert notifier.unregistered == notifier.registered


def test_websocket_rejects_untrusted_origin_before_registration(storage: Storage) -> None:
    app, _, notifier = _app(storage)
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client, pytest.raises(WebSocketDisconnect) as rejected, client.websocket_connect(
        "/api/ws",
        headers={
            "Origin": "https://evil.example",
            "Host": "tianshu.example.com",
            "X-Forwarded-Proto": "https",
            "Authorization": "Bearer bootstrap-token-for-tests",
        },
    ):
        pass

    assert rejected.value.code == 4403
    assert notifier.registered == []


@pytest.mark.asyncio
async def test_websocket_endpoint_unregisters_after_unexpected_receive_error() -> None:
    from tianshu.gateway.api import websocket_endpoint

    notifier = RecordingNotifier()

    class BrokenWebSocket:
        def __init__(self) -> None:
            self.app = SimpleNamespace(state=SimpleNamespace(notifier=notifier))
            self.state = SimpleNamespace(
                auth_context=SimpleNamespace(
                    principal=SimpleNamespace(id="user:owner")
                )
            )

        async def accept(self) -> None:
            return None

        async def receive_text(self) -> str:
            raise RuntimeError("unexpected receive failure")

    websocket = BrokenWebSocket()
    with pytest.raises(RuntimeError, match="unexpected receive failure"):
        await websocket_endpoint(websocket)  # type: ignore[arg-type]

    assert notifier.registered == [websocket]
    assert notifier.unregistered == [websocket]
