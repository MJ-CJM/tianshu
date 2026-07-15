"""Shared text approval commands resolve canonical durable decision IDs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.core.approval import ApprovalCommand, ApprovalCommandHandler
from tianshu.models.principal import AuthContext


async def test_text_command_expands_legacy_memorial_binding_to_decision_ids() -> None:
    approval = MagicMock()
    approval.list_pending_tool_calls.return_value = [
        {
            "decision_request_id": "01DECISIONAAAA11111111111111",
            "memorial_id": "memorial-shared",
        },
        {
            "decision_request_id": "01DECISIONBBBB22222222222222",
            "memorial_id": "memorial-shared",
        },
    ]
    approval.resolve_tool_decision = AsyncMock(
        return_value=SimpleNamespace(
            resolution=SimpleNamespace(action="approve", payload={"grant_scope": "edict"})
        )
    )
    handler = ApprovalCommandHandler(
        approval_manager=approval,
        list_pending=lambda _chat: ["memorial-shared"],
        actor_prefix="feishu",
    )

    disambiguation = await handler.handle(
        chat_id="oc_1",
        sender_open_id="ou_1",
        command=ApprovalCommand("approve", "once", None),
    )
    reply = await handler.handle(
        chat_id="oc_1",
        sender_open_id="ou_1",
        command=ApprovalCommand("approve", "edict", "01DECISIONAAAA"),
    )

    assert "01DECISI" in disambiguation
    assert "已批准" in reply
    kwargs = approval.resolve_tool_decision.await_args.kwargs
    assert approval.resolve_tool_decision.await_args.args == ("01DECISIONAAAA11111111111111",)
    assert isinstance(kwargs["auth"], AuthContext)
    assert kwargs["auth"].principal.id == "feishu:ou_1"
    assert kwargs["auth"].source.value == "webhook"
    assert "actor" not in kwargs
