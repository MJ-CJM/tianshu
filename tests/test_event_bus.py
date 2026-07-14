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

    @pytest.mark.parametrize("wildcard_first", [True, False])
    def test_consumer_name_cannot_overlap_wildcard_and_exact(self, bus, wildcard_first):
        first_type, second_type = ("*", "test") if wildcard_first else ("test", "*")
        bus.on(first_type, AsyncMock(), consumer_name="test.overlap.v1")

        with pytest.raises(ValueError, match="test.overlap.v1"):
            bus.on(second_type, AsyncMock(), consumer_name="test.overlap.v1")

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

    async def test_dispatch_orders_wildcard_and_exact_consumers_by_priority(self, bus):
        order: list[str] = []

        async def exact_first(_event):
            order.append("exact-first")

        async def wildcard(_event):
            order.append("wildcard")

        async def exact_last(_event):
            order.append("exact-last")

        bus.on("test", exact_last, consumer_name="test.exact-last.v1", priority=300)
        bus.on("*", wildcard, consumer_name="test.wildcard.v1", priority=200)
        bus.on("test", exact_first, consumer_name="test.exact-first.v1", priority=100)

        report = await bus.dispatch(make_event("test"))

        assert order == ["exact-first", "wildcard", "exact-last"]
        assert [result.consumer_name for result in report.results] == [
            "test.exact-first.v1",
            "test.wildcard.v1",
            "test.exact-last.v1",
        ]

    async def test_dispatch_skips_wildcard_consumer_by_exact_name(self, bus):
        wildcard = AsyncMock()
        exact = AsyncMock()
        bus.on("*", wildcard, consumer_name="test.wildcard.v1", priority=10)
        bus.on("test", exact, consumer_name="test.exact.v1", priority=20)

        report = await bus.dispatch(
            make_event("test"),
            skip_consumers={"test.wildcard.v1"},
        )

        wildcard.assert_not_awaited()
        exact.assert_awaited_once()
        assert [result.consumer_name for result in report.results] == ["test.exact.v1"]

    async def test_dispatch_excludes_local_subscribers(self, bus):
        local = AsyncMock()
        bus.on_local("test", local, priority=10)

        report = await bus.dispatch(make_event("test"))

        local.assert_not_awaited()
        assert report.results == ()

    async def test_emit_isolates_and_cleans_up_multiple_local_subscribers(self, bus):
        order: list[str] = []

        async def failed(_event):
            order.append("failed")
            raise ValueError("boom")

        async def first_stream(_event):
            order.append("first-stream")

        async def second_stream(_event):
            order.append("second-stream")

        bus.on_local("profile.synthesis.completed", second_stream, priority=20)
        bus.on_local("profile.synthesis.completed", failed, priority=10)
        bus.on_local("profile.synthesis.completed", first_stream, priority=20)

        await bus.emit(make_event("profile.synthesis.completed"))
        bus.off_local("profile.synthesis.completed", first_stream)
        await bus.emit(make_event("profile.synthesis.completed"))

        assert order == [
            "failed",
            "second-stream",
            "first-stream",
            "failed",
            "second-stream",
        ]

    async def test_fire_invokes_local_subscribers(self, bus):
        delivered = asyncio.Event()

        async def local(_event):
            delivered.set()

        bus.on_local("test", local)

        bus.fire(make_event("test"))

        await asyncio.wait_for(delivered.wait(), timeout=1)

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
