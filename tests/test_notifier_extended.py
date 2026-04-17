"""Extended Notifier tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.common import AuditResult
from tianshu.models.events import make_event
from tianshu.notifier.notifier import Notifier


class TestNotifierHandlers:
    @pytest.fixture
    def notifier(self, storage):
        return Notifier(storage=storage)

    async def test_handle_audit_completed(self, notifier, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.COMPLETED,
            summary="Done",
        )
        storage.save_memorial(memorial)

        event = make_event(
            "audit.completed",
            edict_id=edict.id,
            memorial_id=memorial.id,
        )
        await notifier.handle_audit_completed(event)

    async def test_handle_audit_completed_no_memorial(self, notifier):
        event = make_event("audit.completed", memorial_id="nonexistent")
        await notifier.handle_audit_completed(event)  # Should not raise

    async def test_handle_audit_completed_no_memorial_id(self, notifier):
        event = make_event("audit.completed")
        await notifier.handle_audit_completed(event)  # Should not raise

    async def test_handle_execution_failed(self, notifier, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.FAILED,
            error="Something broke",
        )
        storage.save_memorial(memorial)

        event = make_event(
            "execution.failed",
            edict_id=edict.id,
            memorial_id=memorial.id,
        )
        await notifier.handle_execution_failed(event)

    async def test_handle_execution_failed_no_memorial(self, notifier):
        event = make_event("execution.failed", memorial_id="nonexistent")
        await notifier.handle_execution_failed(event)

    async def test_register_unregister_ws(self, notifier):
        mock_ws = MagicMock()
        notifier.register_ws(mock_ws)
        assert mock_ws in notifier._ws_clients
        notifier.unregister_ws(mock_ws)
        assert mock_ws not in notifier._ws_clients

    async def test_send_webhook(self, notifier):
        # Just verify it doesn't raise (no real server)
        await notifier.send_webhook("http://localhost:99999/hook", {"test": True})
