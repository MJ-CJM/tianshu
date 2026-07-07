"""CallbackDispatcher：ea: → 审批；cmd: → 合成命令；始终 answer。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tianshu.gateway.telegram.card_action_dispatcher import CallbackDispatcher
from tianshu.gateway.telegram.dispatcher import TelegramCallback


def _cb(data: str) -> TelegramCallback:
    return TelegramCallback(
        update_id="u",
        callback_id="q",
        chat_id="c1",
        sender_id="7",
        message_id="9",
        data=data,
    )


def _mk(monkeypatch=None):
    mode_router = AsyncMock()
    approval_kb = AsyncMock()
    approval_kb.handle_callback = AsyncMock(return_value="✅ 已批准")
    outbound = AsyncMock()
    disp = CallbackDispatcher(
        mode_router=mode_router,
        approval_kb=approval_kb,
        outbound=outbound,
    )
    return disp, mode_router, approval_kb, outbound


@pytest.mark.asyncio
async def test_ea_routes_to_approval_and_answers():
    disp, mode_router, approval_kb, outbound = _mk()
    await disp.handle(_cb("ea:approve:once:MID12345"))
    approval_kb.handle_callback.assert_awaited_once()
    outbound.answer_callback.assert_awaited_once()
    # popup 文本透传
    args = outbound.answer_callback.await_args
    assert args.args[1] == "✅ 已批准"
    mode_router.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_list_synthesizes_command():
    disp, mode_router, _, outbound = _mk()
    await disp.handle(_cb("cmd:list"))
    mode_router.dispatch.assert_awaited_once()
    synthetic = mode_router.dispatch.await_args.args[0]
    assert synthetic.text == "/list"
    assert synthetic.chat_id == "c1"
    outbound.answer_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_select_with_id():
    disp, mode_router, _, _ = _mk()
    await disp.handle(_cb("cmd:select:abc12345"))
    synthetic = mode_router.dispatch.await_args.args[0]
    assert synthetic.text == "/select abc12345"


@pytest.mark.asyncio
async def test_unknown_data_still_answers():
    disp, mode_router, approval_kb, outbound = _mk()
    await disp.handle(_cb("garbage"))
    mode_router.dispatch.assert_not_called()
    approval_kb.handle_callback.assert_not_called()
    outbound.answer_callback.assert_awaited_once()
