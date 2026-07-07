"""WebhookConnection：rate limit / 签名失败 / token 失败 / dedup / body 长度。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import MagicMock as _MM

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway.feishu.connection import WebhookConnection, WebSocketConnection
from tianshu.gateway.feishu.settings import FeishuSettings


def _settings(*, encrypt_key: str = "", token: str = "") -> FeishuSettings:
    return FeishuSettings(
        app_id="x",
        app_secret="y",
        domain="feishu",
        connection_mode="webhook",
        allowed_users=("ou_test",),
        home_channel="",
        encrypt_key=encrypt_key,
        verification_token=token,
        bot_open_id="",
        bot_name="",
        webhook_path="/feishu/webhook",
        ws_reconnect_interval=120,
        text_batch_delay=0.0,
        dedup_cache_size=2048,
    )


@pytest.fixture
def conn_app(storage):
    queue: asyncio.Queue = asyncio.Queue()
    conn = WebhookConnection(settings=_settings(), storage=storage, inbound_queue=queue)
    app = FastAPI()
    app.include_router(conn.router)
    return conn, queue, app


def test_url_verification_returns_challenge(conn_app):
    _, _, app = conn_app
    with TestClient(app) as client:
        r = client.post("/feishu/webhook", json={"type": "url_verification", "challenge": "abc"})
        assert r.status_code == 200
        assert r.json() == {"challenge": "abc"}


def test_normal_event_enqueued(conn_app):
    _, queue, app = conn_app
    with TestClient(app) as client:
        body = {"header": {"event_type": "im.message.receive_v1", "event_id": "evt1"}, "event": {}}
        r = client.post("/feishu/webhook", json=body)
        assert r.status_code == 200
    # 队列应有一条
    assert queue.qsize() == 1


def test_dedup_drops_repeat(conn_app):
    _, queue, app = conn_app
    with TestClient(app) as client:
        body = {"header": {"event_type": "im.message.receive_v1", "event_id": "dup"}, "event": {}}
        r1 = client.post("/feishu/webhook", json=body)
        r2 = client.post("/feishu/webhook", json=body)
        assert r1.status_code == 200
        assert r2.status_code == 200
    assert queue.qsize() == 1  # 第二次被去重


def test_invalid_signature_rejected(storage):
    queue: asyncio.Queue = asyncio.Queue()
    conn = WebhookConnection(
        settings=_settings(encrypt_key="hidden_key"),
        storage=storage,
        inbound_queue=queue,
    )
    app = FastAPI()
    app.include_router(conn.router)
    with TestClient(app) as client:
        r = client.post("/feishu/webhook", json={"event": {}})
        assert r.status_code == 401


def test_valid_signature_passes(storage):
    queue: asyncio.Queue = asyncio.Queue()
    key = "secret_key"
    conn = WebhookConnection(
        settings=_settings(encrypt_key=key),
        storage=storage,
        inbound_queue=queue,
    )
    app = FastAPI()
    app.include_router(conn.router)
    body = json.dumps({"header": {"event_type": "im.test", "event_id": "ok"}, "event": {}}).encode()
    timestamp = "1700000000"
    nonce = "n"
    sig = hashlib.sha256(f"{timestamp}{nonce}{key}".encode() + body).hexdigest()
    with TestClient(app) as client:
        r = client.post(
            "/feishu/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Lark-Request-Timestamp": timestamp,
                "X-Lark-Request-Nonce": nonce,
                "X-Lark-Signature": sig,
            },
        )
        assert r.status_code == 200


def test_invalid_token_rejected(storage):
    queue: asyncio.Queue = asyncio.Queue()
    conn = WebhookConnection(
        settings=_settings(token="expected_token"),
        storage=storage,
        inbound_queue=queue,
    )
    app = FastAPI()
    app.include_router(conn.router)
    body = {"header": {"event_type": "im.test", "event_id": "x", "token": "wrong"}, "event": {}}
    with TestClient(app) as client:
        r = client.post("/feishu/webhook", json=body)
        assert r.status_code == 401


def test_bad_json_returns_400(conn_app):
    _, _, app = conn_app
    with TestClient(app) as client:
        r = client.post(
            "/feishu/webhook", content=b"<not json>", headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 400


def test_body_too_large_returns_413(conn_app):
    _, _, app = conn_app
    with TestClient(app) as client:
        # 1MB+1 bytes
        big = b"a" * (1024 * 1024 + 100)
        r = client.post(
            "/feishu/webhook", content=big, headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 413


def test_rate_limit_window(storage):
    """超出 60s/120 req 应返 429。"""
    queue: asyncio.Queue = asyncio.Queue()
    conn = WebhookConnection(settings=_settings(), storage=storage, inbound_queue=queue)
    # 直接打满 _allow_rate
    key = "k"
    for _ in range(120):
        assert conn._allow_rate(key) is True
    assert conn._allow_rate(key) is False  # 第 121 次拒绝


def test_rate_state_eviction(storage):
    """rate_state 超过 _RATE_MAX_KEYS 时按 LRU 淘汰旧 key。"""
    queue: asyncio.Queue = asyncio.Queue()
    conn = WebhookConnection(settings=_settings(), storage=storage, inbound_queue=queue)
    conn._RATE_MAX_KEYS = 3  # 缩小用于测试
    for k in ("k1", "k2", "k3"):
        conn._allow_rate(k)
    conn._allow_rate("k4")  # 触发淘汰
    assert "k1" not in conn._rate_state
    assert "k4" in conn._rate_state


@pytest.mark.asyncio
async def test_start_stop_no_op(storage):
    queue: asyncio.Queue = asyncio.Queue()
    conn = WebhookConnection(settings=_settings(), storage=storage, inbound_queue=queue)
    await conn.start()
    await conn.stop()  # 双 no-op，不应抛


# --- WebSocketConnection 静态方法 / 转换逻辑测试 ---


def test_sdk_message_to_payload_with_full_event():
    """SDK 事件对象 → webhook 兼容字典。"""
    event = _MM()
    event.header.event_id = "evt_x"
    event.event.message.message_id = "om_1"
    event.event.message.chat_id = "oc_y"
    event.event.message.chat_type = "p2p"
    event.event.message.message_type = "text"
    event.event.message.content = '{"text":"hi"}'
    mention = _MM()
    mention.id.open_id = "ou_bot"
    mention.name = "bot"
    event.event.message.mentions = [mention]
    event.event.sender.sender_id.open_id = "ou_user"
    event.event.sender.sender_id.user_id = "uu"
    event.event.sender.sender_id.union_id = "un"
    payload = WebSocketConnection._sdk_message_to_payload(event)
    assert payload["header"]["event_type"] == "im.message.receive_v1"
    assert payload["header"]["event_id"] == "evt_x"
    assert payload["event"]["message"]["chat_id"] == "oc_y"
    assert payload["event"]["sender"]["sender_id"]["open_id"] == "ou_user"
    assert payload["event"]["message"]["mentions"][0]["id"]["open_id"] == "ou_bot"


def test_sdk_message_to_payload_handles_none_event():
    """event=None → 返回空字典而不是崩溃。"""
    obj = _MM()
    obj.event = None
    payload = WebSocketConnection._sdk_message_to_payload(obj)
    assert payload["header"]["event_type"] == "im.message.receive_v1"
    assert payload["event"] == {}


def test_sdk_card_to_payload_with_full_event():
    event = _MM()
    event.header.event_id = "ec"
    event.event.operator.open_id = "ou_test"
    event.event.action.value = {"action": "approve"}
    event.event.context.open_chat_id = "oc_x"
    payload = WebSocketConnection._sdk_card_to_payload(event)
    assert payload["header"]["event_type"] == "card.action.trigger"
    assert payload["event"]["operator"]["open_id"] == "ou_test"
    assert payload["event"]["action"]["value"] == {"action": "approve"}
    assert payload["event"]["context"]["open_chat_id"] == "oc_x"


@pytest.mark.asyncio
async def test_websocket_on_message_enqueues_payload(storage):
    """_on_message 转 payload 后 schedule 到 loop 入队。"""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    conn = WebSocketConnection(
        settings=_settings(),
        storage=storage,
        inbound_queue=queue,
        loop=loop,
    )
    event = _MM()
    event.header.event_id = "evt"
    event.event.message.message_id = "om"
    event.event.message.chat_id = "oc"
    event.event.message.chat_type = "p2p"
    event.event.message.message_type = "text"
    event.event.message.content = '{"text":"hi"}'
    event.event.message.mentions = []
    event.event.sender.sender_id.open_id = "ou_test"
    event.event.sender.sender_id.user_id = ""
    event.event.sender.sender_id.union_id = ""
    conn._on_message(event)
    # asyncio.run_coroutine_threadsafe 异步调度，等一拍
    await asyncio.sleep(0.05)
    assert queue.qsize() == 1
    payload = await queue.get()
    assert payload["event"]["message"]["chat_id"] == "oc"


@pytest.mark.asyncio
async def test_websocket_on_card_enqueues_and_returns_response(storage):
    """_on_card 必须返回 P2CardActionTriggerResponse。"""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    conn = WebSocketConnection(
        settings=_settings(),
        storage=storage,
        inbound_queue=queue,
        loop=loop,
    )
    event = _MM()
    event.header.event_id = "ec"
    event.event.operator.open_id = "ou_test"
    event.event.action.value = {"action": "approve"}
    event.event.context.open_chat_id = "oc"
    resp = conn._on_card(event)
    await asyncio.sleep(0.05)
    assert queue.qsize() == 1
    # 仅断言返回非 None（具体类型为 lark SDK 的 P2CardActionTriggerResponse）
    assert resp is not None


@pytest.mark.asyncio
async def test_websocket_stop_cancels_watchdog(storage):
    """stop() 应取消 watchdog_task（即使无 client）。"""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    conn = WebSocketConnection(
        settings=_settings(),
        storage=storage,
        inbound_queue=queue,
        loop=loop,
    )

    # 模拟一个 watchdog task
    async def _wait():
        await asyncio.sleep(60)

    conn._watchdog_task = asyncio.create_task(_wait())
    await conn.stop()
    await asyncio.sleep(0.05)
    assert conn._watchdog_task.cancelled() or conn._watchdog_task.done()
