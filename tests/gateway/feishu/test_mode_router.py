"""ModeRouter 单元测试：状态机判定 + 分发（v2 极简模型）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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


def _make_router(
    *,
    anchor_value: str | None = None,
    chat_edict: bool = False,
    biz_edict: bool = False,
):
    """构造 ModeRouter，可控制 anchor 与 storage.get_edict 行为。"""
    anchor = MagicMock()
    anchor.get.return_value = anchor_value
    storage = MagicMock()
    if chat_edict:
        e = MagicMock()
        e.metadata = {"assistant_chat": True}
        storage.get_edict.return_value = e
    elif biz_edict:
        e = MagicMock()
        e.metadata = {}
        storage.get_edict.return_value = e
    else:
        storage.get_edict.return_value = None
    bridge = MagicMock()
    bridge.ensure_chat_edict = AsyncMock(return_value="ed_chat_new")
    assistant = MagicMock()
    assistant.handle = AsyncMock()
    edict_branch = MagicMock()
    edict_branch.handle = AsyncMock()
    settings = MagicMock()
    settings.assistant_persona_id = "tongzheng"
    router = ModeRouter(
        anchor=anchor,
        assistant_branch=assistant,
        edict_branch=edict_branch,
        edict_bridge=bridge,
        storage=storage,
        settings=settings,
    )
    return router, anchor, storage, bridge, assistant, edict_branch


def test_resolve_assistant_mode_when_no_anchor():
    router, *_ = _make_router(anchor_value=None)
    ctx = router.resolve_mode("oc_x")
    assert ctx.mode == "assistant"
    assert ctx.edict_id is None


def test_resolve_mode_chat_edict_returns_assistant():
    """v2: anchor 指向 metadata.assistant_chat=true 敕令 → assistant 模式。"""
    router, *_ = _make_router(anchor_value="ed_chat", chat_edict=True)
    ctx = router.resolve_mode("oc_x")
    assert ctx.mode == "assistant"
    assert ctx.edict_id == "ed_chat"


def test_resolve_mode_business_edict_returns_edict():
    """v2: anchor 指向不带 assistant_chat 标记的敕令 → edict 模式。"""
    router, *_ = _make_router(anchor_value="ed_biz", biz_edict=True)
    ctx = router.resolve_mode("oc_x")
    assert ctx.mode == "edict"
    assert ctx.edict_id == "ed_biz"


@pytest.mark.asyncio
async def test_dispatch_to_assistant_when_chat_anchor():
    """v2: anchor 指向 chat 敕令 → 调 ensure_chat_edict + 走 assistant 分支。"""
    router, _, _, bridge, assistant, edict_branch = _make_router(
        anchor_value="ed_chat",
        chat_edict=True,
    )
    await router.dispatch(_make_msg(text="/menu"))
    bridge.ensure_chat_edict.assert_awaited_once()
    assistant.handle.assert_awaited_once()
    edict_branch.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_to_edict_when_business_anchor():
    """v2: anchor 指向业务敕令 → 走 edict 分支。"""
    router, _, _, bridge, assistant, edict_branch = _make_router(
        anchor_value="ed_biz",
        biz_edict=True,
    )
    await router.dispatch(_make_msg(text="hi"))
    bridge.ensure_chat_edict.assert_awaited_once()
    edict_branch.handle.assert_awaited_once()
    assistant.handle.assert_not_awaited()
    ctx_arg = edict_branch.handle.await_args.args[1]
    assert isinstance(ctx_arg, ModeContext)
    assert ctx_arg.edict_id == "ed_biz"


@pytest.mark.asyncio
async def test_dispatch_calls_ensure_chat_edict_when_no_anchor():
    """v2: 首次接入（无 anchor）应调 ensure_chat_edict。"""
    router, _, _, bridge, _, _ = _make_router(anchor_value=None)
    await router.dispatch(_make_msg(text="hello"))
    bridge.ensure_chat_edict.assert_awaited_once()
