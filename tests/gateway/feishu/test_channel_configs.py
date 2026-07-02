"""通政司 channel_configs 表 + Storage 加密读写测试。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    from cryptography.fernet import Fernet

    from tianshu.secrets.vault import reset_vault

    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", Fernet.generate_key().decode())
    reset_vault()
    yield
    reset_vault()


def test_save_then_load(storage):
    cfg = {"app_id": "cli_x", "domain": "feishu", "connection_mode": "websocket"}
    storage.save_channel_config("feishu", cfg, secret_plaintext="secret_xyz")

    masked = storage.get_channel_config("feishu")
    assert masked is not None
    assert masked["app_id"] == "cli_x"
    assert masked["_has_secret"] is True

    runtime = storage.load_channel_runtime_config("feishu")
    assert runtime is not None
    assert runtime["app_secret"] == "secret_xyz"


def test_save_without_secret_keeps_existing(storage):
    storage.save_channel_config("feishu", {"app_id": "x"}, secret_plaintext="s1")
    # 不传 secret → 不动 encrypted_secret
    storage.save_channel_config("feishu", {"app_id": "y"}, secret_plaintext=None)
    runtime = storage.load_channel_runtime_config("feishu")
    assert runtime["app_id"] == "y"
    assert runtime["app_secret"] == "s1"


def test_save_clear_secret(storage):
    storage.save_channel_config("feishu", {"app_id": "x"}, secret_plaintext="s1")
    # 空串 → 清空 secret
    storage.save_channel_config("feishu", {"app_id": "x"}, secret_plaintext="")
    runtime = storage.load_channel_runtime_config("feishu")
    assert runtime["app_secret"] == ""


def test_save_without_master_key_raises(storage, monkeypatch):
    monkeypatch.delenv("TIANSHU_SECRET_MASTER_KEY", raising=False)
    from tianshu.secrets.vault import reset_vault

    reset_vault()
    with pytest.raises(RuntimeError, match="MASTER_KEY"):
        storage.save_channel_config("feishu", {"app_id": "x"}, secret_plaintext="s")


def test_unconfigured_returns_none(storage):
    assert storage.get_channel_config("feishu") is None
    assert storage.load_channel_runtime_config("feishu") is None
