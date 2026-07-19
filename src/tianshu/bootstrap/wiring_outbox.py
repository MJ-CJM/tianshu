"""Durable outbox repository, dispatcher, and lifecycle wiring."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI

from tianshu.application.outbox import OutboxDispatcher, OutboxLifecycle
from tianshu.config import TianshuSettings
from tianshu.storage.outbox_repo import OutboxRepository


async def wire_outbox(app: FastAPI, settings: TianshuSettings) -> None:
    """Construct and start the single worker after durable consumers exist."""
    repository = OutboxRepository(app.state.storage.unit_of_work)
    dispatcher = OutboxDispatcher(
        repository,
        app.state.event_bus,
        owner_id=f"outbox-{uuid4().hex}",
        lease_seconds=settings.outbox_lease_seconds,
        base_backoff_seconds=settings.durable_retry_base_seconds,
        max_backoff_seconds=settings.durable_retry_max_seconds,
        poll_interval_seconds=settings.outbox_poll_interval_seconds,
        shutdown_timeout_seconds=settings.outbox_shutdown_timeout_seconds,
    )
    lifecycle = OutboxLifecycle(dispatcher)

    app.state.outbox_repository = repository
    app.state.outbox_dispatcher = dispatcher
    app.state.outbox_lifecycle = lifecycle
    await lifecycle.start()
    app.state.outbox_task = lifecycle.task


__all__ = ["wire_outbox"]
