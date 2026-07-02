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

    async def test_tool_decision_downgrades_always_for_shell_exec(
        self,
        manager,
        storage,
        event_bus,
    ):
        """Bug A 回归：shell_exec + always 应被前置降级为 once，且事件 payload
        透出 grant_downgraded=true，前端可以提示用户'已降级为本次'。"""
        import asyncio

        edict = Edict(goal="g")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
        storage.save_memorial(memorial)

        # 模拟 PolicyHook 正在等待审批
        manager._pending[memorial.id] = asyncio.Event()
        manager._pending_tool[memorial.id] = "shell_exec"

        captured: list = []

        async def collect(evt):
            captured.append(evt)

        event_bus.on("decree.approved", collect)

        decree = await manager.submit_tool_decision(
            memorial_id=memorial.id,
            action="approve",
            grant_scope="always",
        )

        # decree 实际落库的 grant_scope 已被降级
        assert decree.grant_scope == "once"

        # 等事件总线 flush
        await asyncio.sleep(0.05)
        assert len(captured) == 1
        payload = captured[0].payload
        assert payload["grant_scope"] == "once"
        assert payload["requested_grant_scope"] == "always"
        assert payload["grant_downgraded"] is True
        assert "shell_exec" in (payload["grant_downgrade_reason"] or "")

    async def test_tool_decision_keeps_always_for_non_bash_tool(
        self,
        manager,
        storage,
        event_bus,
    ):
        """非 bash 类工具不会被降级 — always 正常生效。"""
        import asyncio

        edict = Edict(goal="g")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
        storage.save_memorial(memorial)

        manager._pending[memorial.id] = asyncio.Event()
        manager._pending_tool[memorial.id] = "read_file"

        captured: list = []

        async def collect(evt):
            captured.append(evt)

        event_bus.on("decree.approved", collect)

        decree = await manager.submit_tool_decision(
            memorial_id=memorial.id,
            action="approve",
            grant_scope="always",
        )
        assert decree.grant_scope == "always"

        await asyncio.sleep(0.05)
        payload = captured[0].payload
        assert payload["grant_scope"] == "always"
        assert payload["grant_downgraded"] is False
