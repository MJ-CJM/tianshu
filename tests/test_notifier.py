"""Tests for Notifier and Renderer."""

import pytest

from tianshu.models import Memorial, TaskStatus, UsageSummary
from tianshu.models.common import AuditResult
from tianshu.notifier.notifier import Notifier
from tianshu.notifier.renderer import render_status


class TestRenderer:
    def test_render_completed(self):
        m = Memorial(
            edict_id="e1",
            status=TaskStatus.COMPLETED,
            summary="Task done",
            usage=UsageSummary(total_tokens=100),
        )
        result = render_status(m)
        assert result["status"] == "completed"
        assert result["summary"] == "Task done"
        assert result["tokens_used"] == 100

    def test_render_failed(self):
        m = Memorial(
            edict_id="e1",
            status=TaskStatus.FAILED,
            error="Something broke",
        )
        result = render_status(m)
        assert result["status"] == "failed"
        assert result["error"] == "Something broke"

    def test_render_with_audit(self):
        m = Memorial(
            edict_id="e1",
            status=TaskStatus.NEEDS_REVIEW,
            review_status="pending",
            audit=AuditResult(verdict="flag"),
        )
        result = render_status(m)
        assert result["audit_verdict"] == "flag"
        assert result["review_status"] == "pending"

    def test_render_truncates_long_fields(self):
        m = Memorial(
            edict_id="e1",
            summary="x" * 1000,
            error="y" * 1000,
        )
        result = render_status(m)
        assert len(result["summary"]) == 500
        assert len(result["error"]) == 500


class TestNotifier:
    @pytest.fixture
    def notifier(self, storage):
        return Notifier(storage=storage)

    async def test_broadcast_no_clients(self, notifier):
        # Should not raise even with no WebSocket clients
        await notifier.broadcast_ws({"test": "msg"})
