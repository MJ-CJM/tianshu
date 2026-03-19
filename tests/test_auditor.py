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
