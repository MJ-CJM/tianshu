"""Ingress adapters delegate one durable Edict submission with stable identities."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.application.edicts import SubmitEdictResult
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.core.edict_bridge import EdictBridge
from tianshu.gateway.feishu.dispatcher import FeishuMessage
from tianshu.gateway.telegram.dispatcher import TelegramMessage
from tianshu.models import Decree, Edict, Memorial, TaskStatus
from tianshu.models.principal import AuthContext
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.schedule_edict import register_schedule_edict
from tianshu.tools.submit_edict import register_submit_edict


class RecordingEdictService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, AuthContext, str, str]] = []

    def submit(
        self,
        command: Any,
        *,
        auth: AuthContext,
        producer: str,
        correlation_id: str,
    ) -> SubmitEdictResult:
        self.calls.append((command, auth, producer, correlation_id))
        return SubmitEdictResult(
            edict=command.edict,
            memorial=Memorial(
                edict_id=command.edict.id,
                instruction=command.edict.goal,
                status=TaskStatus.SUBMITTED,
            ),
            event_id=f"event-{len(self.calls)}",
            request_hash="a" * 64,
            deduplicated=False,
        )


def test_compatibility_wrapper_delegates_without_legacy_persistence(storage) -> None:
    from tianshu.edict_ops import submit_new_edict

    service = RecordingEdictService()
    edict = Edict(goal="compatibility submission")

    with pytest.warns(DeprecationWarning):
        memorial = submit_new_edict(
            storage,
            EventBus(),
            edict,
            producer="legacy-caller",
            idempotency_key="legacy-request-1",
            correlation_id="legacy-correlation-1",
            edict_application_service=service,
        )

    assert memorial.edict_id == edict.id
    assert len(service.calls) == 1
    command, _auth, producer, correlation_id = service.calls[0]
    assert command.idempotency_key == "legacy-request-1"
    assert producer == "legacy-caller"
    assert correlation_id == "legacy-correlation-1"
    assert storage.get_edict(edict.id) is None


@pytest.mark.asyncio
async def test_bot_bridge_uses_verified_source_message_identity_once(storage) -> None:
    service = RecordingEdictService()
    anchor = MagicMock()
    executor = MagicMock(execute_edict=AsyncMock(), running_tasks=set())
    bridge = EdictBridge(
        storage=storage,
        event_bus=EventBus(),
        executor=executor,
        anchor=anchor,
        channel="feishu",
        edict_application_service=service,
    )

    result = await bridge.create_new(
        chat_id="oc-chat",
        sender_open_id="ou-user",
        goal="prepare report",
        source_message_id="evt-source-1",
    )

    assert result.edict_id
    assert len(service.calls) == 1
    command, auth, producer, correlation_id = service.calls[0]
    assert command.idempotency_key == "feishu:evt-source-1"
    assert auth.principal.id == "feishu:ou-user"
    assert producer == "feishu_bot"
    assert correlation_id == "feishu:evt-source-1"
    anchor.set.assert_called_once_with("oc-chat", result.edict_id)


def test_channel_messages_expose_their_durable_ingress_identity() -> None:
    feishu = FeishuMessage(
        event_id="feishu-event-1",
        chat_id="oc-chat",
        chat_type="p2p",
        sender_open_id="ou-user",
        text="hello",
        raw={},
        message_id="om-message-1",
    )
    telegram = TelegramMessage(
        update_id="telegram-update-1",
        chat_id="100",
        chat_type="private",
        sender_id="200",
        text="hello",
        message_id="300",
    )

    assert feishu.ingress_id == "feishu-event-1"
    assert telegram.ingress_id == "telegram-update-1"


@pytest.mark.asyncio
async def test_submit_tool_uses_one_tool_call_identity(storage) -> None:
    registry = ToolRegistry()
    service = RecordingEdictService()
    register_submit_edict(
        registry,
        storage=storage,
        event_bus=EventBus(),
        edict_application_service=service,
    )

    result = await registry.execute(
        "submit_edict",
        {"goal": "tool submission"},
        invocation_id="tool-call-1",
    )

    assert result.is_error is False
    assert len(service.calls) == 1
    command, _auth, _producer, correlation_id = service.calls[0]
    assert command.idempotency_key == "tool:tool-call-1"
    assert correlation_id == "tool:tool-call-1"


@pytest.mark.asyncio
async def test_schedule_tool_submits_once_and_uses_durable_event_job_id(storage) -> None:
    registry = ToolRegistry()
    service = RecordingEdictService()
    scheduler = MagicMock()
    scheduler.schedule = AsyncMock(return_value="submitted-job")
    register_schedule_edict(
        registry,
        storage=storage,
        scheduler=scheduler,
        edict_application_service=service,
    )

    result = await registry.execute(
        "schedule_edict",
        {"action": "create", "goal": "daily report", "schedule": "0 9 * * *"},
        invocation_id="schedule-call-1",
    )

    assert result.is_error is False
    assert len(service.calls) == 1
    command, _auth, _producer, correlation_id = service.calls[0]
    assert command.idempotency_key == "tool:schedule-call-1"
    assert correlation_id == "tool:schedule-call-1"
    scheduler.schedule.assert_not_awaited()
    assert result.details["job_id"].startswith("submitted-")


@pytest.mark.asyncio
async def test_governed_amend_uses_decree_identity_once(storage) -> None:
    original = Edict(goal="original")
    storage.save_edict(original)
    memorial = Memorial(
        edict_id=original.id,
        instruction=original.goal,
        status=TaskStatus.NEEDS_REVIEW,
    )
    storage.save_memorial(memorial)
    service = RecordingEdictService()
    manager = ApprovalManager(
        event_bus=EventBus(),
        storage=storage,
        edict_application_service=service,
    )
    decree = Decree(
        id="decision-amend-1",
        memorial_id=memorial.id,
        action="amend",
        amended_goal="amended",
        actor="reviewer-1",
    )

    await manager.submit_decree(decree)

    assert len(service.calls) == 1
    command, auth, producer, correlation_id = service.calls[0]
    assert command.idempotency_key == "amend:decision-amend-1"
    assert command.extra_payload["amended_from"] == memorial.id
    assert auth.principal.id == "approval:reviewer-1"
    assert producer == "approval_manager"
    assert correlation_id == "amend:decision-amend-1"
