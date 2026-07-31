"""Channel configuration APIs never disclose write-only webhook credentials."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway import tongzheng_api
from tianshu.gateway.tongzheng_api import tongzheng_router


def _client(storage) -> TestClient:
    app = FastAPI()
    app.state.storage = storage
    app.include_router(tongzheng_router, prefix="/api")
    return TestClient(app)


def test_feishu_default_view_masks_webhook_credentials_and_omitted_update_preserves_them(storage):
    storage.save_channel_instance(
        instance_id="feishu-default",
        channel_type="feishu",
        label="默认",
        enabled=True,
        config={
            "app_id": "cli_x",
            "encrypt_key": "encrypt-secret",
            "verification_token": "verify-secret",
        },
    )

    with _client(storage) as client:
        response = client.get("/api/tongzheng/channels/feishu")
        update = client.put("/api/tongzheng/channels/feishu", json={"app_id": "cli_y"})

    assert response.status_code == 200
    assert response.json()["data"]["encrypt_key"] == "***"
    assert response.json()["data"]["verification_token"] == "***"
    assert "encrypt-secret" not in response.text
    assert "verify-secret" not in response.text
    assert update.status_code == 200
    runtime = storage.load_channel_instance_runtime("feishu-default")
    assert runtime is not None
    assert runtime["encrypt_key"] == "encrypt-secret"
    assert runtime["verification_token"] == "verify-secret"


def test_telegram_default_view_masks_webhook_secret_and_mask_round_trip_preserves_it(storage):
    storage.save_channel_instance(
        instance_id="telegram-default",
        channel_type="telegram",
        label="默认",
        enabled=True,
        config={"connection_mode": "webhook", "webhook_secret": "telegram-secret"},
    )

    with _client(storage) as client:
        response = client.get("/api/tongzheng/channels/telegram")
        update = client.put(
            "/api/tongzheng/channels/telegram",
            json={"connection_mode": "webhook", "webhook_secret": "***"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["webhook_secret"] == "***"
    assert "telegram-secret" not in response.text
    assert update.status_code == 200
    runtime = storage.load_channel_instance_runtime("telegram-default")
    assert runtime is not None
    assert runtime["webhook_secret"] == "telegram-secret"


def test_instance_list_and_detail_mask_channel_webhook_credentials(storage):
    storage.save_channel_instance(
        instance_id="feishu-extra",
        channel_type="feishu",
        label="飞书",
        enabled=False,
        config={
            "encrypt_key": "feishu-encrypt-sensitive-value",
            "verification_token": "feishu-verify-sensitive-value",
        },
    )
    storage.save_channel_instance(
        instance_id="telegram-extra",
        channel_type="telegram",
        label="电报",
        enabled=False,
        config={"webhook_secret": "hook-secret"},
    )

    with _client(storage) as client:
        listed = client.get("/api/tongzheng/instances")
        feishu = client.get("/api/tongzheng/instances/feishu-extra")
        telegram = client.get("/api/tongzheng/instances/telegram-extra")

    assert listed.status_code == 200
    assert "feishu-encrypt-sensitive-value" not in listed.text
    assert "feishu-verify-sensitive-value" not in listed.text
    assert "hook-secret" not in listed.text
    assert feishu.json()["data"]["encrypt_key"] == "***"
    assert feishu.json()["data"]["verification_token"] == "***"
    assert telegram.json()["data"]["webhook_secret"] == "***"


def test_generic_instance_update_omitted_or_masked_credentials_preserve_existing_values(storage):
    storage.save_channel_instance(
        instance_id="feishu-extra",
        channel_type="feishu",
        label="飞书",
        enabled=False,
        config={"app_id": "old", "encrypt_key": "enc", "verification_token": "token"},
    )

    with _client(storage) as client:
        first = client.put(
            "/api/tongzheng/instances/feishu-extra",
            json={"config": {"app_id": "new"}},
        )
        second = client.put(
            "/api/tongzheng/instances/feishu-extra",
            json={
                "config": {
                    "app_id": "newer",
                    "encrypt_key": "***",
                    "verification_token": "***",
                }
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    runtime = storage.load_channel_instance_runtime("feishu-extra")
    assert runtime is not None
    assert runtime["app_id"] == "newer"
    assert runtime["encrypt_key"] == "enc"
    assert runtime["verification_token"] == "token"


def test_explicit_empty_webhook_credentials_clear_existing_values(storage):
    storage.save_channel_instance(
        instance_id="telegram-default",
        channel_type="telegram",
        label="默认",
        enabled=True,
        config={"webhook_secret": "hook-secret"},
    )

    with _client(storage) as client:
        response = client.put(
            "/api/tongzheng/channels/telegram",
            json={"webhook_secret": ""},
        )

    assert response.status_code == 200
    runtime = storage.load_channel_instance_runtime("telegram-default")
    assert runtime is not None
    assert runtime["webhook_secret"] == ""


def test_eval_mode_saves_and_enables_without_starting_external_channels(storage, monkeypatch):
    storage.save_channel_instance(
        instance_id="feishu-extra",
        channel_type="feishu",
        label="飞书",
        enabled=False,
        config={"app_id": "cli_test"},
    )
    runtime_settings = SimpleNamespace(validate_or_raise=lambda: None)
    monkeypatch.setattr(
        tongzheng_api,
        "_settings_for_instance",
        lambda _storage, _instance_id: runtime_settings,
    )
    bot_manager = SimpleNamespace(reload_instance=AsyncMock(return_value=True))
    client = _client(storage)
    client.app.state.settings = SimpleNamespace(eval_mode=True)
    client.app.state.bot_manager = bot_manager

    with client:
        saved = client.put(
            "/api/tongzheng/channels/telegram",
            json={"connection_mode": "polling"},
        )
        enabled = client.patch(
            "/api/tongzheng/instances/feishu-extra/enabled",
            json={"enabled": True},
        )

    assert saved.status_code == 200
    assert saved.json()["data"] == {
        "reloaded": False,
        "reason": "external connections disabled in eval mode",
    }
    assert enabled.status_code == 200
    assert enabled.json()["data"]["enabled"] is True
    assert storage.get_channel_instance("telegram-default")["enabled"] is True
    assert storage.get_channel_instance("feishu-extra")["enabled"] is True
    bot_manager.reload_instance.assert_not_awaited()
