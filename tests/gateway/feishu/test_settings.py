"""FeishuSettings 单元测试。"""
from __future__ import annotations

import pytest

from tianshu.gateway.feishu.settings import FeishuSettings, from_global_settings


def _make(**overrides) -> FeishuSettings:
    base = dict(
        app_id="cli_test",
        app_secret="sec",
        domain="feishu",
        connection_mode="webhook",
        allowed_users=("ou_user",),
        home_channel="",
        encrypt_key="",
        verification_token="",
        bot_open_id="",
        bot_name="",
        webhook_path="/feishu/webhook",
        ws_reconnect_interval=120,
        text_batch_delay=0.6,
        dedup_cache_size=2048,
    )
    base.update(overrides)
    return FeishuSettings(**base)


def test_enabled_when_app_id_set():
    assert _make().enabled is True


def test_disabled_when_app_id_empty():
    s = _make(app_id="")
    assert s.enabled is False
    # 未启用时 validate 直接返回
    s.validate_or_raise()


def test_validate_requires_secret_and_allowlist():
    # missing app_secret
    with pytest.raises(RuntimeError, match="APP_SECRET"):
        _make(app_secret="").validate_or_raise()
    # missing allowlist
    with pytest.raises(RuntimeError, match="ALLOWED_USERS"):
        _make(allowed_users=()).validate_or_raise()


def test_validate_rejects_invalid_modes():
    with pytest.raises(RuntimeError, match="connection_mode"):
        _make(connection_mode="grpc").validate_or_raise()
    with pytest.raises(RuntimeError, match="domain"):
        _make(domain="bad").validate_or_raise()


def test_from_global_settings_parses_csv():
    """from_global_settings 把逗号分隔字符串拆为 tuple，并保留默认值。"""
    class FakeSettings:
        feishu_app_id = "x"
        feishu_app_secret = "y"
        feishu_domain = "feishu"
        feishu_connection_mode = "websocket"
        feishu_allowed_users = "ou_a, ou_b ,ou_c"
        feishu_home_channel = ""
        feishu_encrypt_key = ""
        feishu_verification_token = ""
        feishu_bot_open_id = ""
        feishu_bot_name = ""
        feishu_webhook_path = "/feishu/webhook"
        feishu_ws_reconnect_interval = 120
        feishu_text_batch_delay = 0.6
        feishu_dedup_cache_size = 2048

    s = from_global_settings(FakeSettings())
    assert s.allowed_users == ("ou_a", "ou_b", "ou_c")
    assert s.app_id == "x"
    assert s.connection_mode == "websocket"
