"""TelegramApprovalCommandHandler：pending 反查 telegram_pending_buttons + 提交决策。"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.gateway.telegram.approval_commands import (
    TelegramApprovalCommandHandler,
    parse_approval_command,
)
from tianshu.models.principal import AuthenticationSource, ClientKind, PrincipalKind


def _handler(storage, instance_id: str = "telegram-default"):
    approval = MagicMock()

    async def resolve(_decision_id, *, action, **_kwargs):
        record = MagicMock()
        record.resolution.action = action
        record.resolution.payload = {"grant_scope": "once"}
        return record

    approval.resolve_tool_decision = AsyncMock(side_effect=resolve)
    return (
        TelegramApprovalCommandHandler(
            storage=storage,
            approval_manager=approval,
            instance_id=instance_id,
        ),
        approval,
    )


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
    assert kwargs["auth"].principal.id == "telegram:telegram-default:7"
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


@pytest.mark.asyncio
async def test_configured_telegram_instance_namespaces_webhook_identity():
    storage = MagicMock()
    storage.list_telegram_pending_for_chat.return_value = ["01DECISIONAAAA11111111111111"]
    first, first_approval = _handler(storage, "telegram-primary")
    second, second_approval = _handler(storage, "telegram-secondary")
    command = parse_approval_command("/准")

    await first.handle(chat_id="shared-chat", sender_open_id="7", command=command)
    await second.handle(chat_id="shared-chat", sender_open_id="7", command=command)

    first_auth = first_approval.resolve_tool_decision.await_args.kwargs["auth"]
    second_auth = second_approval.resolve_tool_decision.await_args.kwargs["auth"]
    assert first_auth.principal.id == "telegram:telegram-primary:7"
    assert second_auth.principal.id == "telegram:telegram-secondary:7"
    assert first_auth.principal.id != second_auth.principal.id
    assert first_auth.principal.kind is PrincipalKind.WEBHOOK
    assert first_auth.principal.scopes == frozenset({"decision:resolve"})
    assert first_auth.source is AuthenticationSource.WEBHOOK
    assert first_auth.client_kind is ClientKind.WEBHOOK
    expected_digest = hashlib.sha256(
        "\0".join(
            (
                "telegram",
                "telegram-primary",
                "shared-chat",
                "7",
                "01DECISIONAAAA11111111111111",
            )
        ).encode()
    ).hexdigest()[:32]
    assert first_auth.correlation_id == f"approval-command:{expected_digest}"
    assert first_auth.correlation_id != second_auth.correlation_id
