"""EdictBranch 单元测试：敕令模式命令 + /exit + 续接。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.dispatcher import FeishuMessage
from tianshu.gateway.feishu.edict_branch import EdictBranch
from tianshu.gateway.feishu.edict_bridge import EdictBusyError
from tianshu.gateway.feishu.mode_router import ModeContext


def _msg(text: str = "hi", chat: str = "oc_x") -> FeishuMessage:
    return FeishuMessage(
        event_id="e",
        chat_id=chat,
        chat_type="p2p",
        sender_open_id="ou_a",
        text=text,
        raw={},
    )


def _ctx(eid: str = "ed_anchor1") -> ModeContext:
    return ModeContext(mode="edict", chat_id="oc_x", sender_open_id="ou_a", edict_id=eid)


def _renderer():
    r = MagicMock()
    r.edict_tag.side_effect = lambda eid: f"📋 #{eid[:8]}"
    r.edict_exit_reply.return_value = "💼 已退出"
    r.edict_received_reply.side_effect = lambda eid: f"📋 已收到 #{eid[:8]}"
    r.edict_cancel_reply.side_effect = lambda eid: f"📋 已取消 #{eid[:8]}"
    r.help_edict.return_value = "help-edict"
    r.unknown_command_reply.side_effect = lambda tag, cmd: f"{tag} 未识 {cmd}"
    r.edict_created_reply.side_effect = lambda eid, t: f"✅ #{eid[:8]}"
    return r


@pytest.fixture
def branch():
    storage = MagicMock()
    anchor = MagicMock()
    anchor.get.return_value = None
    bridge = MagicMock()
    bridge.create_new = AsyncMock(return_value="ed_new5678")
    bridge.continue_or_create = AsyncMock(return_value="ed_anchor1")
    outbound = MagicMock()
    outbound.send_text = AsyncMock()
    assistant = MagicMock()
    assistant.handle = AsyncMock()
    b = EdictBranch(
        storage=storage,
        anchor=anchor,
        edict_bridge=bridge,
        outbound=outbound,
        renderer=_renderer(),
        assistant_branch=assistant,
    )
    return b, storage, anchor, outbound, assistant, bridge


@pytest.mark.asyncio
async def test_exit_clears_anchor(branch):
    b, storage, _, outbound, _, _ = branch
    await b.handle(_msg("/exit"), _ctx())
    storage.delete_feishu_anchor.assert_called_with("oc_x")
    assert "已退出" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_new_in_edict_mode_exits_then_creates(branch):
    b, storage, _, outbound, _, bridge = branch
    await b.handle(_msg("/new 新目标"), _ctx())
    storage.delete_feishu_anchor.assert_called_with("oc_x")
    bridge.create_new.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_without_goal_shows_usage(branch):
    b, storage, _, outbound, _, bridge = branch
    await b.handle(_msg("/new"), _ctx())
    storage.delete_feishu_anchor.assert_not_called()
    bridge.create_new.assert_not_awaited()
    assert "用法" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_status_default_uses_anchor(branch):
    b, storage, _, outbound, _, _ = branch
    e = MagicMock()
    e.id = "ed_anchor1"
    e.title = "x"
    e.status = "open"
    storage.get_edict.return_value = e
    await b.handle(_msg("/status"), _ctx())
    storage.get_edict.assert_called_with("ed_anchor1")


@pytest.mark.asyncio
async def test_status_unknown_id_replies_not_found(branch):
    b, storage, _, outbound, _, _ = branch
    storage.get_edict.return_value = None
    await b.handle(_msg("/status ed_zzz"), _ctx())
    assert "不存在" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_query_commands_delegate_to_assistant(branch):
    b, _, _, _, assistant, _ = branch
    await b.handle(_msg("/list"), _ctx())
    assistant.handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_delegates_to_assistant(branch):
    b, _, _, _, assistant, _ = branch
    await b.handle(_msg("/budget"), _ctx())
    assistant.handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_menu_delegates_to_assistant(branch):
    b, _, _, _, assistant, _ = branch
    await b.handle(_msg("/menu"), _ctx())
    assistant.handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_text_continues(branch):
    b, _, _, outbound, _, bridge = branch
    await b.handle(_msg("补一句"), _ctx())
    bridge.continue_or_create.assert_awaited_once()
    assert "已收到" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_plain_text_busy_replies_with_error(branch):
    b, _, _, outbound, _, bridge = branch
    bridge.continue_or_create.side_effect = EdictBusyError("敕令处理中")
    await b.handle(_msg("补一句"), _ctx())
    assert "敕令处理中" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_help_in_edict_mode(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/help"), _ctx())
    assert "help-edict" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_unknown_slash_command_in_edict_mode(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/foo"), _ctx())
    assert "未识" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_cancel_default_uses_anchor(branch):
    b, storage, anchor, outbound, _, _ = branch
    e = MagicMock()
    e.id = "ed_anchor1"
    e.title = "x"
    e.status = "open"
    storage.get_edict.return_value = e
    anchor.get.return_value = "ed_anchor1"
    await b.handle(_msg("/cancel"), _ctx())
    storage.update_edict_status.assert_called_once()
    storage.delete_feishu_anchor.assert_called_with("oc_x")


@pytest.mark.asyncio
async def test_cancel_already_completed(branch):
    b, storage, _, outbound, _, _ = branch
    e = MagicMock()
    e.id = "ed_anchor1"
    e.title = "x"
    e.status = "completed"
    storage.get_edict.return_value = e
    await b.handle(_msg("/cancel"), _ctx())
    storage.update_edict_status.assert_not_called()
    assert "无需取消" in outbound.send_text.await_args.args[1]


def test_set_renderer_replaces_renderer():
    b = EdictBranch(
        storage=MagicMock(),
        anchor=MagicMock(),
        edict_bridge=MagicMock(),
        outbound=MagicMock(),
        renderer=_renderer(),
        assistant_branch=MagicMock(),
    )
    new_r = _renderer()
    b.set_renderer(new_r)
    assert b._renderer is new_r
