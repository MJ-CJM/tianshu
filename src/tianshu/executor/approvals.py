"""Approval (decree) management — T3 real-time approval via asyncio.Event."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from tianshu.bus.event_bus import EventBus
from tianshu.models.common import TaskStatus
from tianshu.models.decree import Decree
from tianshu.models.edict import Edict
from tianshu.models.events import make_event
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT = 300.0  # 5 minutes


class ApprovalManager:
    """Manages approval workflow for memorials that need human review."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, Decree] = {}

    async def wait_for_approval(
        self,
        memorial_id: str,
        tool_name: str,
    ) -> Decree | None:
        """Block until a decree is submitted for this memorial, or timeout."""
        evt = asyncio.Event()
        self._pending[memorial_id] = evt

        logger.info(
            "Waiting for approval on memorial %s (tool: %s)",
            memorial_id,
            tool_name,
        )

        try:
            await asyncio.wait_for(evt.wait(), timeout=APPROVAL_TIMEOUT)
            return self._results.pop(memorial_id, None)
        except asyncio.TimeoutError:
            logger.warning(
                "Approval timeout for memorial %s, auto-rejecting",
                memorial_id,
            )
            return None
        finally:
            self._pending.pop(memorial_id, None)

    async def submit_decree(self, decree: Decree) -> None:
        """Process a decree and update memorial status accordingly."""
        memorial = self._storage.get_memorial(decree.memorial_id)
        if not memorial:
            raise ValueError(f"Memorial '{decree.memorial_id}' not found")

        self._storage.save_decree(decree)

        if decree.action == "approve":
            await self._handle_approve(memorial, decree)
        elif decree.action == "reject":
            await self._handle_reject(memorial, decree)
        elif decree.action == "retry":
            await self._handle_retry(memorial, decree)
        elif decree.action == "amend":
            await self._handle_amend(memorial, decree)
        elif decree.action == "cancel":
            await self._handle_cancel(memorial, decree)

        # Wake up any waiting approval
        evt = self._pending.get(decree.memorial_id)
        if evt:
            self._results[decree.memorial_id] = decree
            evt.set()

    async def _handle_approve(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "approved"
        memorial.status = TaskStatus.COMPLETED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.approved",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id, "comment": decree.comment},
            )
        )

    async def _handle_reject(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.error = decree.comment or "Rejected by reviewer"
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.rejected",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id, "comment": decree.comment},
            )
        )

    async def _handle_retry(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

        new_memorial = Memorial(
            edict_id=memorial.edict_id,
            instruction=memorial.instruction,
            attempt=memorial.attempt + 1,
            parent_memorial_id=memorial.id,
        )
        self._storage.save_memorial(new_memorial)

        await self._bus.emit(
            make_event(
                "decree.retry",
                edict_id=memorial.edict_id,
                memorial_id=new_memorial.id,
                producer="approval_manager",
                payload={
                    "decree_id": decree.id,
                    "original_memorial_id": memorial.id,
                    "attempt": new_memorial.attempt,
                },
            )
        )

    async def _handle_amend(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

        if decree.amended_goal:
            new_edict = Edict(
                goal=decree.amended_goal,
                title=decree.amended_goal[:20] + "..." if len(decree.amended_goal) > 20 else decree.amended_goal,
                context=f"Amended from memorial {memorial.id}",
            )
            self._storage.save_edict(new_edict)
            await self._bus.emit(
                make_event(
                    "edict.submitted",
                    edict_id=new_edict.id,
                    producer="approval_manager",
                    payload={"goal": new_edict.goal, "amended_from": memorial.id},
                )
            )

    async def _handle_cancel(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.CANCELLED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.cancelled",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id},
            )
        )
