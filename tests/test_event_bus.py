"""Tests for EventBus."""

from unittest.mock import AsyncMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models.events import make_event


class TestEventBus:
    @pytest.fixture
    def bus(self):
        return EventBus()

    async def test_emit_calls_handler(self, bus):
        handler = AsyncMock()
        bus.on("test.event", handler)
        event = make_event("test.event", edict_id="e1")
        await bus.emit(event)
        handler.assert_called_once_with(event)

    async def test_priority_ordering(self, bus):
        order = []
        async def h1(e):
            order.append("h1")
        async def h2(e):
            order.append("h2")
        async def h3(e):
            order.append("h3")

        bus.on("test", h3, priority=300)
        bus.on("test", h1, priority=100)
        bus.on("test", h2, priority=200)

        await bus.emit(make_event("test"))
        assert order == ["h1", "h2", "h3"]

    async def test_handler_exception_isolated(self, bus):
        async def bad_handler(e):
            raise ValueError("boom")

        ok_handler = AsyncMock()
        bus.on("test", bad_handler, priority=1)
        bus.on("test", ok_handler, priority=2)

        await bus.emit(make_event("test"))
        ok_handler.assert_called_once()

    async def test_off_removes_handler(self, bus):
        handler = AsyncMock()
        bus.on("test", handler)
        bus.off("test", handler)
        await bus.emit(make_event("test"))
        handler.assert_not_called()

    async def test_no_handlers(self, bus):
        # Should not raise
        await bus.emit(make_event("unknown.event"))

    async def test_multiple_event_types(self, bus):
        h1 = AsyncMock()
        h2 = AsyncMock()
        bus.on("type.a", h1)
        bus.on("type.b", h2)

        await bus.emit(make_event("type.a"))
        h1.assert_called_once()
        h2.assert_not_called()

    async def test_persists_to_storage(self, storage):
        from tianshu.models import Edict
        edict = Edict(goal="test")
        storage.save_edict(edict)

        bus = EventBus(storage=storage)
        handler = AsyncMock()
        bus.on("edict.submitted", handler)

        event = make_event("edict.submitted", edict_id=edict.id)
        await bus.emit(event)

        events = storage.get_events(edict.id)
        assert len(events) == 1
        assert events[0]["event_type"] == "edict.submitted"
