"""Tests for approval/decree workflow."""

import asyncio

import pytest

from tianshu.application.edicts import EdictApplicationService
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Decree, Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.principal import AuthContext, Principal


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="user:reviewer",
            kind="human",
            display_name="Reviewer",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="test-tool-decision",
    )


def _suspend(manager, edict, memorial, tool_name):
    return manager.request_tool_decision(
        edict=edict,
        memorial=memorial,
        invocation_id=f"call:{memorial.id}",
        tool_name=tool_name,
        tool_args={"command": "git status"},
        tool_tier="T1_WORKSPACE",
        policy_rule_id="approval_required",
        messages=[{"role": "user", "content": "run"}],
        iteration=1,
        usage=UsageSummary(),
    )


class TestApprovalManager:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def manager(self, event_bus, storage):
        return ApprovalManager(
            event_bus=event_bus,
            storage=storage,
            edict_application_service=EdictApplicationService(storage),
            decision_service=DecisionService(storage),
        )

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
        edict = Edict(goal="g")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
        storage.save_memorial(memorial)

        manager._pending[memorial.id] = asyncio.Event()
        requested = _suspend(manager, edict, memorial, "shell_exec")

        captured: list = []

        async def collect(evt):
            captured.append(evt)

        event_bus.on("decree.approved", collect, consumer_name="test.decree_approved.v1")

        record = await manager.resolve_tool_decision(
            requested.decision_request_id,
            action="approve",
            grant_scope="always",
            auth=_auth(),
        )

        assert record.resolution is not None
        assert record.resolution.payload["grant_scope"] == "once"

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
        edict = Edict(goal="g")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
        storage.save_memorial(memorial)

        manager._pending[memorial.id] = asyncio.Event()
        requested = _suspend(manager, edict, memorial, "read_file")

        captured: list = []

        async def collect(evt):
            captured.append(evt)

        event_bus.on("decree.approved", collect, consumer_name="test.decree_approved.v1")

        record = await manager.resolve_tool_decision(
            requested.decision_request_id,
            action="approve",
            grant_scope="always",
            auth=_auth(),
        )
        assert record.resolution is not None
        assert record.resolution.payload["grant_scope"] == "always"

        await asyncio.sleep(0.05)
        payload = captured[0].payload
        assert payload["grant_scope"] == "always"
        assert payload["grant_downgraded"] is False


class TestDecreeGuidance:
    """批红「驳回+指导」(迭代 5)——驳回工具但注入纠正意见,agent 据此续跑。"""

    @pytest.fixture
    def event_bus(self):
        from tianshu.bus.event_bus import EventBus

        return EventBus()

    @pytest.fixture
    def manager(self, event_bus, storage):
        from tianshu.executor.approvals import ApprovalManager

        return ApprovalManager(
            event_bus=event_bus,
            storage=storage,
            decision_service=DecisionService(storage),
        )

    async def test_guide_emits_guided_event_and_wakes_wait(self, manager, storage, event_bus):
        edict = Edict(goal="g")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
        storage.save_memorial(memorial)
        manager._pending[memorial.id] = asyncio.Event()
        requested = _suspend(manager, edict, memorial, "shell_exec")

        captured: list = []
        event_bus.on(
            "decree.guided",
            lambda e: captured.append(e),
            consumer_name="test.decree_guided.v1",
        )

        record = await manager.resolve_tool_decision(
            requested.decision_request_id,
            action="guide",
            comment="改用 read_file 而非 shell cat",
            auth=_auth(),
        )
        assert record.resolution is not None and record.resolution.action == "guide"
        # 唤醒等待的工具调用
        assert manager._pending[memorial.id].is_set()
        await asyncio.sleep(0.05)
        assert (
            len(captured) == 1 and captured[0].payload["comment"] == "改用 read_file 而非 shell cat"
        )
