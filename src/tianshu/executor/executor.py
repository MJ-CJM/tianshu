"""Executor — event-driven orchestration of Agent execution with DAG support."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager
from tianshu.dag.models import DAGExecution
from tianshu.executor.hooks import HookRegistry, HookType
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.models.memorial import Memorial
from tianshu.models.plan import Plan
from tianshu.persona.model import DEFAULT_EXECUTOR_ID
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class Executor:
    """Subscribes to plan.completed events and runs Agent execution.

    For multi-task plans, creates a DAG and delegates to DAGScheduler.
    For single-task or no-plan edicts, runs the agent directly.
    """

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        config_manager: ConfigManager,
        hook_registry: HookRegistry,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._config_manager = config_manager
        self._hooks = hook_registry
        self._agent = None  # set via set_agent()
        self._dag_scheduler = None  # set via set_dag_scheduler()
        self._lane_manager = None  # set via set_lane_manager()
        self._persona_loader = None  # set via set_persona_loader()
        self._running_tasks: set[asyncio.Task] = set()

    def set_agent(self, agent: object) -> None:
        self._agent = agent

    def set_dag_scheduler(self, scheduler: object) -> None:
        self._dag_scheduler = scheduler

    def set_lane_manager(self, lane_manager: object) -> None:
        self._lane_manager = lane_manager

    def set_persona_loader(self, persona_loader: object) -> None:
        self._persona_loader = persona_loader

    @property
    def running_tasks(self) -> set[asyncio.Task]:
        return self._running_tasks

    async def handle_plan_completed(self, event: EventEnvelope) -> None:
        """EventBus handler for plan.completed."""
        edict_id = event.edict_id
        if not edict_id:
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            logger.error("Executor: edict %s not found", edict_id)
            return

        plan = None
        if "plan" in event.payload:
            plan = Plan.model_validate(event.payload["plan"])

        # Recover memorial created at submission time
        memorial_id = event.memorial_id
        memorial = self._storage.get_memorial(memorial_id) if memorial_id else None

        # Multi-task plan → DAG execution
        if plan and len(plan.tasks) > 1 and self._dag_scheduler:
            logger.debug(
                "[EXEC] Edict %s: using DAG path, %d tasks, max_concurrency=%d",
                edict.id, len(plan.tasks), edict.runtime.max_concurrency,
            )
            task = asyncio.create_task(self._execute_dag(edict, plan, memorial=memorial))
        else:
            task = asyncio.create_task(self.execute_edict(edict, plan, memorial=memorial))

        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

    async def _execute_dag(
        self, edict: Edict, plan: Plan, memorial: Memorial | None = None,
    ) -> None:
        """Create DAG from plan and run via DAGScheduler."""
        max_concurrency = edict.runtime.max_concurrency
        execution = plan.to_dag(edict.id, max_concurrency=max_concurrency)

        # Reuse the original memorial as root (update from PLANNING → RUNNING)
        if memorial:
            root_memorial = memorial
            root_memorial.status = TaskStatus.RUNNING
            root_memorial.started_at = datetime.now(UTC)
            self._storage.update_memorial(root_memorial)
        else:
            root_memorial = Memorial(
                edict_id=edict.id,
                instruction=edict.goal,
                status=TaskStatus.RUNNING,
                started_at=datetime.now(UTC),
            )
            self._storage.save_memorial(root_memorial)
        execution.root_memorial_id = root_memorial.id

        # Persist DAG
        self._storage.save_dag_execution(execution)

        # Setup lanes
        session_lane = None
        global_lane = None
        if self._lane_manager:
            session_lane = self._lane_manager.get_session_lane(
                edict.id, max_concurrency,
            )
            global_lane = self._lane_manager.global_lane
            self._dag_scheduler._session_lane = session_lane
            self._dag_scheduler._global_lane = global_lane

        try:
            await self._dag_scheduler.run(edict, execution)
        finally:
            if self._lane_manager:
                self._lane_manager.remove_session(edict.id)

    async def execute_edict(
        self,
        edict: Edict,
        plan: Plan | None = None,
        memorial: Memorial | None = None,
        history: list[dict] | None = None,
        user_content: str | None = None,
    ) -> None:
        """Run the agent for an edict, managing memorial lifecycle.

        This is the single-task fast path (backward compatible with Phase 2).
        """
        if not self._agent:
            logger.error("Executor: no agent set")
            return

        if memorial is None:
            memorial = Memorial(
                edict_id=edict.id,
                instruction=edict.goal,
                status=TaskStatus.SUBMITTED,
            )
            self._storage.save_memorial(memorial)

        # Set persona_id: plan assignment > edict assignment > default
        if not memorial.persona_id:
            plan_persona = None
            if plan and plan.tasks:
                plan_persona = plan.tasks[0].assigned_official
            memorial.persona_id = (
                edict.assigned_persona_id
                or plan_persona
                or DEFAULT_EXECUTOR_ID
            )
        logger.debug(
            "[EXEC] Edict %s: start execution, persona=%s, timeout=%ds, max_iter=%d",
            edict.id, memorial.persona_id,
            edict.runtime.timeout_seconds, edict.runtime.max_iterations,
        )
        memorial.status = TaskStatus.RUNNING
        memorial.started_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

        await self._bus.emit(
            make_event(
                "execution.started",
                edict_id=edict.id,
                memorial_id=memorial.id,
                producer="executor",
                payload={"memorial_id": memorial.id},
            )
        )

        # Session start hook
        await self._hooks.run(
            HookType.SESSION_START,
            edict=edict,
            memorial=memorial,
        )

        # Before agent start hook
        hook_result = await self._hooks.run(
            HookType.BEFORE_AGENT_START,
            edict=edict,
            memorial=memorial,
            plan=plan,
        )
        if hook_result.block:
            memorial.status = TaskStatus.FAILED
            memorial.error = f"Blocked by hook: {hook_result.reason}"
            memorial.completed_at = datetime.now(UTC)
            self._storage.update_memorial(memorial)
            return

        # Merge memory history from hook into execution history
        if hook_result.modified_args:
            memory_history = hook_result.modified_args.get("memory_history")
            if memory_history:
                history = (history or []) + memory_history

        # Resolve persona object for Agent (enables llm_config_name passthrough)
        persona = None
        if self._persona_loader and memorial.persona_id:
            persona = self._persona_loader.get(memorial.persona_id)

        def on_event(event: dict) -> None:
            self._storage.append_event(edict.id, memorial.id, event["type"], event)

        try:
            timeout = edict.runtime.timeout_seconds
            result = await asyncio.wait_for(
                self._agent.execute(
                    edict,
                    on_event=on_event,
                    history=history,
                    user_content=user_content,
                    persona=persona,
                ),
                timeout=timeout,
            )
            memorial.status = result.status
            memorial.summary = result.summary
            memorial.result = result.result
            memorial.usage = result.usage
            memorial.error = result.error
            event_type = {
                TaskStatus.COMPLETED: "execution.completed",
                TaskStatus.FAILED: "execution.failed",
                TaskStatus.CANCELLED: "execution.cancelled",
            }.get(result.status, "execution.failed")

        except asyncio.CancelledError:
            memorial.status = TaskStatus.CANCELLED
            memorial.error = "Task was cancelled"
            event_type = "execution.cancelled"
            raise
        except asyncio.TimeoutError:
            memorial.status = TaskStatus.FAILED
            memorial.error = f"Execution timed out after {edict.runtime.timeout_seconds}s"
            event_type = "execution.failed"
        except Exception as e:
            logger.exception("Unexpected error executing edict %s", edict.id)
            memorial.status = TaskStatus.FAILED
            memorial.error = str(e)
            event_type = "execution.failed"
        finally:
            memorial.completed_at = datetime.now(UTC)
            logger.debug(
                "[EXEC] Edict %s: finished status=%s, error=%s",
                edict.id, memorial.status.value, memorial.error,
            )

            # Save memorial BEFORE emitting event so auditor reads fresh data
            try:
                self._storage.update_memorial(memorial)
            except Exception:
                logger.exception("Failed to update memorial %s", memorial.id)

            # Emit event — auditor may modify memorial status (e.g. needs_review)
            try:
                await self._bus.emit(
                    make_event(
                        event_type,
                        edict_id=edict.id,
                        memorial_id=memorial.id,
                        producer="executor",
                        payload={
                            "status": memorial.status.value,
                            "error": memorial.error,
                        },
                    )
                )
            except Exception:
                logger.exception("Failed to emit %s for memorial %s", event_type, memorial.id)

            # Agent end hook
            await self._hooks.run(
                HookType.AGENT_END,
                edict=edict,
                memorial=memorial,
            )

            # Session end hook
            await self._hooks.run(
                HookType.SESSION_END,
                edict=edict,
                memorial=memorial,
                usage=memorial.usage,
            )

            # Auto-retry: if failed and retry_limit not exhausted
            if (
                memorial.status == TaskStatus.FAILED
                and edict.runtime.retry_limit > 0
                and memorial.attempt < edict.runtime.retry_limit
            ):
                logger.info(
                    "Auto-retry edict %s: attempt %d/%d",
                    edict.id, memorial.attempt + 1, edict.runtime.retry_limit,
                )
                retry_memorial = Memorial(
                    edict_id=edict.id,
                    instruction=memorial.instruction or edict.goal,
                    attempt=memorial.attempt + 1,
                    parent_memorial_id=memorial.id,
                )
                self._storage.save_memorial(retry_memorial)
                retry_task = asyncio.create_task(
                    self.execute_edict(edict, memorial=retry_memorial)
                )
                self._running_tasks.add(retry_task)
                retry_task.add_done_callback(self._running_tasks.discard)

    async def cancel_dag(self, dag_id: str) -> list[str]:
        """Cancel a running DAG execution."""
        from tianshu.executor.cancel import CascadeCanceller

        execution = self._storage.get_dag_execution(dag_id)
        if not execution:
            raise ValueError(f"DAG execution '{dag_id}' not found")
        if execution.status not in ("pending", "running"):
            raise ValueError(f"DAG execution is already {execution.status}")

        worker_pool = self._dag_scheduler._pool if self._dag_scheduler else None
        if not worker_pool:
            raise ValueError("No worker pool available")

        canceller = CascadeCanceller(self._storage, worker_pool)
        cancelled = await canceller.cancel(execution)

        await self._bus.emit(make_event(
            "dag.cancelled",
            edict_id=execution.edict_id,
            producer="executor",
            payload={"dag_id": dag_id, "cancelled_nodes": cancelled},
        ))

        return cancelled

    async def retry_dag(
        self, dag_id: str, from_node_ids: list[str] | None = None,
    ) -> list[str]:
        """Retry failed nodes in a DAG execution."""
        from tianshu.executor.retry import PartialRetrier

        execution = self._storage.get_dag_execution(dag_id)
        if not execution:
            raise ValueError(f"DAG execution '{dag_id}' not found")
        if execution.status not in ("failed", "cancelled"):
            raise ValueError(f"DAG execution must be failed/cancelled to retry, got {execution.status}")

        retrier = PartialRetrier(self._storage)
        reset_ids = retrier.prepare_retry(execution, from_node_ids)

        if not reset_ids:
            return []

        # Reload execution with fresh state
        execution = self._storage.get_dag_execution(dag_id)
        edict = self._storage.get_edict(execution.edict_id)
        if not edict or not self._dag_scheduler:
            raise ValueError("Cannot retry: missing edict or DAG scheduler")

        # Re-run the DAG scheduler
        task = asyncio.create_task(self._dag_scheduler.run(edict, execution))
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

        return reset_ids

    async def shutdown(self) -> None:
        for task in list(self._running_tasks):
            task.cancel()
        await asyncio.gather(*self._running_tasks, return_exceptions=True)
