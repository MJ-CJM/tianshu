"""WorkerPool — asyncio semaphore-based concurrency management."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class WorkItem:
    id: str
    dag_execution_id: str
    node_id: str
    description: str
    coro_factory: Callable[[], Awaitable[None]]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class WorkerStatus:
    active_count: int
    max_concurrency: int
    pending_count: int
    completed_count: int
    failed_count: int


class WorkerPool:
    """Manages concurrent execution slots via asyncio.Semaphore."""

    def __init__(self, max_concurrency: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_concurrency = max_concurrency
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._completed_count = 0
        self._failed_count = 0

    @property
    def active_count(self) -> int:
        return len(self._active_tasks)

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def status(self) -> WorkerStatus:
        return WorkerStatus(
            active_count=len(self._active_tasks),
            max_concurrency=self._max_concurrency,
            pending_count=0,
            completed_count=self._completed_count,
            failed_count=self._failed_count,
        )

    async def submit(
        self,
        item: WorkItem,
        on_complete: Callable[[str, str | None], Awaitable[None]] | None = None,
    ) -> asyncio.Task:
        """Submit a work item. Blocks until a slot is available."""
        task = asyncio.create_task(
            self._run(item, on_complete),
            name=f"worker-{item.node_id}",
        )
        self._active_tasks[item.id] = task
        task.add_done_callback(lambda t: self._active_tasks.pop(item.id, None))
        return task

    async def _run(
        self,
        item: WorkItem,
        on_complete: Callable[[str, str | None], Awaitable[None]] | None,
    ) -> None:
        async with self._semaphore:
            error: str | None = None
            try:
                await item.coro_factory()
            except asyncio.CancelledError:
                error = "Cancelled"
                raise
            except Exception as e:
                error = str(e)
                self._failed_count += 1
                logger.exception("Worker %s failed: %s", item.node_id, e)
            else:
                self._completed_count += 1
            finally:
                if on_complete:
                    try:
                        await on_complete(item.node_id, error)
                    except Exception:
                        logger.exception("on_complete callback failed for %s", item.node_id)

    async def cancel(self, work_id: str) -> bool:
        """Cancel a running work item."""
        task = self._active_tasks.get(work_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def list_active(self) -> list[str]:
        """Return IDs of active work items."""
        return list(self._active_tasks.keys())

    async def shutdown(self) -> None:
        """Cancel all active tasks and wait."""
        for task in list(self._active_tasks.values()):
            task.cancel()
        if self._active_tasks:
            await asyncio.gather(
                *self._active_tasks.values(), return_exceptions=True
            )
        self._active_tasks.clear()
