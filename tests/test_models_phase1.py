"""Tests for Phase 1 model extensions."""

from tianshu.models import (
    AuditResult,
    Decree,
    Edict,
    EdictDispatch,
    EdictRuntime,
    EdictSchedule,
    EventEnvelope,
    Memorial,
    Plan,
    PlanTask,
    make_event,
)


class TestEdictPhase1:
    def test_new_defaults(self):
        e = Edict(goal="test")
        assert e.source == "api"
        assert e.priority == "normal"
        assert e.review_policy == "never"
        assert e.constraints == []
        assert e.schedule.type == "immediate"
        assert e.runtime.timeout_seconds == 300
        assert e.runtime.max_iterations == 20
        assert e.metadata == {}
        assert e.idempotency_key is None
        assert e.dispatch is None

    def test_schedule(self):
        s = EdictSchedule(type="cron", cron="0 * * * *")
        e = Edict(goal="test", schedule=s)
        assert e.schedule.type == "cron"
        assert e.schedule.cron == "0 * * * *"

    def test_runtime(self):
        r = EdictRuntime(
            timeout_seconds=60,
            max_iterations=10,
            token_budget=5000,
            approval_required_tools=["shell_exec"],
        )
        e = Edict(goal="test", runtime=r)
        assert e.runtime.timeout_seconds == 60
        assert e.runtime.token_budget == 5000

    def test_dispatch(self):
        d = EdictDispatch(channels=["ws"], mode="broadcast")
        e = Edict(goal="test", dispatch=d)
        assert e.dispatch.channels == ["ws"]


class TestMemorialPhase1:
    def test_new_defaults(self):
        m = Memorial(edict_id="e1")
        assert m.attempt == 1
        assert m.parent_memorial_id is None
        assert m.review_status == "not_required"
        assert m.audit is None
        assert m.artifacts == []
        assert m.timeline == []

    def test_with_audit(self):
        audit = AuditResult(verdict="flag", reasons=["over budget"])
        m = Memorial(edict_id="e1", audit=audit)
        assert m.audit.verdict == "flag"
        assert len(m.audit.reasons) == 1


class TestDecree:
    def test_creation(self):
        d = Decree(memorial_id="m1", action="approve")
        assert d.memorial_id == "m1"
        assert d.action == "approve"
        assert d.actor == "human"
        assert d.id  # auto-generated

    def test_actions(self):
        for action in ("approve", "reject", "retry", "amend", "cancel"):
            d = Decree(memorial_id="m1", action=action)
            assert d.action == action

    def test_amend_with_goal(self):
        d = Decree(memorial_id="m1", action="amend", amended_goal="new goal")
        assert d.amended_goal == "new goal"


class TestEventEnvelope:
    def test_creation(self):
        e = EventEnvelope(event_type="test.event", edict_id="e1")
        assert e.event_type == "test.event"
        assert e.edict_id == "e1"
        assert e.producer == "system"
        assert e.payload == {}

    def test_make_event(self):
        e = make_event("edict.submitted", edict_id="e1", producer="gateway")
        assert e.event_type == "edict.submitted"
        assert e.producer == "gateway"
        assert e.event_id  # auto-generated


class TestPlan:
    def test_empty_plan(self):
        p = Plan()
        assert p.tasks == []
        assert p.priority_order == []

    def test_plan_with_tasks(self):
        t1 = PlanTask(task_id="t1", description="do thing")
        t2 = PlanTask(task_id="t2", description="do other", depends_on=["t1"])
        p = Plan(tasks=[t1, t2], priority_order=["t1", "t2"])
        assert len(p.tasks) == 2
        assert p.tasks[1].depends_on == ["t1"]

    def test_plan_serialization(self):
        t = PlanTask(task_id="t1", description="test", estimated_tokens=1000)
        p = Plan(tasks=[t])
        data = p.model_dump()
        restored = Plan(**data)
        assert restored.tasks[0].estimated_tokens == 1000
