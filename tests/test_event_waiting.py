"""Tests for predicate-based EventBus waits."""

import asyncio

import pytest

from tests.support.waiting import EventProbe, await_event
from tianshu.bus.event_bus import EventBus
from tianshu.models.events import make_event


async def test_probe_matches_buffered_event_and_preserves_unmatched_events() -> None:
    bus = EventBus()
    async with EventProbe(bus, ("generation.ready",)) as probe:
        unmatched = make_event("generation.ready", payload={"generation_id": "rg-old"})
        matched = make_event("generation.ready", payload={"generation_id": "rg-new"})
        await bus.emit(unmatched)
        await bus.emit(matched)

        observed = await probe.wait_for(
            lambda event: event.payload["generation_id"] == "rg-new",
            timeout=0.5,
        )

        assert observed == matched
        assert probe.drain() == (unmatched,)
        assert probe.seen_types() == ("generation.ready", "generation.ready")

    assert bus.local_subscriber_count("generation.ready") == 0


async def test_await_event_subscribes_before_concurrent_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = EventBus()
    subscribed = asyncio.Event()
    original_on_local = bus.on_local

    def observed_on_local(event_type, handler, *, priority=100):
        original_on_local(event_type, handler, priority=priority)
        subscribed.set()

    monkeypatch.setattr(bus, "on_local", observed_on_local)
    expected = make_event(
        "generation.active",
        payload={"scope": "executor:keqing:pi"},
    )

    async def emit_after_subscription() -> None:
        await subscribed.wait()
        await bus.emit(expected)

    observed, _ = await asyncio.gather(
        await_event(
            bus,
            "generation.active",
            lambda event: event.payload.get("scope") == "executor:keqing:pi",
            timeout=0.5,
        ),
        emit_after_subscription(),
    )

    assert observed == expected
    assert bus.local_subscriber_count("generation.active") == 0


async def test_probe_timeout_is_only_a_failure_bound_and_close_is_idempotent() -> None:
    bus = EventBus()
    probe = EventProbe(bus, ("generation.failed",))

    with pytest.raises(TimeoutError):
        await probe.wait_for(lambda _event: True, timeout=0.01)

    probe.close()
    probe.close()
    assert bus.local_subscriber_count("generation.failed") == 0
    with pytest.raises(RuntimeError, match="closed"):
        await probe.wait_for(lambda _event: True, timeout=0.5)
