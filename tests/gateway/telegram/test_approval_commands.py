"""TelegramApprovalCommandHandler：pending 反查 telegram_pending_buttons + 提交决策。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.gateway.telegram.approval_commands import (
    TelegramApprovalCommandHandler,
    parse_approval_command,
)


def _handler(storage):
    approval = MagicMock()

    async def resolve(_decision_id, *, action, **_kwargs):
        record = MagicMock()
        record.resolution.action = action
        record.resolution.payload = {"grant_scope": "once"}
        return record

    approval.resolve_tool_decision = AsyncMock(side_effect=resolve)
    return TelegramApprovalCommandHandler(storage=storage, approval_manager=approval), approval


@pytest.mark.asyncio
async def test_no_pending(storage):
    h, approval = _handler(storage)
    cmd = parse_approval_command("/准")
    reply = await h.handle(chat_id="c1", sender_open_id="7", command=cmd)
    assert "无待审批" in reply
    approval.resolve_tool_decision.assert_not_called()


@pytest.mark.asyncio
async def test_single_pending_approve(storage):
    h, approval = _handler(storage)
    storage.save_telegram_pending_button(
        approval_id="MEMORIAL01",
        chat_id="c1",
        message_id="9",
        kind="tool.approval_required",
    )
    cmd = parse_approval_command("/准")
    reply = await h.handle(chat_id="c1", sender_open_id="7", command=cmd)
    approval.resolve_tool_decision.assert_awaited_once()
    assert approval.resolve_tool_decision.await_args.args == ("MEMORIAL01",)
    kwargs = approval.resolve_tool_decision.await_args.kwargs
    assert kwargs["action"] == "approve"
    assert kwargs["auth"].principal.id == "telegram:7"
    assert "已批准" in reply


@pytest.mark.asyncio
async def test_multi_pending_requires_prefix(storage):
    h, approval = _handler(storage)
    storage.save_telegram_pending_button(
        approval_id="AAAAAA01", chat_id="c1", message_id="1", kind="tool.approval_required"
    )
    storage.save_telegram_pending_button(
        approval_id="BBBBBB02", chat_id="c1", message_id="2", kind="tool.approval_required"
    )
    cmd = parse_approval_command("/准")
    reply = await h.handle(chat_id="c1", sender_open_id="7", command=cmd)
    assert "待审批" in reply  # 提示指定短 ID
    approval.resolve_tool_decision.assert_not_called()


@pytest.mark.asyncio
async def test_reject(storage):
    h, approval = _handler(storage)
    storage.save_telegram_pending_button(
        approval_id="MEMORIAL99", chat_id="c1", message_id="3", kind="tool.approval_required"
    )
    cmd = parse_approval_command("/驳")
    reply = await h.handle(chat_id="c1", sender_open_id="7", command=cmd)
    assert kwargs_action(approval) == "reject"
    assert "已拒绝" in reply


def kwargs_action(approval):
    return approval.resolve_tool_decision.await_args.kwargs["action"]
