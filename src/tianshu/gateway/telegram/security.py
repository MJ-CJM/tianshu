"""Telegram 入站安全：allowlist + webhook secret 校验。"""
from __future__ import annotations

import hmac


def is_allowed_user(user_id: int, allowed: tuple[int, ...]) -> bool:
    """allowlist 校验。空 allowed = 放行任意人（与飞书行为一致，启动时打 warning）。"""
    if not allowed:
        return True
    return user_id in allowed


def verify_webhook_secret(header_value: str | None, expected: str) -> bool:
    """webhook 模式校验 X-Telegram-Bot-Api-Secret-Token（常量时间比较）。"""
    if not expected:
        return False
    if not header_value:
        return False
    return hmac.compare_digest(header_value, expected)
