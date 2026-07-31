"""Extended Auditor tests — handle_execution_completed handler."""

from unittest.mock import AsyncMock

import pytest

from tianshu.auditor.auditor import Auditor
from tianshu.bus.event_bus import EventBus
from tianshu.models import AuditResult, Edict, EdictStatus, Memorial, TaskStatus
from tianshu.models.edict import EdictRuntime
from tianshu.models.events import make_event


class TestAuditorHandler:
    @pytest.fixture
    def event_bus(self, storage):
        return EventBus()

    @pytest.fixture
    def auditor(self, event_bus, storage, config_manager):
        return Auditor(
            event_bus=event_bus,
            storage=storage,
            config_manager=config_manager,
        )

    async def test_handle_execution_completed_pass(self, auditor, storage, event_bus):
        audit_handler = AsyncMock()
        event_bus.on(
            "audit.completed",
            audit_handler,
            consumer_name="test.audit_completed.v1",
        )

        # conversation=False：本用例验证一次性闭环语义（默认已是多轮保持 open）
        edict = Edict(goal="test", review_policy="never", runtime=EdictRuntime(conversation=False))
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.COMPLETED,
            result="done",
        )
        storage.save_memorial(memorial)

        event = make_event(
            "execution.completed",
            edict_id=edict.id,
            memorial_id=memorial.id,
        )
        await auditor.handle_execution_completed(event)

        audit_handler.assert_awaited_once()
        audit_event = audit_handler.await_args.args[0]
        assert audit_event.event_type == "audit.completed"
        assert audit_event.edict_id == edict.id
        assert audit_event.memorial_id == memorial.id
        assert audit_event.payload == {"verdict": "pass", "reasons": []}
        loaded = storage.get_memorial(memorial.id)
        assert loaded.review_status == "not_required"
        assert storage.get_edict(edict.id).status == EdictStatus.COMPLETED

    async def test_handle_execution_completed_always_review(self, auditor, storage, event_bus):
        edict = Edict(goal="test", review_policy="always")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.COMPLETED,
            result="done",
        )
        storage.save_memorial(memorial)

        event = make_event(
            "execution.completed",
            edict_id=edict.id,
            memorial_id=memorial.id,
        )
        await auditor.handle_execution_completed(event)

        loaded = storage.get_memorial(memorial.id)
        # With "always" policy, audit runs and may flag or pass
        assert loaded.audit is not None

    async def test_handle_missing_edict(self, auditor, storage, event_bus):
        event = make_event(
            "execution.completed",
            edict_id="nonexistent",
            memorial_id="nonexistent",
        )
        await auditor.handle_execution_completed(event)  # Should not raise

    async def test_handle_missing_ids(self, auditor):
        event = make_event("execution.completed")
        await auditor.handle_execution_completed(event)  # Should not raise

    async def test_on_failure_policy(self, auditor, storage, event_bus):
        audit_handler = AsyncMock()
        event_bus.on(
            "audit.completed",
            audit_handler,
            consumer_name="test.audit_completed.v1",
        )
        auditor.audit = AsyncMock()
        edict = Edict(
            goal="test", review_policy="on_failure", runtime=EdictRuntime(conversation=False)
        )
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.COMPLETED,  # Not failed, so no audit
            result="done",
        )
        storage.save_memorial(memorial)

        event = make_event(
            "execution.completed",
            edict_id=edict.id,
            memorial_id=memorial.id,
        )
        await auditor.handle_execution_completed(event)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.review_status == "not_required"
        auditor.audit.assert_not_awaited()
        audit_handler.assert_awaited_once()
        assert audit_handler.await_args.args[0].payload["verdict"] == "pass"
        assert storage.get_edict(edict.id).status == EdictStatus.COMPLETED

    async def test_on_failure_policy_audits_failed_event_without_marking_success(
        self,
        auditor,
        storage,
        event_bus,
    ):
        audit_handler = AsyncMock()
        event_bus.on(
            "audit.completed",
            audit_handler,
            consumer_name="test.audit_completed.v1",
        )
        auditor.audit = AsyncMock(
            return_value=AuditResult(
                verdict="flag",
                reasons=["review the failed output"],
                rules_checked=3,
                llm_reviewed=True,
            )
        )
        edict = Edict(
            goal="test",
            review_policy="on_failure",
            runtime=EdictRuntime(conversation=False),
        )
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.FAILED,
            error="executor crashed",
        )
        storage.save_memorial(memorial)

        event = make_event(
            "execution.failed",
            edict_id=edict.id,
            memorial_id=memorial.id,
        )
        await auditor.handle_execution_failed(event)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.audit is not None
        assert loaded.audit.execution_failed is True
        assert loaded.review_status == "pending"
        assert loaded.status == TaskStatus.NEEDS_REVIEW
        assert storage.get_edict(edict.id).status == EdictStatus.OPEN
        auditor.audit.assert_awaited_once()
        audit_handler.assert_awaited_once()
        assert audit_handler.await_args.args[0].payload["verdict"] == "flag"
