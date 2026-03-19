"""Integration test — decree (approval) workflow."""

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.models import Decree, Edict, Memorial, TaskStatus


class TestDecreeIntegration:
    @pytest.fixture
    def event_bus(self, storage):
        return EventBus(storage=storage)

    @pytest.fixture
    def manager(self, event_bus, storage):
        return ApprovalManager(event_bus=event_bus, storage=storage)

    async def test_approve_flow(self, manager, storage, event_bus):
        """approve decree → memorial COMPLETED."""
        events_seen = []

        async def track(e):
            events_seen.append(e.event_type)

        event_bus.on("decree.approved", track)

        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.NEEDS_REVIEW,
            review_status="pending",
            result="Some result",
        )
        storage.save_memorial(memorial)

        decree = Decree(memorial_id=memorial.id, action="approve")
        await manager.submit_decree(decree)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.status == TaskStatus.COMPLETED
        assert loaded.review_status == "approved"
        assert "decree.approved" in events_seen

    async def test_reject_flow(self, manager, storage, event_bus):
        """reject decree → memorial FAILED."""
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.NEEDS_REVIEW,
        )
        storage.save_memorial(memorial)

        decree = Decree(memorial_id=memorial.id, action="reject", comment="Not good")
        await manager.submit_decree(decree)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.status == TaskStatus.FAILED
        assert loaded.review_status == "rejected"

    async def test_retry_flow(self, manager, storage, event_bus):
        """retry decree → new Memorial with attempt+1."""
        events_seen = []

        async def track(e):
            events_seen.append(e.event_type)

        event_bus.on("decree.retry", track)

        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.NEEDS_REVIEW,
            attempt=1,
        )
        storage.save_memorial(memorial)

        decree = Decree(memorial_id=memorial.id, action="retry")
        await manager.submit_decree(decree)

        memorials = storage.list_memorials_by_edict(edict.id)
        assert len(memorials) == 2

        new = [m for m in memorials if m.id != memorial.id][0]
        assert new.attempt == 2
        assert new.parent_memorial_id == memorial.id
        assert "decree.retry" in events_seen

    async def test_amend_flow(self, manager, storage, event_bus):
        """amend decree → new Edict created."""
        edict = Edict(goal="original goal")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.NEEDS_REVIEW,
        )
        storage.save_memorial(memorial)

        decree = Decree(
            memorial_id=memorial.id,
            action="amend",
            amended_goal="improved goal",
        )
        await manager.submit_decree(decree)

        edicts, _ = storage.list_edicts()
        goals = [e.goal for e in edicts]
        assert "improved goal" in goals

    async def test_full_approve_reject_cycle(self, manager, storage, event_bus):
        """Multiple decrees on same memorial."""
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.NEEDS_REVIEW,
        )
        storage.save_memorial(memorial)

        # First reject
        d1 = Decree(memorial_id=memorial.id, action="reject")
        await manager.submit_decree(d1)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.status == TaskStatus.FAILED

        decrees = storage.list_decrees_by_memorial(memorial.id)
        assert len(decrees) == 1
