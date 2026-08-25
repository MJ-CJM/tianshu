"""ChannelRegistry unregister lifecycle coverage."""

from __future__ import annotations

import pytest

from tianshu.notifier.channel_registry import ChannelRegistry
from tianshu.notifier.channels.base import NotificationChannel


class _RecordingChannel(NotificationChannel):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "recording"

    async def send(self, message: dict, rendered: str) -> bool:
        self.calls += 1
        return True


@pytest.mark.asyncio
async def test_unregister_removes_channel_and_rate_limit_state() -> None:
    registry = ChannelRegistry()
    channel = _RecordingChannel()
    registry.register(channel, rpm=1)
    assert await registry.send_all({}, "first") == {"recording": True}
    assert registry._send_log["recording"]

    assert registry.unregister("recording") is True

    assert registry.get("recording") is None
    assert "recording" not in registry._rate_limits
    assert "recording" not in registry._send_log
    assert await registry.send_all({}, "second") == {}
    assert channel.calls == 1
    assert registry.unregister("recording") is False
