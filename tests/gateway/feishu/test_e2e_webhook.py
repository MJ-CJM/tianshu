"""端到端：Webhook 模式下，POST /feishu/webhook → Edict 创建/续接。"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_feishu(monkeypatch):
    monkeypatch.setenv("TIANSHU_FEISHU_APP_ID", "test_app")
    monkeypatch.setenv("TIANSHU_FEISHU_APP_SECRET", "test_sec")
    monkeypatch.setenv("TIANSHU_FEISHU_ALLOWED_USERS", "ou_test")
    monkeypatch.setenv("TIANSHU_FEISHU_CONNECTION_MODE", "webhook")
    # 关闭文本批处理，让事件立即派发
    monkeypatch.setenv("TIANSHU_FEISHU_TEXT_BATCH_DELAY", "0.0")
    # LLM 占位（执行器需要）
    monkeypatch.setenv("TIANSHU_LLM_API_KEY", "fake")
    monkeypatch.setenv("TIANSHU_LLM_API_BASE", "http://localhost:9999")
    from tianshu.app import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client, app


def _build_msg_payload(*, event_id: str, chat_id: str, sender_open_id: str, text: str) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "token": "",
            "app_id": "test_app",
            "tenant_key": "t",
            "create_time": "1700000000000",
        },
        "event": {
            "sender": {"sender_id": {"open_id": sender_open_id}},
            "message": {
                "message_id": f"om_{event_id}",
                "chat_id": chat_id,
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}),
                "mentions": [],
            },
        },
    }


def test_message_creates_edict(app_with_feishu):
    client, app = app_with_feishu
    storage = app.state.storage

    payload = _build_msg_payload(
        event_id="evt_1", chat_id="oc_chat",
        sender_open_id="ou_test", text="帮我看看进度",
    )
    resp = client.post("/feishu/webhook", json=payload)
    assert resp.status_code == 200

    # dispatcher 是异步消费 inbound_queue → executor 异步任务，等一拍
    time.sleep(1.5)
    edicts, _ = storage.list_edicts()
    assert len(edicts) >= 1
    e = edicts[0]
    assert (e.metadata or {}).get("chat_id") == "oc_chat"
    assert (e.metadata or {}).get("feishu_user") == "ou_test"


def test_dedup_repeated_event_id(app_with_feishu):
    client, app = app_with_feishu
    storage = app.state.storage

    payload = _build_msg_payload(
        event_id="evt_dup", chat_id="oc_x",
        sender_open_id="ou_test", text="一次任务",
    )
    r1 = client.post("/feishu/webhook", json=payload)
    assert r1.status_code == 200
    time.sleep(0.3)
    # 同 event_id 第二次：webhook 层去重 → 不会创建新 Edict
    r2 = client.post("/feishu/webhook", json=payload)
    assert r2.status_code == 200
    time.sleep(1.5)

    edicts, _ = storage.list_edicts()
    # 只有 evt_dup 这一条创建出来；同 event_id 第二次被去重
    assert len(edicts) == 1


def test_message_from_non_allowlisted_user_ignored(app_with_feishu):
    client, app = app_with_feishu
    storage = app.state.storage

    payload = _build_msg_payload(
        event_id="evt_evil", chat_id="oc_x",
        sender_open_id="ou_attacker",  # 不在 allowlist
        text="malicious",
    )
    resp = client.post("/feishu/webhook", json=payload)
    assert resp.status_code == 200  # webhook 仍 200（避免飞书重投）
    time.sleep(0.5)
    # 不允许的用户不会落 Edict
    edicts, _ = storage.list_edicts()
    assert len(edicts) == 0


def test_url_verification_challenge(app_with_feishu):
    client, _ = app_with_feishu
    resp = client.post(
        "/feishu/webhook",
        json={"type": "url_verification", "challenge": "abc123"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}
