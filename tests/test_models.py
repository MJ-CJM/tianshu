"""Tests for Phase 0 models — Edict/Memorial serialization, defaults, enums."""

from datetime import UTC, datetime

from tianshu.models import (
    ApiResponse,
    Edict,
    EdictStatus,
    Memorial,
    TaskStatus,
    UsageSummary,
)


class TestTaskStatus:
    def test_values(self):
        assert TaskStatus.SUBMITTED == "submitted"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"

    def test_phase1_values(self):
        assert TaskStatus.SCHEDULED == "scheduled"
        assert TaskStatus.PLANNING == "planning"
        assert TaskStatus.AUDITING == "auditing"
        assert TaskStatus.NEEDS_REVIEW == "needs_review"


class TestEdictStatus:
    def test_values(self):
        assert EdictStatus.OPEN == "open"
        assert EdictStatus.COMPLETED == "completed"
        assert EdictStatus.CANCELLED == "cancelled"


class TestUsageSummary:
    def test_defaults(self):
        u = UsageSummary()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_serialization(self):
        u = UsageSummary(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        data = u.model_dump()
        assert data["total_tokens"] == 30
        restored = UsageSummary(**data)
        assert restored == u


class TestEdict:
    def test_defaults(self):
        e = Edict(goal="test goal")
        assert e.goal == "test goal"
        assert e.status == EdictStatus.OPEN
        assert e.title == ""
        assert e.context is None
        assert e.id  # auto-generated

    def test_serialization(self):
        e = Edict(goal="test", title="t")
        data = e.model_dump(mode="json")
        assert data["goal"] == "test"
        assert "id" in data
        assert "created_at" in data

    def test_unique_ids(self):
        e1 = Edict(goal="a")
        e2 = Edict(goal="b")
        assert e1.id != e2.id


class TestMemorial:
    def test_defaults(self):
        m = Memorial(edict_id="test-edict")
        assert m.edict_id == "test-edict"
        assert m.status == TaskStatus.SUBMITTED
        assert m.usage.total_tokens == 0
        assert m.error is None
        assert m.attempt == 1
        assert m.review_status == "not_required"

    def test_serialization(self):
        m = Memorial(edict_id="e1", status=TaskStatus.COMPLETED, result="done")
        data = m.model_dump(mode="json")
        restored = Memorial(**data)
        assert restored.edict_id == "e1"
        assert restored.result == "done"


class TestApiResponse:
    def test_success(self):
        r = ApiResponse(success=True, data={"key": "val"})
        assert r.success is True
        assert r.data == {"key": "val"}

    def test_error(self):
        r = ApiResponse(success=False, error="something went wrong")
        assert r.error == "something went wrong"
        assert r.data is None
