"""审批双语命令解析与路由单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.gateway.feishu.approval_commands import (
    ApprovalCommand,
    ApprovalCommandHandler,
    parse_approval_command,
)

# --- parse_approval_command ---


@pytest.mark.parametrize(
    "text,expected",
    [
        # 英文
        ("/approve", ApprovalCommand("approve", "once", None)),
        ("/approve edict", ApprovalCommand("approve", "edict", None)),
        ("/approve always", ApprovalCommand("approve", "always", None)),
        ("/reject", ApprovalCommand("reject", None, None)),
        # 中文
        ("/准", ApprovalCommand("approve", "once", None)),
        ("/准敕", ApprovalCommand("approve", "edict", None)),
        ("/准永", ApprovalCommand("approve", "always", None)),
        ("/驳", ApprovalCommand("reject", None, None)),
        # 加 ID 前缀
        ("/approve abc12345", ApprovalCommand("approve", "once", "abc12345")),
        ("/approve edict abc12345", ApprovalCommand("approve", "edict", "abc12345")),
        ("/approve abc12345 always", ApprovalCommand("approve", "always", "abc12345")),
        ("/reject abc12345", ApprovalCommand("reject", None, "abc12345")),
        ("/准 abc12345", ApprovalCommand("approve", "once", "abc12345")),
        ("/准敕 abc12345", ApprovalCommand("approve", "edict", "abc12345")),
        ("/驳 abc12345", ApprovalCommand("reject", None, "abc12345")),
    ],
)
def test_parse_known_commands(text, expected):
    assert parse_approval_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "/list",
        "/new abc",
        "/approveX",  # 不是 /approve
        "/准乱",  # 中文 scope 后缀不识别
    ],
)
def test_parse_unknown_returns_none(text):
    assert parse_approval_command(text) is None


def test_parse_id_prefix_too_short_ignored():
    """ID 前缀至少 6 字符，否则不当作 prefix（避免误识 always/edict）。"""
    cmd = parse_approval_command("/approve ab")
    assert cmd == ApprovalCommand("approve", "once", None)


# --- ApprovalCommandHandler ---


def _decree_returning(scope: str | None):
    """构造 mock Decree，grant_scope = 实际生效值（可能被 always→once 降级）。"""
    d = MagicMock()
    d.grant_scope = scope
    return d


@pytest.fixture
def handler_setup():
    storage = MagicMock()
    # 模拟 storage._conn.execute(...).fetchall()
    storage._conn = MagicMock()
    approval = MagicMock()

    # 默认：实际 scope 等于 input scope（无降级）
    async def _default_decision(*, memorial_id, action, grant_scope=None, **_kw):
        return _decree_returning(grant_scope)

    approval.submit_tool_decision = AsyncMock(side_effect=_default_decision)
    h = ApprovalCommandHandler(storage=storage, approval_manager=approval)
    return h, storage, approval


def _set_pending(storage: MagicMock, memorial_ids: list[str]) -> None:
    """让 _list_pending_for_chat 返回指定 memorial_id 列表。"""
    storage._conn.execute.return_value.fetchall.return_value = [(mid,) for mid in memorial_ids]


@pytest.mark.asyncio
async def test_handle_no_pending_returns_hint(handler_setup):
    h, storage, _ = handler_setup
    _set_pending(storage, [])
    cmd = ApprovalCommand("approve", "once", None)
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    assert "无待审批" in reply


@pytest.mark.asyncio
async def test_handle_single_pending_default_approve(handler_setup):
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_a1234567"])
    cmd = ApprovalCommand("approve", "once", None)
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    approval.submit_tool_decision.assert_awaited_once_with(
        memorial_id="mem_a1234567",
        action="approve",
        grant_scope="once",
        actor="feishu:ou_a",
    )
    assert "已批准" in reply
    assert "单次" in reply


@pytest.mark.asyncio
async def test_handle_single_pending_default_reject(handler_setup):
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_b"])
    cmd = ApprovalCommand("reject", None, None)
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    approval.submit_tool_decision.assert_awaited_once_with(
        memorial_id="mem_b",
        action="reject",
        grant_scope=None,
        actor="feishu:ou_a",
    )
    assert "拒绝" in reply


@pytest.mark.asyncio
async def test_handle_multi_pending_without_id_lists_them(handler_setup):
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_x12345", "mem_y67890"])
    cmd = ApprovalCommand("approve", "once", None)
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    approval.submit_tool_decision.assert_not_awaited()
    assert "2 个待审批" in reply
    assert "/approve" in reply
    assert "/准" in reply


@pytest.mark.asyncio
async def test_handle_multi_pending_with_prefix_targets_one(handler_setup):
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_x12345abc", "mem_y67890def"])
    cmd = ApprovalCommand("approve", "edict", "mem_x12")
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    approval.submit_tool_decision.assert_awaited_once_with(
        memorial_id="mem_x12345abc",
        action="approve",
        grant_scope="edict",
        actor="feishu:ou_a",
    )
    assert "已批准" in reply
    assert "本敕令" in reply


@pytest.mark.asyncio
async def test_handle_prefix_no_match(handler_setup):
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_x12345"])
    cmd = ApprovalCommand("approve", "once", "nomatch")
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    approval.submit_tool_decision.assert_not_awaited()
    assert "未找到" in reply


@pytest.mark.asyncio
async def test_handle_prefix_ambiguous(handler_setup):
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_x12345", "mem_x12999"])
    cmd = ApprovalCommand("approve", "once", "mem_x12")
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    approval.submit_tool_decision.assert_not_awaited()
    assert "匹配多个" in reply


@pytest.mark.asyncio
async def test_handle_idempotent_when_already_resolved(handler_setup):
    """submit_tool_decision 抛 ValueError → 友好提示已被其他通道响应。"""
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_z"])
    approval.submit_tool_decision = AsyncMock(side_effect=ValueError("not pending"))
    cmd = ApprovalCommand("approve", "once", None)
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    assert "其他通道" in reply


@pytest.mark.asyncio
async def test_handle_always_downgraded_to_once_shows_real_scope(handler_setup):
    """shell_exec 等 bash 类工具 always→once 降级时，回复必须显示真实 scope + 降级提示。

    不能再像旧版一样无脑显示"总是" —— 那会让用户以为永久放行了，
    下次同一审批又冒出来时还以为系统出 bug，反复 /approve always 死循环。
    """
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_w12345678"])

    # 模拟后端把 always 降级为 once（policy_store.assert_can_grant 拒绝）
    async def _downgrade(*, memorial_id, action, grant_scope=None, **_kw):
        return _decree_returning("once")  # 实际生效 once

    approval.submit_tool_decision = AsyncMock(side_effect=_downgrade)

    cmd = ApprovalCommand("approve", "always", None)  # 用户请求 always
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)

    assert "已批准" in reply
    assert "单次" in reply  # 真实生效是单次
    assert "总是" not in reply.split("（")[0]  # 不该把"总是"作为主标签
    assert "降级" in reply  # 显式提示用户被降级了
    assert "shell_exec" in reply or "高危" in reply  # 解释为什么


@pytest.mark.asyncio
async def test_handle_no_downgrade_no_extra_hint(handler_setup):
    """正常 once/edict 批准不应带降级提示，避免噪音。"""
    h, storage, approval = handler_setup
    _set_pending(storage, ["mem_n"])
    cmd = ApprovalCommand("approve", "edict", None)
    reply = await h.handle(chat_id="oc_x", sender_open_id="ou_a", command=cmd)
    assert "本敕令" in reply
    assert "降级" not in reply
