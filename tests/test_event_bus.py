"""Tests for EventBus."""

import asyncio
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
        bus.on("test.event", handler, consumer_name="test.emit.v1")
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

        bus.on("test", h3, consumer_name="test.h3.v1", priority=300)
        bus.on("test", h1, consumer_name="test.h1.v1", priority=100)
        bus.on("test", h2, consumer_name="test.h2.v1", priority=200)

        await bus.emit(make_event("test"))
        assert order == ["h1", "h2", "h3"]

    async def test_handler_exception_isolated(self, bus):
        async def bad_handler(e):
            raise ValueError("boom")

        ok_handler = AsyncMock()
        bus.on("test", bad_handler, consumer_name="test.bad.v1", priority=1)
        bus.on("test", ok_handler, consumer_name="test.ok.v1", priority=2)

        await bus.emit(make_event("test"))
        ok_handler.assert_called_once()

    async def test_off_removes_handler(self, bus):
        handler = AsyncMock()
        bus.on("test", handler, consumer_name="test.removable.v1")
        bus.off("test", handler)
        await bus.emit(make_event("test"))
        handler.assert_not_called()

    async def test_no_handlers(self, bus):
        # Should not raise
        await bus.emit(make_event("unknown.event"))

    async def test_multiple_event_types(self, bus):
        h1 = AsyncMock()
        h2 = AsyncMock()
        bus.on("type.a", h1, consumer_name="test.shared.v1")
        bus.on("type.b", h2, consumer_name="test.shared.v1")

        await bus.emit(make_event("type.a"))
        h1.assert_called_once()
        h2.assert_not_called()

    @pytest.mark.parametrize("consumer_name", ["", " ", "\n"])
    def test_consumer_name_must_be_non_blank(self, bus, consumer_name):
        with pytest.raises(ValueError, match="consumer_name"):
            bus.on("test", AsyncMock(), consumer_name=consumer_name)

    def test_consumer_name_must_be_unique_per_event_type(self, bus):
        bus.on("test", AsyncMock(), consumer_name="test.unique.v1")

        with pytest.raises(ValueError, match="test.unique.v1"):
            bus.on("test", AsyncMock(), consumer_name="test.unique.v1")

    async def test_dispatch_reports_failures_and_continues_in_priority_order(self, bus):
        order: list[str] = []

        async def first(_event):
            order.append("first")

        async def failed(_event):
            order.append("failed")
            raise ValueError("boom")

        async def skipped(_event):
            order.append("skipped")

        async def last(_event):
            order.append("last")

        bus.on("test", last, consumer_name="test.last.v1", priority=400)
        bus.on("test", skipped, consumer_name="test.skipped.v1", priority=300)
        bus.on("test", failed, consumer_name="test.failed.v1", priority=200)
        bus.on("test", first, consumer_name="test.first.v1", priority=100)
        event = make_event("test")

        report = await bus.dispatch(event, skip_consumers={"test.skipped.v1"})

        assert report.event_id == event.event_id
        assert order == ["first", "failed", "last"]
        assert [(result.consumer_name, result.succeeded) for result in report.results] == [
            ("test.first.v1", True),
            ("test.failed.v1", False),
            ("test.last.v1", True),
        ]
        assert report.results[0].error is None
        assert isinstance(report.results[1].error, ValueError)
        assert str(report.results[1].error) == "boom"

    async def test_wildcard_consumer_receives_each_event_type(self, bus):
        handler = AsyncMock()
        bus.on("*", handler, consumer_name="test.all-events.v1")
        first = make_event("type.a")
        second = make_event("type.b")

        await bus.emit(first)
        await bus.emit(second)

        assert handler.await_args_list[0].args == (first,)
        assert handler.await_args_list[1].args == (second,)

    async def test_fire_is_best_effort_and_continues_after_failure(self, bus):
        delivered = asyncio.Event()

        async def failed(_event):
            raise ValueError("boom")

        async def later(_event):
            delivered.set()

        bus.on("test", failed, consumer_name="test.fire-failed.v1", priority=100)
        bus.on("test", later, consumer_name="test.fire-later.v1", priority=200)

        bus.fire(make_event("test"))

        await asyncio.wait_for(delivered.wait(), timeout=1)
