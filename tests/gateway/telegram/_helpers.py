"""telegram 测试公共构造器。"""
from __future__ import annotations

from tianshu.gateway.telegram.settings import TelegramSettings


def make_settings(**kw) -> TelegramSettings:
    base = dict(
        bot_token="123:ABC",
        connection_mode="polling",
        allowed_users=(),
        home_channel="",
        webhook_path="/telegram/webhook",
        webhook_secret="",
        poll_timeout=30,
        text_batch_delay=0.01,
        dedup_cache_size=2048,
        assistant_persona_id="tongzheng",
        disable_assistant_mode=False,
        enable_edict_submission=False,
    )
    base.update(kw)
    return TelegramSettings(**base)
