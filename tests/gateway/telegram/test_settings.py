"""TelegramSettings 校验 + from_global_settings 解析。"""
from __future__ import annotations

import pytest

from tianshu.config import TianshuSettings
from tianshu.gateway.telegram.settings import (
    TelegramSettings,
    _parse_allowed_users,
    from_global_settings,
)

from ._helpers import make_settings


def test_enabled_requires_token():
    assert not make_settings(bot_token="").enabled
    assert make_settings(bot_token="x").enabled


def test_validate_polling_ok():
    make_settings(connection_mode="polling").validate_or_raise()  # 不抛


def test_validate_invalid_mode():
    with pytest.raises(RuntimeError):
        make_settings(connection_mode="grpc").validate_or_raise()


def test_validate_webhook_requires_secret():
    with pytest.raises(RuntimeError):
        make_settings(connection_mode="webhook", webhook_secret="").validate_or_raise()
    make_settings(connection_mode="webhook", webhook_secret="s").validate_or_raise()


def test_disabled_skips_validation():
    # token 空 → enabled False → 即便 mode 非法也不抛
    make_settings(bot_token="", connection_mode="grpc").validate_or_raise()


def test_parse_allowed_users():
    assert _parse_allowed_users("1, 2 ,3") == (1, 2, 3)
    assert _parse_allowed_users("") == ()
    assert _parse_allowed_users("abc,5,") == (5,)  # 非数字忽略


def test_from_global_settings():
    s = TianshuSettings(
        telegram_bot_token="tok",
        telegram_allowed_users="10,20",
        telegram_connection_mode="webhook",
        telegram_webhook_secret="sec",
    )
    tg = from_global_settings(s)
    assert isinstance(tg, TelegramSettings)
    assert tg.bot_token == "tok"
    assert tg.allowed_users == (10, 20)
    assert tg.connection_mode == "webhook"
    assert tg.enabled
