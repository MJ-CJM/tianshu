"""ModeRouter 单元测试：状态机判定 + 分发。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.dispatcher import FeishuMessage
from tianshu.gateway.feishu.mode_router import ModeContext, ModeRouter


def _make_msg(chat_id: str = "oc_x", sender: str = "ou_a", text: str = "hi") -> FeishuMessage:
    return FeishuMessage(
        event_id="e",
        chat_id=chat_id,
        chat_type="p2p",
        sender_open_id=sender,
        text=text,
        raw={},
    )


def test_resolve_assistant_mode_when_no_anchor():
    anchor = MagicMock()
    anchor.get.return_value = None
    router = ModeRouter(
        anchor=anchor,
        assistant_branch=MagicMock(),
        edict_branch=MagicMock(),
    )
    ctx = router.resolve_mode("oc_x")
    assert ctx.mode == "assistant"
    assert ctx.edict_id is None


def test_resolve_edict_mode_when_anchored():
    anchor = MagicMock()
    anchor.get.return_value = "ed_abc"
    router = ModeRouter(
        anchor=anchor,
        assistant_branch=MagicMock(),
        edict_branch=MagicMock(),
    )
    ctx = router.resolve_mode("oc_x")
    assert ctx.mode == "edict"
    assert ctx.edict_id == "ed_abc"


@pytest.mark.asyncio
async def test_dispatch_to_assistant_when_no_anchor():
    anchor = MagicMock()
    anchor.get.return_value = None
    assistant = MagicMock()
    assistant.handle = AsyncMock()
    edict = MagicMock()
    edict.handle = AsyncMock()
    router = ModeRouter(anchor=anchor, assistant_branch=assistant, edict_branch=edict)
    await router.dispatch(_make_msg(text="/menu"))
    assistant.handle.assert_awaited_once()
    edict.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_to_edict_when_anchored():
    anchor = MagicMock()
    anchor.get.return_value = "ed_abc"
    assistant = MagicMock()
    assistant.handle = AsyncMock()
    edict = MagicMock()
    edict.handle = AsyncMock()
    router = ModeRouter(anchor=anchor, assistant_branch=assistant, edict_branch=edict)
    await router.dispatch(_make_msg(text="hi"))
    edict.handle.assert_awaited_once()
    assistant.handle.assert_not_awaited()
    ctx_arg = edict.handle.await_args.args[1]
    assert isinstance(ctx_arg, ModeContext)
    assert ctx_arg.edict_id == "ed_abc"
