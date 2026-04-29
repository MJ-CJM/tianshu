"""AssistantBranch 单元测试：助手模式命令路由。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.assistant_branch import AssistantBranch
from tianshu.gateway.feishu.dispatcher import FeishuMessage
from tianshu.gateway.feishu.mode_router import ModeContext


def _msg(text: str = "hi", chat: str = "oc_x", sender: str = "ou_a") -> FeishuMessage:
    return FeishuMessage(
        event_id="e",
        chat_id=chat,
        chat_type="p2p",
        sender_open_id=sender,
        text=text,
        raw={},
    )


def _ctx(chat: str = "oc_x") -> ModeContext:
    return ModeContext(mode="assistant", chat_id=chat, sender_open_id="ou_a", edict_id=None)


def _renderer():
    r = MagicMock()
    r.assistant_tag.return_value = "💼 助手"
    r.assistant_silent_reply.return_value = "💼 待命中"
    r.help_assistant.return_value = "help"
    r.unknown_command_reply.side_effect = lambda tag, cmd: f"{tag} 未识 {cmd}"
    r.edict_created_reply.side_effect = lambda eid, t: f"✅ 新敕令 #{eid[:8]}"
    r.edict_selected_reply.side_effect = lambda eid, t: f"📋 已切换 #{eid[:8]}"
    r.edict_cancel_reply.side_effect = lambda eid: f"📋 已取消 #{eid[:8]}"
    r.llm_intent_hint.side_effect = lambda i: f"💡 我理解你想：{i}"
    return r


@pytest.fixture
def branch():
    storage = MagicMock()
    anchor = MagicMock()
    bridge = MagicMock()
    bridge.create_new = AsyncMock(return_value="ed_new1234")
    outbound = MagicMock()
    outbound.send_text = AsyncMock()
    outbound.send_card = AsyncMock()
    cb = MagicMock()
    cb.build_menu_card.return_value = {"header": {}}
    cb.build_list_card.return_value = {"header": {}}
    cb.build_budget_card = AsyncMock(return_value={"header": {}})
    b = AssistantBranch(
        storage=storage,
        anchor=anchor,
        edict_bridge=bridge,
        outbound=outbound,
        renderer=_renderer(),
        card_builder=cb,
        intent_parser=None,
    )
    return b, storage, anchor, outbound, cb, bridge


@pytest.mark.asyncio
async def test_help_command(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/help"), _ctx())
    outbound.send_text.assert_awaited()


@pytest.mark.asyncio
async def test_new_command_creates_edict(branch):
    b, _, _, outbound, _, bridge = branch
    await b.handle(_msg("/new 写代码"), _ctx())
    bridge.create_new.assert_awaited_once()
    assert "ed_new12" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_new_without_goal_shows_usage(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/new"), _ctx())
    assert "用法" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_menu_command_sends_card(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/menu"), _ctx())
    outbound.send_card.assert_awaited()


@pytest.mark.asyncio
async def test_list_command_empty(branch):
    b, storage, _, outbound, _, _ = branch
    storage.list_edicts.return_value = ([], 0)
    await b.handle(_msg("/list"), _ctx())
    assert "暂无敕令" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_list_command_with_data_sends_card(branch):
    b, storage, _, outbound, cb, _ = branch
    e = MagicMock()
    e.id = "ed_abcdefg"
    e.title = "x"
    e.status = "open"
    storage.list_edicts.return_value = ([e], 1)
    await b.handle(_msg("/list"), _ctx())
    outbound.send_card.assert_awaited()


@pytest.mark.asyncio
async def test_select_short_id_rejected(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/select abc"), _ctx())
    assert "至少 6" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_select_no_target_shows_usage(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/select"), _ctx())
    assert "用法" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_select_unique_match_sets_anchor(branch):
    b, storage, anchor, outbound, _, _ = branch
    e = MagicMock()
    e.id = "ed_abcdef1234"
    e.title = "目标"
    e.status = "open"
    storage.list_edicts.return_value = ([e], 1)
    await b.handle(_msg("/select ed_abc"), _ctx())
    anchor.set.assert_called_once_with("oc_x", "ed_abcdef1234")
    assert "已切换" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_select_no_match(branch):
    b, storage, _, outbound, _, _ = branch
    storage.list_edicts.return_value = ([], 0)
    await b.handle(_msg("/select ed_xxxxxx"), _ctx())
    assert "未找到" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_select_multiple_matches(branch):
    b, storage, _, outbound, _, _ = branch
    e1 = MagicMock()
    e1.id = "ed_abcdef1"
    e2 = MagicMock()
    e2.id = "ed_abcdef2"
    storage.list_edicts.return_value = ([e1, e2], 2)
    await b.handle(_msg("/select ed_abc"), _ctx())
    assert "多个匹配" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_budget_command_sends_card(branch):
    b, _, _, outbound, cb, _ = branch
    await b.handle(_msg("/budget"), _ctx())
    cb.build_budget_card.assert_awaited()
    outbound.send_card.assert_awaited()


@pytest.mark.asyncio
async def test_status_without_target_shows_hint(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/status"), _ctx())
    assert "需要指定" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_cancel_without_target_shows_hint(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/cancel"), _ctx())
    assert "需要指定" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_unknown_slash_command(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("/foo"), _ctx())
    assert "未识" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_natural_language_no_intent_parser_silent(branch):
    b, _, _, outbound, _, _ = branch
    await b.handle(_msg("你好"), _ctx())
    assert "待命" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_natural_language_with_intent_parser_unknown_falls_silent():
    """IntentParser 返回 unknown 时也走 silent。"""
    parser = MagicMock()
    parser.parse = AsyncMock(return_value={"intent": "unknown", "args": {}})
    storage = MagicMock()
    anchor = MagicMock()
    bridge = MagicMock()
    bridge.create_new = AsyncMock()
    outbound = MagicMock()
    outbound.send_text = AsyncMock()
    outbound.send_card = AsyncMock()
    cb = MagicMock()
    cb.build_menu_card.return_value = {}
    b = AssistantBranch(
        storage=storage,
        anchor=anchor,
        edict_bridge=bridge,
        outbound=outbound,
        renderer=_renderer(),
        card_builder=cb,
        intent_parser=parser,
    )
    await b.handle(_msg("呃"), _ctx())
    parser.parse.assert_awaited()
    assert "待命" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_natural_language_intent_synthesizes_command():
    """IntentParser 返回 list intent 时合成 /list。"""
    parser = MagicMock()
    parser.parse = AsyncMock(return_value={"intent": "list", "args": {"filter": "open"}})
    storage = MagicMock()
    storage.list_edicts.return_value = ([], 0)
    anchor = MagicMock()
    bridge = MagicMock()
    outbound = MagicMock()
    outbound.send_text = AsyncMock()
    outbound.send_card = AsyncMock()
    cb = MagicMock()
    b = AssistantBranch(
        storage=storage,
        anchor=anchor,
        edict_bridge=bridge,
        outbound=outbound,
        renderer=_renderer(),
        card_builder=cb,
        intent_parser=parser,
    )
    await b.handle(_msg("显示我的列表"), _ctx())
    # /list 解析得到空列表 → "暂无敕令"
    sent_texts = [c.args[1] for c in outbound.send_text.await_args_list]
    assert any("理解你想" in t for t in sent_texts)
    assert any("暂无敕令" in t for t in sent_texts)


@pytest.mark.asyncio
async def test_status_with_id_finds_edict(branch):
    b, storage, _, outbound, _, _ = branch
    e = MagicMock()
    e.id = "ed_abcdef1"
    e.title = "目标"
    e.status = "open"
    storage.list_edicts.return_value = ([e], 1)
    await b.handle(_msg("/status ed_abcdef"), _ctx())
    assert "ed_abcde" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_status_with_id_not_found(branch):
    b, storage, _, outbound, _, _ = branch
    storage.list_edicts.return_value = ([], 0)
    await b.handle(_msg("/status ed_nono1"), _ctx())
    assert "未找到" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_cancel_with_id_open_status(branch):
    b, storage, _, outbound, _, _ = branch
    e = MagicMock()
    e.id = "ed_abcdef1"
    e.title = "目标"
    e.status = "open"
    storage.list_edicts.return_value = ([e], 1)
    await b.handle(_msg("/cancel ed_abcdef"), _ctx())
    storage.update_edict_status.assert_called_once_with("ed_abcdef1", "cancelled")


@pytest.mark.asyncio
async def test_cancel_with_id_already_completed(branch):
    b, storage, _, outbound, _, _ = branch
    e = MagicMock()
    e.id = "ed_abcdef1"
    e.title = "目标"
    e.status = "completed"
    storage.list_edicts.return_value = ([e], 1)
    await b.handle(_msg("/cancel ed_abcdef"), _ctx())
    storage.update_edict_status.assert_not_called()
    assert "无需取消" in outbound.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_cancel_with_id_not_found(branch):
    b, storage, _, outbound, _, _ = branch
    storage.list_edicts.return_value = ([], 0)
    await b.handle(_msg("/cancel ed_nono1"), _ctx())
    assert "未找到" in outbound.send_text.await_args.args[1]


def test_synthesize_command_all_intents():
    """覆盖 _synthesize_command 各分支。"""
    syn = AssistantBranch._synthesize_command
    assert syn("list", {}) == "/list open"
    assert syn("list", {"filter": "all"}) == "/list all"
    assert syn("new", {"goal": "x"}) == "/new x"
    assert syn("new", {}) is None
    assert syn("status", {"target": "ed_x"}) == "/status ed_x"
    assert syn("status", {"edict_id": "ed_y"}) == "/status ed_y"
    assert syn("cancel", {"target": "ed_z"}) == "/cancel ed_z"
    assert syn("budget", {}) == "/budget"
    assert syn("help", {}) == "/help"
    assert syn("unknown", {}) is None


def test_parse_filter_all_branches():
    """覆盖 _parse_filter 全部分支。"""
    from tianshu.models.common import EdictStatus
    pf = AssistantBranch._parse_filter
    assert pf("open") == EdictStatus.OPEN
    assert pf("active") == EdictStatus.OPEN
    assert pf("") == EdictStatus.OPEN
    assert pf("completed") == EdictStatus.COMPLETED
    assert pf("cancelled") == EdictStatus.CANCELLED
    assert pf("all") is None
    assert pf("garbage") == EdictStatus.OPEN


@pytest.mark.asyncio
async def test_natural_language_synthesize_returns_none_falls_silent():
    """intent=new but goal 为空 → synthesize 返回 None → silent。"""
    parser = MagicMock()
    parser.parse = AsyncMock(return_value={"intent": "new", "args": {}})
    storage = MagicMock()
    anchor = MagicMock()
    bridge = MagicMock()
    outbound = MagicMock()
    outbound.send_text = AsyncMock()
    outbound.send_card = AsyncMock()
    cb = MagicMock()
    b = AssistantBranch(
        storage=storage,
        anchor=anchor,
        edict_bridge=bridge,
        outbound=outbound,
        renderer=_renderer(),
        card_builder=cb,
        intent_parser=parser,
    )
    await b.handle(_msg("新建"), _ctx())
    sent_texts = [c.args[1] for c in outbound.send_text.await_args_list]
    assert any("待命" in t for t in sent_texts)


def test_set_renderer_replaces_renderer():
    """Reload 时切换 renderer 不应丢其他依赖。"""
    b = AssistantBranch(
        storage=MagicMock(),
        anchor=MagicMock(),
        edict_bridge=MagicMock(),
        outbound=MagicMock(),
        renderer=_renderer(),
        card_builder=MagicMock(),
        intent_parser=None,
    )
    new_r = _renderer()
    b.set_renderer(new_r)
    assert b._renderer is new_r
