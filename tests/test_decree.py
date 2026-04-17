"""Tests for approval/decree workflow."""

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.models import Decree, Edict, Memorial, TaskStatus


class TestApprovalManager:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def manager(self, event_bus, storage):
        return ApprovalManager(event_bus=event_bus, storage=storage)

    async def test_approve(self, manager, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.NEEDS_REVIEW,
            review_status="pending",
        )
        storage.save_memorial(memorial)

        decree = Decree(memorial_id=memorial.id, action="approve", comment="LGTM")
        await manager.submit_decree(decree)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.status == TaskStatus.COMPLETED
        assert loaded.review_status == "approved"

    async def test_reject(self, manager, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.NEEDS_REVIEW)
        storage.save_memorial(memorial)

        decree = Decree(memorial_id=memorial.id, action="reject", comment="Bad")
        await manager.submit_decree(decree)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.status == TaskStatus.FAILED
        assert loaded.review_status == "rejected"

    async def test_retry_creates_new_memorial(self, manager, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.NEEDS_REVIEW)
        storage.save_memorial(memorial)

        decree = Decree(memorial_id=memorial.id, action="retry")
        await manager.submit_decree(decree)

        memorials = storage.list_memorials_by_edict(edict.id)
        assert len(memorials) == 2
        new_memorial = [m for m in memorials if m.id != memorial.id][0]
        assert new_memorial.attempt == 2
        assert new_memorial.parent_memorial_id == memorial.id

    async def test_cancel(self, manager, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.NEEDS_REVIEW)
        storage.save_memorial(memorial)

        decree = Decree(memorial_id=memorial.id, action="cancel")
        await manager.submit_decree(decree)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.status == TaskStatus.CANCELLED

    async def test_amend_creates_new_edict(self, manager, storage):
        edict = Edict(goal="original")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.NEEDS_REVIEW)
        storage.save_memorial(memorial)

        decree = Decree(
            memorial_id=memorial.id,
            action="amend",
            amended_goal="better goal",
        )
        await manager.submit_decree(decree)

        edicts, _ = storage.list_edicts()
        assert len(edicts) == 2

    async def test_invalid_memorial(self, manager):
        decree = Decree(memorial_id="nonexistent", action="approve")
        with pytest.raises(ValueError, match="not found"):
            await manager.submit_decree(decree)
