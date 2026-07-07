"""Tests for EVAL_MODE side-effect containment.

When TIANSHU_EVAL_MODE=1, the app must NOT register real outbound notification
channels (Feishu webhook / DingTalk / SMTP) and must NOT start channel bots
(Feishu app-bot / Telegram).

Assertion strategy
------------------
After entering the lifespan, we inspect ``app.state.channel_registry.list_channels()``.
In eval mode that list must be empty — even when the relevant env vars (feishu_webhook,
dingtalk_webhook, smtp_host) are present — because the registration block is gated
behind ``if not settings.eval_mode``.

For bot startup (``bot_manager.start_all``), we patch it with an AsyncMock and assert
it was never called when eval_mode is active.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tianshu.app import create_app, lifespan


@pytest.mark.asyncio
async def test_eval_mode_no_outbound_channels_registered(monkeypatch):
    """ChannelRegistry is empty in eval mode, even with channel env vars set."""
    # Activate eval mode
    monkeypatch.setenv("TIANSHU_EVAL_MODE", "1")
    # Provide channel credentials that would normally cause registration
    monkeypatch.setenv("TIANSHU_FEISHU_WEBHOOK", "https://open.feishu.cn/fake-webhook")
    monkeypatch.setenv("TIANSHU_DINGTALK_WEBHOOK", "https://oapi.dingtalk.com/fake")
    monkeypatch.setenv("TIANSHU_SMTP_HOST", "smtp.example.com")

    app = create_app()
    async with lifespan(app):
        channels = app.state.channel_registry.list_channels()
        assert channels == [], f"Expected no outbound channels in eval mode, got: {channels}"


@pytest.mark.asyncio
async def test_normal_mode_registers_channels(monkeypatch):
    """Sanity check: channels ARE registered in normal mode when env vars present."""
    # Ensure eval mode is off
    monkeypatch.delenv("TIANSHU_EVAL_MODE", raising=False)
    monkeypatch.setenv("TIANSHU_FEISHU_WEBHOOK", "https://open.feishu.cn/fake-webhook")

    app = create_app()
    async with lifespan(app):
        channels = app.state.channel_registry.list_channels()
        assert "feishu" in channels, f"Expected feishu channel in normal mode, got: {channels}"


@pytest.mark.asyncio
async def test_eval_mode_bots_not_started(monkeypatch):
    """bot_manager.start_all is never called when eval_mode is True."""
    monkeypatch.setenv("TIANSHU_EVAL_MODE", "1")

    app = create_app()

    # Patch start_all on the ChannelBotManager class so we can track calls
    with patch(
        "tianshu.gateway.bot_manager.ChannelBotManager.start_all",
        new_callable=AsyncMock,
    ) as mock_start_all:
        async with lifespan(app):
            pass  # lifespan entered and exited cleanly

    mock_start_all.assert_not_called()


@pytest.mark.asyncio
async def test_eval_mode_app_still_functional(monkeypatch):
    """Core app state (storage, executor, agent) is present in eval mode."""
    monkeypatch.setenv("TIANSHU_EVAL_MODE", "1")

    app = create_app()
    async with lifespan(app):
        # These must all exist — eval mode only suppresses external I/O
        assert app.state.storage is not None
        assert app.state.executor is not None
        assert app.state.agent is not None
        assert app.state.notifier is not None
        assert app.state.channel_registry is not None
