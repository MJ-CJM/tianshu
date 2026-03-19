"""Extended Auditor tests — handle_execution_completed handler."""

from unittest.mock import AsyncMock

import pytest

from tianshu.auditor.auditor import Auditor
from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.events import make_event


class TestAuditorHandler:
    @pytest.fixture
    def event_bus(self, storage):
        return EventBus(storage=storage)

    @pytest.fixture
    def auditor(self, event_bus, storage, config_manager):
        return Auditor(
            event_bus=event_bus,
            storage=storage,
            config_manager=config_manager,
        )

    async def test_handle_execution_completed_pass(self, auditor, storage, event_bus):
        audit_handler = AsyncMock()
        event_bus.on("audit.completed", audit_handler)

        edict = Edict(goal="test", review_policy="never")
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

        audit_handler.assert_called_once()
        loaded = storage.get_memorial(memorial.id)
        assert loaded.review_status == "not_required"

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
        edict = Edict(goal="test", review_policy="on_failure")
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
