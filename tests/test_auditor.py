"""Tests for Auditor and RulesEngine."""

import pytest

from tianshu.auditor.auditor import Auditor
from tianshu.auditor.rules import RulesEngine
from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.edict import EdictRuntime


class TestRulesEngine:
    @pytest.fixture
    def rules(self):
        return RulesEngine()

    def test_pass_clean(self, rules):
        edict = Edict(goal="test")
        memorial = Memorial(edict_id=edict.id, result="done", status=TaskStatus.COMPLETED)
        result = rules.check(edict, memorial)
        assert result.verdict == "pass"
        assert result.rules_checked > 0

    def test_flag_over_budget(self, rules):
        edict = Edict(
            goal="test",
            runtime=EdictRuntime(token_budget=100),
        )
        memorial = Memorial(
            edict_id=edict.id,
            result="done",
            usage=UsageSummary(total_tokens=200),
        )
        result = rules.check(edict, memorial)
        assert result.verdict == "flag"
        assert any("budget" in r.lower() for r in result.reasons)

    def test_flag_with_error(self, rules):
        edict = Edict(goal="test")
        memorial = Memorial(
            edict_id=edict.id,
            error="Something went wrong",
            result="partial",
        )
        result = rules.check(edict, memorial)
        assert result.verdict == "flag"

    def test_flag_empty_result(self, rules):
        edict = Edict(goal="test")
        memorial = Memorial(edict_id=edict.id)
        result = rules.check(edict, memorial)
        assert result.verdict == "flag"
        assert any("no result" in r.lower() for r in result.reasons)


class TestAuditor:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def auditor(self, event_bus, storage, config_manager):
        return Auditor(
            event_bus=event_bus,
            storage=storage,
            config_manager=config_manager,
        )

    async def test_audit_pass(self, auditor):
        edict = Edict(goal="test")
        memorial = Memorial(
            edict_id=edict.id,
            result="done",
            status=TaskStatus.COMPLETED,
        )
        result = await auditor.audit(edict, memorial)
        assert result.verdict == "pass"

    async def test_audit_flag(self, auditor):
        edict = Edict(goal="test", review_policy="on_flag")
        memorial = Memorial(edict_id=edict.id)
        result = await auditor.audit(edict, memorial)
        assert result.verdict in ("pass", "flag")

    async def test_conversational_executor_keeps_edict_open(self, auditor, storage):
        # 对话式客卿(pi RPC 会话档)审计通过后须保持 OPEN,供连续 follow_up;
        # 否则一次产出即 auto-close、canFollowUp 失效,用户无法「继续批示」追问。
        from tianshu.models.edict import EdictRuntime
        from tianshu.models.common import EdictStatus
        from tianshu.models.events import make_event

        edict = Edict(goal="hi", runtime=EdictRuntime(executor="keqing:pi"))
        memorial = Memorial(edict_id=edict.id, result="Hi!", status=TaskStatus.COMPLETED)
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        event = make_event(
            "execution.completed", edict_id=edict.id, memorial_id=memorial.id, producer="test"
        )
        await auditor.handle_execution_completed(event)
        assert storage.get_edict(edict.id).status == EdictStatus.OPEN.value  # 保持 open

    async def test_single_shot_executor_auto_closes(self, auditor, storage):
        # 单发/native 审计通过后仍 auto-close(一次性任务语义不变)。
        from tianshu.models.common import EdictStatus
        from tianshu.models.events import make_event

        edict = Edict(goal="task")  # 默认 native 执行器
        memorial = Memorial(edict_id=edict.id, result="done", status=TaskStatus.COMPLETED)
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        event = make_event(
            "execution.completed", edict_id=edict.id, memorial_id=memorial.id, producer="test"
        )
        await auditor.handle_execution_completed(event)
        assert storage.get_edict(edict.id).status == EdictStatus.COMPLETED.value
