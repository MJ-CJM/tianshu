"""Telegram tool-decision keyboard binds durable decision IDs and verified actors."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.telegram.approval_kb import ApprovalKeyboardHandler, build_approval_message
from tianshu.gateway.telegram.dispatcher import TelegramCallback
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope
from tianshu.models.principal import AuthenticationSource, ClientKind, PrincipalKind

from ._helpers import make_settings

_DECISION_1 = "01J00000000000000000000000"
_DECISION_2 = "01J00000000000000000000001"


def _record(*, action: str = "approve", actor: str = "telegram:tg-a:7", scope: str = "once"):
    return SimpleNamespace(
        resolution=SimpleNamespace(
            action=action,
            actor_principal_id=actor,
            payload={"grant_scope": scope},
        )
    )


@pytest.fixture
def handler(storage):
    approval = MagicMock()
    approval.resolve_tool_decision = AsyncMock(return_value=_record())
    approval.get_tool_decision = MagicMock(return_value=_record())
    outbound = MagicMock()
    outbound.send_card = AsyncMock(return_value="msg-1")
    outbound.edit_message = AsyncMock(return_value=True)
    h = ApprovalKeyboardHandler(
        settings=make_settings(home_channel="c-home", instance_id="tg-a"),
        storage=storage,
        event_bus=EventBus(),
        approval_manager=approval,
        outbound=outbound,
        instance_id="tg-a",
    )
    return h, outbound, approval


def test_keyboard_callback_binds_canonical_decision_id_within_telegram_limit():
    _, keyboard = build_approval_message(
        decision_request_id=_DECISION_1,
        memorial_id="memorial-1",
        edict_id="edict-1",
        tool_name="shell_exec",
        args_summary={"command": "git status"},
        reason="approval_required",
    )

    values = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert values
    assert all(value.endswith(_DECISION_1) for value in values)
    assert all(len(value.encode("utf-8")) <= 64 for value in values)


@pytest.mark.asyncio
async def test_approval_required_stores_decision_id_and_missing_id_is_not_actionable(
    handler, storage
):
    h, outbound, _ = handler
    edict = Edict(
        title="t",
        goal="g",
        source="channel",
        metadata={"chat_id": "c1", "instance_id": "tg-a"},
    )
    storage.save_edict(edict)

    await h._on_approval_required(
        EventEnvelope(
            event_type="tool.approval_required",
            edict_id=edict.id,
            memorial_id="same-memorial",
            payload={"decision_request_id": _DECISION_1, "tool_name": "shell_exec"},
        )
    )
    assert storage.get_telegram_pending_button(_DECISION_1) is not None

    await h._on_approval_required(
        EventEnvelope(
            event_type="tool.approval_required",
            edict_id=edict.id,
            memorial_id="same-memorial",
            payload={"decision_request_id": _DECISION_1, "tool_name": "shell_exec"},
        )
    )
    outbound.send_card.assert_awaited_once()

    outbound.send_card.reset_mock()
    await h._on_approval_required(
        EventEnvelope(
            event_type="tool.approval_required",
            edict_id=edict.id,
            memorial_id="same-memorial",
            payload={"tool_name": "shell_exec"},
        )
    )
    outbound.send_card.assert_not_awaited()
    assert storage.get_telegram_pending_button(_DECISION_1) is not None


@pytest.mark.asyncio
async def test_failed_button_delivery_releases_claim_for_retry(handler, storage):
    h, outbound, _ = handler
    edict = Edict(
        title="t",
        goal="g",
        source="channel",
        metadata={"chat_id": "c1", "instance_id": "tg-a"},
    )
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="tool.approval_required",
        edict_id=edict.id,
        memorial_id="memorial",
        payload={"decision_request_id": _DECISION_1, "tool_name": "shell_exec"},
    )
    outbound.send_card.side_effect = [RuntimeError("delivery failed"), "msg-retry"]

    with pytest.raises(RuntimeError, match="delivery failed"):
        await h._on_approval_required(event)
    await h._on_approval_required(event)

    assert outbound.send_card.await_count == 2
    assert storage.get_telegram_pending_button(_DECISION_1) is not None


@pytest.mark.asyncio
async def test_callback_resolves_decision_with_verified_instance_auth(handler, storage):
    h, _, approval = handler
    storage.save_telegram_pending_button(
        approval_id=_DECISION_1,
        chat_id="c1",
        message_id="m1",
        kind="tool.approval_required",
        instance_id="tg-a",
    )
    cb = TelegramCallback(
        update_id="u1",
        callback_id="q1",
        chat_id="c1",
        sender_id="7",
        message_id="m1",
        data=f"ea:approve:once:{_DECISION_1}",
    )

    assert await h.handle_callback(cb) == "✅ 已批准"
    kwargs = approval.resolve_tool_decision.await_args.kwargs
    assert approval.resolve_tool_decision.await_args.args == (_DECISION_1,)
    assert kwargs["action"] == "approve"
    assert kwargs["grant_scope"] == "once"
    auth = kwargs["auth"]
    assert auth.principal.id == "telegram:tg-a:7"
    assert auth.principal.kind is PrincipalKind.WEBHOOK
    assert auth.principal.scopes == frozenset({"decision:resolve"})
    assert auth.source is AuthenticationSource.WEBHOOK
    assert auth.client_kind is ClientKind.WEBHOOK
    assert auth.correlation_id.startswith("approval-button:")


@pytest.mark.asyncio
async def test_callback_displays_durable_cross_channel_winner(handler, storage):
    h, outbound, approval = handler
    approval.resolve_tool_decision.return_value = _record(
        action="reject",
        actor="feishu:feishu-a:ou_1",
    )
    storage.save_telegram_pending_button(
        approval_id=_DECISION_1,
        chat_id="c1",
        message_id="m1",
        kind="tool.approval_required",
        instance_id="tg-a",
    )
    cb = TelegramCallback(
        update_id="u1",
        callback_id="q1",
        chat_id="c1",
        sender_id="7",
        message_id="m1",
        data=f"ea:approve:always:{_DECISION_1}",
    )

    assert await h.handle_callback(cb) == "已被其他通道拒绝"
    text = outbound.edit_message.await_args.args[2]
    assert "已拒绝" in text
    assert "飞书" in text


@pytest.mark.asyncio
async def test_malformed_oversized_or_empty_sender_callback_has_no_effect(handler):
    h, outbound, approval = handler
    callbacks = (
        TelegramCallback("u", "q", "c", "7", "m", "ea:approve:once:not-ulid"),
        TelegramCallback("u", "q", "c", "7", "m", f"ea:approve:once:{'X' * 70}"),
        TelegramCallback("u", "q", "c", "", "m", f"ea:approve:once:{_DECISION_1}"),
    )

    for callback in callbacks:
        assert await h.handle_callback(callback) == "无效操作"

    approval.resolve_tool_decision.assert_not_awaited()
    outbound.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolved_event_pops_only_matching_decision(handler, storage):
    h, outbound, _ = handler
    for decision_id, message_id in ((_DECISION_1, "m1"), (_DECISION_2, "m2")):
        storage.save_telegram_pending_button(
            approval_id=decision_id,
            chat_id="c1",
            message_id=message_id,
            kind="tool.approval_required",
            instance_id="tg-a",
        )

    await h._on_decree_resolved(
        EventEnvelope(
            event_type="decree.approved",
            memorial_id="same-memorial",
            payload={
                "decision_request_id": _DECISION_1,
                "tool_name": "shell_exec",
                "actor": "web:user",
            },
        )
    )

    assert storage.get_telegram_pending_button(_DECISION_1) is None
    assert storage.get_telegram_pending_button(_DECISION_2) is not None
    outbound.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_must_match_pending_instance_chat_and_message(handler, storage):
    h, outbound, approval = handler
    storage.save_telegram_pending_button(
        approval_id=_DECISION_1,
        chat_id="c-other",
        message_id="m-other",
        kind="tool.approval_required",
        instance_id="tg-other",
    )
    cb = TelegramCallback(
        update_id="u",
        callback_id="q",
        chat_id="c1",
        sender_id="7",
        message_id="m1",
        data=f"ea:approve:once:{_DECISION_1}",
    )

    assert await h.handle_callback(cb) == "无效操作"
    approval.resolve_tool_decision.assert_not_awaited()
    outbound.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_decree_refresh_uses_durable_winner_not_event_actor(handler, storage):
    h, outbound, approval = handler
    approval.get_tool_decision.return_value = _record(
        action="approve", actor="feishu:feishu-a:ou_1"
    )
    storage.save_telegram_pending_button(
        approval_id=_DECISION_1,
        chat_id="c1",
        message_id="m1",
        kind="tool.approval_required",
        instance_id="tg-a",
    )

    await h._on_decree_resolved(
        EventEnvelope(
            event_type="decree.approved",
            memorial_id="memorial",
            payload={"decision_request_id": _DECISION_1, "actor": "web:forged"},
        )
    )

    assert "飞书" in outbound.edit_message.await_args.args[2]
