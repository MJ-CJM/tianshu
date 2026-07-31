"""Tests for Auditor and RulesEngine."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tianshu.auditor.auditor import Auditor
from tianshu.auditor.rules import RulesEngine
from tianshu.auditor.rules_config import AuditRulesConfig
from tianshu.bus.event_bus import EventBus
from tianshu.llm import LLMUsageContext
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

    def test_config_disables_rules_and_enables_risk_keyword_scan(self):
        rules = RulesEngine(
            AuditRulesConfig(
                check_token_budget=False,
                check_execution_error=False,
                check_empty_result=False,
                risk_keywords=("credential leak",),
            )
        )
        edict = Edict(goal="test", runtime=EdictRuntime(token_budget=1))
        memorial = Memorial(
            edict_id=edict.id,
            result="Potential credential leak in the generated output",
            error="ignored by disabled rule",
            usage=UsageSummary(total_tokens=100),
        )

        result = rules.check(edict, memorial)

        assert result.verdict == "flag"
        assert result.rules_checked == 1
        assert result.reasons == ["Risk keyword detected: credential leak"]


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

    async def test_reviewer_uses_configured_generation_limits(
        self,
        event_bus,
        storage,
        config_manager,
    ):
        rules_config = AuditRulesConfig(
            review_temperature=0.35,
            review_max_tokens=321,
            risk_keywords=("risk",),
        )
        auditor = Auditor(
            event_bus=event_bus,
            storage=storage,
            config_manager=config_manager,
            rules_config=rules_config,
        )
        llm = AsyncMock()
        llm.chat.return_value = SimpleNamespace(content='{"verdict":"pass","reasons":[]}')
        edict = Edict(goal="test", review_policy="on_flag")
        memorial = Memorial(edict_id=edict.id, result="risk")

        with patch("tianshu.auditor.reviewer.LLMClient", return_value=llm) as llm_type:
            result = await auditor.audit(edict, memorial)

        assert result.verdict == "pass"
        assert llm_type.call_args.kwargs["temperature"] == 0.35
        assert llm_type.call_args.kwargs["max_tokens"] == 321
        assert llm.chat.await_args.kwargs["usage_context"] == LLMUsageContext(
            edict_id=edict.id,
            memorial_id=memorial.id,
            operation="audit_review",
        )

    async def test_conversational_executor_keeps_edict_open(self, auditor, storage):
        # 对话式客卿(pi RPC 会话档)审计通过后须保持 OPEN,供连续 follow_up;
        # 否则一次产出即 auto-close、canFollowUp 失效,用户无法「继续批示」追问。
        from tianshu.models.common import EdictStatus
        from tianshu.models.edict import EdictRuntime
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

    async def test_conversation_mode_keeps_native_edict_open(self, auditor, storage):
        # runtime.conversation（对话模式）：百官/native 执行成功过审后保持 OPEN，
        # 由人工结案——「继续批示」持续可用（follow_up 回放多轮上下文）。
        from tianshu.models.common import EdictStatus
        from tianshu.models.edict import EdictRuntime
        from tianshu.models.events import make_event

        edict = Edict(goal="chat", runtime=EdictRuntime(conversation=True))
        memorial = Memorial(edict_id=edict.id, result="Hi!", status=TaskStatus.COMPLETED)
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        event = make_event(
            "execution.completed", edict_id=edict.id, memorial_id=memorial.id, producer="test"
        )
        await auditor.handle_execution_completed(event)
        assert storage.get_edict(edict.id).status == EdictStatus.OPEN.value  # 保持 open

    async def test_default_native_edict_stays_open(self, auditor, storage):
        # 2026-07-29 拍板：conversation 默认开启——人下的敕令默认多轮批示，
        # 审计通过后保持 OPEN，结案权在人。
        from tianshu.models.common import EdictStatus
        from tianshu.models.events import make_event

        edict = Edict(goal="chat-by-default")
        memorial = Memorial(edict_id=edict.id, result="done", status=TaskStatus.COMPLETED)
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        event = make_event(
            "execution.completed", edict_id=edict.id, memorial_id=memorial.id, producer="test"
        )
        await auditor.handle_execution_completed(event)
        assert storage.get_edict(edict.id).status == EdictStatus.OPEN.value

    async def test_single_shot_executor_auto_closes(self, auditor, storage):
        # 显式 conversation=False（机器自动化入口）保持一次性闭环语义。
        from tianshu.models.common import EdictStatus
        from tianshu.models.edict import EdictRuntime
        from tianshu.models.events import make_event

        edict = Edict(goal="task", runtime=EdictRuntime(conversation=False))
        memorial = Memorial(edict_id=edict.id, result="done", status=TaskStatus.COMPLETED)
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        event = make_event(
            "execution.completed", edict_id=edict.id, memorial_id=memorial.id, producer="test"
        )
        await auditor.handle_execution_completed(event)
        assert storage.get_edict(edict.id).status == EdictStatus.COMPLETED.value
