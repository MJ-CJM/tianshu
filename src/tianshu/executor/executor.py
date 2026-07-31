"""Executor — event-driven orchestration of Agent execution with DAG support."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from contextlib import nullcontext
from copy import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.application.run_execution import ManagedExecutionProjection
from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager
from tianshu.dag import validate_dag_structure
from tianshu.executor.adapters import (
    DelegatingExecutorAdapter,
    ExecutionMode,
    ExecutorAdapterRegistry,
    PreparedExecutor,
    UnsupportedExecutorMode,
)
from tianshu.executor.capabilities import (
    MandatoryCapabilityMismatch,
    claude_code_manifest,
    codex_manifest,
    native_manifest,
    opencode_manifest,
    pi_manifest,
)
from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.executor.managed_tools import ManagedRunSuspended
from tianshu.executor.workspace_context import BoundWorkspace, bind_workspace
from tianshu.executor.workspace_runtime import (
    WorkspaceContractError,
    WorkspaceRuntime,
    WorkspaceTerminalEvidence,
    complete_workspace_lifecycle,
    shield_workspace_lifecycle,
)
from tianshu.executor.workspace_service import WorkspaceError, WorkspaceService
from tianshu.kernel.hooks import HookRegistry, HookType
from tianshu.models.canonical import RedactedError
from tianshu.models.common import TaskStatus
from tianshu.models.dag import DAGExecution
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.models.failure import resolve_failure_reason
from tianshu.models.governance_contract import LegacyEdictGovernanceMapper
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
        session_rule_store: object | None = None,
        execution_gateway: ExecutionGateway | None = None,
        workspace_service: WorkspaceService | None = None,
        workspace_sources: Mapping[str, Path] | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._config_manager = config_manager
        self._hooks = hook_registry
        self._session_rule_store = session_rule_store
        self._execution_gateway = execution_gateway or ExecutionGateway()
        self._workspace_runtime = WorkspaceRuntime(
            storage=storage,
            service=workspace_service,
            workspace_sources=workspace_sources,
        )
        self._agent = None  # set via set_agent()
        self._dag_scheduler = None  # set via set_dag_scheduler()
        self._lane_manager = None  # set via set_lane_manager()
        self._persona_loader = None  # set via set_persona_loader()
        self._official_selector = None  # set via set_official_selector()
        self._universe_manager = None  # set via set_universe_manager()
        self._running_tasks: set[asyncio.Task] = set()
        self._orchestrator_ctx = None  # set via set_orchestrator_context()
        self._managed_run_ingress: Any | None = None
        from tianshu.application.fenced_run_completion import FencedRunCompletion

        self._fenced_completion: Any = FencedRunCompletion(
            storage.unit_of_work,
            storage.attempt_repo,
        )
        # 迭代 3.5「客卿」:外部 CLI 执行器(runtime.executor=keqing:<agent> 时路由)
        from tianshu.executor.keqing import KeqingExecutor
        from tianshu.executor.keqing.session_executor import KeqingSessionExecutor

        # per-客卿默认模型:敕令未指定 executor_model 时,按客卿回退到治理默认(空则交客卿自身默认)。
        def _keqing_default_model(backend: str) -> str | None:
            return self._config_manager.agent_config.keqing_default_models.get(backend) or None

        self._keqing = KeqingExecutor(
            execution_gateway=self._execution_gateway,
            default_model_provider=_keqing_default_model,
        )
        # pi 走 RPC 会话档(follow_up 验收回灌);单发 PiAdapter 仍在 _REGISTRY 作降级 + grant 校验。
        self._keqing_session = KeqingSessionExecutor(
            execution_gateway=self._execution_gateway,
            default_model_provider=_keqing_default_model,
        )
        self._adapter_registry = ExecutorAdapterRegistry(
            (
                DelegatingExecutorAdapter(
                    adapter_id="keqing:claude-code",
                    manifest=claude_code_manifest(),
                    delegate=self._keqing,
                ),
                DelegatingExecutorAdapter(
                    adapter_id="keqing:codex",
                    manifest=codex_manifest(),
                    delegate=self._keqing,
                ),
                DelegatingExecutorAdapter(
                    adapter_id="keqing:pi",
                    manifest=pi_manifest(),
                    delegate=self._keqing_session,
                ),
                DelegatingExecutorAdapter(
                    adapter_id="keqing:opencode",
                    manifest=opencode_manifest(),
                    delegate=self._keqing,
                ),
            )
        )

    def set_agent(self, agent: object) -> None:
        self._agent = agent
        self._adapter_registry.replace(
            DelegatingExecutorAdapter(
                adapter_id="native",
                manifest=native_manifest(),
                delegate=agent,
            )
        )

    def set_dag_scheduler(self, scheduler: object) -> None:
        self._dag_scheduler = scheduler

    def set_lane_manager(self, lane_manager: object) -> None:
        self._lane_manager = lane_manager

    def set_persona_loader(self, persona_loader: object) -> None:
        self._persona_loader = persona_loader

    def set_official_selector(self, selector: object) -> None:
        """官员拣选器（persona/selector.py）；规划失败时按旨意关键词就地选官。"""
        self._official_selector = selector

    def set_universe_manager(self, manager: object) -> None:
        self._universe_manager = manager

    def set_orchestrator_context(self, orch_ctx: object) -> None:
        """注入 orchestrator 依赖（agent/storage/bus/llms/...）。"""
        self._orchestrator_ctx = orch_ctx

    def set_managed_run_ingress(self, ingress: Any) -> None:
        self._managed_run_ingress = ingress

    def set_fenced_completion(self, completion: Any) -> None:
        self._fenced_completion = completion

    @property
    def managed_run_ingress(self) -> Any | None:
        return self._managed_run_ingress

    @property
    def running_tasks(self) -> set[asyncio.Task]:
        return self._running_tasks

    async def execute_attempt(
        self,
        authority: AttemptAuthority,
        plan: Plan,
    ) -> ManagedExecutionProjection:
        """Execute one claimed root while deferring terminal truth to fencing."""
        memorial = self._storage.get_memorial(authority.memorial_id)
        if memorial is None or memorial.dag_node_id is not None:
            raise RuntimeError("managed execution root is unavailable")
        edict = self._storage.get_edict(memorial.edict_id)
        if edict is None:
            raise RuntimeError("managed execution edict is unavailable")
        if edict.acceptance is not None and self._orchestrator_ctx is not None:
            await self._execute_outer_loop(
                edict,
                memorial,
                _defer_terminal=True,
                attempt_authority=authority,
            )
        elif plan and len(plan.tasks) > 1 and self._dag_scheduler:
            await self._execute_dag(edict, plan, memorial=memorial, _defer_terminal=True)
        else:
            await self.execute_edict(
                edict,
                plan,
                memorial=memorial,
                _defer_terminal=True,
            )
        error = None
        if memorial.status is not TaskStatus.COMPLETED:
            # 服务端留真实失败原因(前端只收脱敏摘要);memorial.error 现由执行器透传具体原因。
            logger.warning(
                "[managed] edict %s not completed: reason=%s error=%s",
                edict.id,
                memorial.failure_reason,
                memorial.error,
            )
            retryable = memorial.failure_reason in {
                "provider_timeout",
                "provider_connection_error",
                "transient_execution_error",
            }
            error = RedactedError(
                code=memorial.failure_reason or "execution_failed",
                message="Managed execution failed",
                retryable=retryable,
                details_hash=(
                    hashlib.sha256((memorial.error or "execution_failed").encode()).hexdigest()
                ),
            )
        return ManagedExecutionProjection(
            status=memorial.status,
            summary=memorial.summary,
            result=memorial.result,
            final_output=memorial.final_output,
            usage=memorial.usage,
            reasoning_content=memorial.reasoning_content,
            failure_reason=resolve_failure_reason(
                memorial.status.value,
                memorial.error,
                memorial.failure_reason,
            ),
            error=error,
        )

    async def handle_plan_completed(self, event: EventEnvelope) -> None:
        """Adopt safely bound upgrade-time work into durable dispatch."""
        edict_id = event.edict_id
        if not edict_id:
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            logger.error("Executor: edict %s not found", edict_id)
            return

        ingress = self._managed_run_ingress
        if ingress is None:
            raise RuntimeError("managed run ingress is not configured")
        await ingress.adopt_legacy(event)

    async def handle_resume(self, event: EventEnvelope) -> None:
        """EventBus handler for edict.resume —— 续跑被 sweeper 判为孤儿的长任务（Multica 借鉴 #1）。

        仅长任务 outer loop（edict.acceptance 不为 None）可续跑：orchestrator 会
        _load_checkpoint 从断点恢复；无 checkpoint 时从头跑一遍（幂等）。
        """
        edict_id = event.edict_id
        if not edict_id:
            return
        edict = self._storage.get_edict(edict_id)
        if not edict or not event.memorial_id:
            logger.error("Resume: edict/memorial not found for %s", edict_id)
            return
        if edict.acceptance is None or self._orchestrator_ctx is None:
            logger.warning(
                "Resume ignored for edict %s: no acceptance / orchestrator ctx",
                edict_id,
            )
            return
        ingress = self._managed_run_ingress
        if ingress is None:
            raise RuntimeError("managed run ingress is not configured")
        await ingress.adopt_legacy(event)

    async def _execute_dag(
        self,
        edict: Edict,
        plan: Plan,
        memorial: Memorial | None = None,
        *,
        _defer_terminal: bool = False,
    ) -> None:
        """Create DAG from plan and run via DAGScheduler."""
        max_concurrency = edict.runtime.max_concurrency
        execution = plan.to_dag(edict.id, max_concurrency=max_concurrency)

        # Reuse the original memorial as root. Capability resolution must happen
        # before the scheduler can create a workspace or invoke any executor.
        if memorial:
            root_memorial = memorial
        else:
            root_memorial = Memorial(
                edict_id=edict.id,
                instruction=edict.goal,
                status=TaskStatus.SUBMITTED,
            )
            self._storage.save_memorial(root_memorial)
        execution.root_memorial_id = root_memorial.id
        try:
            validate_dag_structure(execution.nodes)
        except ValueError as exc:
            await self._reject_invalid_dag(
                edict,
                execution,
                root_memorial,
                exc,
                _defer_terminal=_defer_terminal,
            )
            return

        try:
            prepared_executor, bound_workspace = await self._prepare_runtime_or_cancel(
                edict,
                root_memorial,
                execution_mode="dag",
                dag_id=execution.id,
                defer_root_terminal=_defer_terminal,
            )
        except MandatoryCapabilityMismatch as exc:
            await self._reject_capability_mismatch(
                edict, root_memorial, exc, defer_root_terminal=_defer_terminal
            )
            return
        except UnsupportedExecutorMode as exc:
            await self._reject_executor_mode(
                edict, root_memorial, exc, defer_root_terminal=_defer_terminal
            )
            return
        except (WorkspaceContractError, WorkspaceError) as exc:
            await self._reject_workspace_runtime(
                edict, root_memorial, exc, defer_root_terminal=_defer_terminal
            )
            return

        await self._run_prepared_dag(
            edict,
            execution,
            root_memorial,
            prepared_executor,
            bound_workspace,
            save_execution=True,
            _defer_terminal=_defer_terminal,
        )

    async def _reject_invalid_dag(
        self,
        edict: Edict,
        execution: DAGExecution,
        root_memorial: Memorial,
        exc: ValueError,
        *,
        _defer_terminal: bool = False,
    ) -> None:
        """Persist and publish one failed terminal before any runtime is prepared."""
        error = f"DAG validation failed: {exc}"
        completed_at = datetime.now(UTC)
        root_memorial.status = TaskStatus.FAILED
        root_memorial.error = error
        root_memorial.completed_at = completed_at
        execution.status = "failed"
        execution.completed_at = completed_at
        if not _defer_terminal:
            self._storage.update_memorial(root_memorial)
        self._storage.save_failed_dag_execution(execution)
        if not _defer_terminal:
            await self._bus.emit(
                make_event(
                    "execution.failed",
                    edict_id=edict.id,
                    memorial_id=root_memorial.id,
                    producer="executor",
                    payload={
                        "dag_id": execution.id,
                        "status": root_memorial.status.value,
                        "error": error,
                        "failure_reason": resolve_failure_reason(
                            root_memorial.status.value,
                            error,
                            root_memorial.failure_reason,
                        ),
                    },
                )
            )

    async def _run_prepared_dag(
        self,
        edict: Edict,
        execution: DAGExecution,
        root_memorial: Memorial,
        prepared_executor: PreparedExecutor,
        bound_workspace: BoundWorkspace | None,
        *,
        save_execution: bool,
        _defer_terminal: bool = False,
    ) -> None:
        """Run one governed DAG attempt under its root workspace lease."""
        cancelled_error: asyncio.CancelledError | None = None
        terminal_evidence = WorkspaceTerminalEvidence()
        # 根终态先只在内存里定形，finalize（捕获变更集）之后才落库——与 execute_edict /
        # _execute_outer_loop 同一条不变量：终态可见 ⟹ 变更集立即可读。
        terminal_root: Memorial | None = None
        persist_terminal = False
        binding = bind_workspace(bound_workspace) if bound_workspace is not None else nullcontext()
        with binding:
            try:
                root_memorial.status = TaskStatus.RUNNING
                root_memorial.started_at = datetime.now(UTC)
                self._stamp_universe(root_memorial)
                self._storage.update_memorial(root_memorial)
                if save_execution:
                    self._storage.save_dag_execution(execution)

                if self._lane_manager:
                    self._dag_scheduler._session_lane = self._lane_manager.get_session_lane(
                        edict.id,
                        execution.max_concurrency,
                    )
                    self._dag_scheduler._global_lane = self._lane_manager.global_lane

                terminal_root = await self._dag_scheduler.run(
                    edict,
                    execution,
                    prepared_executor=prepared_executor,
                    persist_root_terminal=False,
                )
                persist_terminal = terminal_root is not None
            except asyncio.CancelledError as exc:
                cancelled_error = exc
                root_memorial.status = TaskStatus.CANCELLED
                root_memorial.error = "DAG execution cancelled"
                root_memorial.completed_at = datetime.now(UTC)
                terminal_root = root_memorial
                persist_terminal = True
                execution.status = "cancelled"
                execution.completed_at = root_memorial.completed_at
                self._storage.update_dag_execution_status(
                    execution.id,
                    execution.status,
                    completed_at=execution.completed_at,
                )
            except Exception as exc:
                logger.exception("DAG execution failed for edict %s", edict.id)
                root_memorial.status = TaskStatus.FAILED
                root_memorial.error = str(exc)
                root_memorial.completed_at = datetime.now(UTC)
                terminal_root = root_memorial
                persist_terminal = True
                execution.status = "failed"
                execution.completed_at = root_memorial.completed_at
                self._storage.update_dag_execution_status(
                    execution.id,
                    execution.status,
                    completed_at=execution.completed_at,
                )
            finally:
                if self._lane_manager:
                    self._lane_manager.remove_session(edict.id)
                if terminal_root is None:
                    # scheduler 没把根终态交回来（自行落库，或没有 root_memorial_id）：
                    # 以库为准。绝不能拿内存里那份仍是 RUNNING 的副本去覆盖已落库的终态。
                    terminal_root = self._storage.get_memorial(root_memorial.id) or root_memorial
                try:
                    terminal_evidence, terminal_cancellation = await complete_workspace_lifecycle(
                        self._workspace_runtime.finalize(
                            bound_workspace,
                            terminal_root.status,
                        )
                    )
                    if terminal_cancellation is not None and cancelled_error is None:
                        cancelled_error = terminal_cancellation
                finally:
                    # finalize 失败也不能让根 memorial 卡在非终态（修一个竞态换来一个
                    # 更糟的挂起）。
                    if persist_terminal and not _defer_terminal:
                        try:
                            self._storage.update_memorial(terminal_root)
                        except Exception:
                            logger.exception("Failed to update root memorial %s", terminal_root.id)

        terminal_memorial = self._storage.get_memorial(root_memorial.id) or terminal_root
        event_type = {
            TaskStatus.COMPLETED: "execution.completed",
            TaskStatus.CANCELLED: "execution.cancelled",
        }.get(terminal_memorial.status, "execution.failed")
        if not _defer_terminal:
            await self._bus.emit(
                make_event(
                    event_type,
                    edict_id=edict.id,
                    memorial_id=root_memorial.id,
                    producer="executor",
                    payload={
                        "dag_id": execution.id,
                        "status": terminal_memorial.status.value,
                        "error": terminal_memorial.error,
                        "failure_reason": resolve_failure_reason(
                            terminal_memorial.status.value,
                            terminal_memorial.error,
                            terminal_memorial.failure_reason,
                        ),
                        **terminal_evidence.event_payload(),
                    },
                )
            )
        if cancelled_error is not None:
            raise cancelled_error

    def _stamp_universe(self, memorial: Memorial) -> None:
        """执行开始时固化 memorial 所属位面（一旦设定，本次运行内不变）。"""
        if self._universe_manager is not None and memorial.universe_id is None:
            memorial.universe_id = self._universe_manager.route_for_memorial(memorial.id)

    def _prepare_governed_executor(
        self,
        edict: Edict,
        memorial: Memorial,
        *,
        execution_mode: ExecutionMode,
    ) -> PreparedExecutor:
        prepared = self._resolve_governed_executor(
            edict,
            memorial,
            execution_mode=execution_mode,
        )
        self._persist_effective_contract(edict, memorial, prepared)
        return prepared

    def _resolve_governed_executor(
        self,
        edict: Edict,
        memorial: Memorial,
        *,
        execution_mode: ExecutionMode,
    ) -> PreparedExecutor:
        requested = edict.governance_contract or LegacyEdictGovernanceMapper.from_edict(
            edict,
            default_workspace_id="legacy-default",
        )
        if edict.governance_contract is not None:
            requested = LegacyEdictGovernanceMapper.apply_run_overrides(
                requested,
                edict,
                runtime_overridden=memorial.runtime_override is not None,
                acceptance_overridden=memorial.acceptance_override is not None,
            )

        existing = self._storage.get_effective_governance_contract(memorial.id)
        if existing is not None:
            if existing.requested_contract_hash != requested.content_hash:
                raise RuntimeError(
                    f"effective governance contract drift for memorial {memorial.id}"
                )
            prepared = self._adapter_registry.bind_effective(
                existing,
                run_id=memorial.id,
                instruction=memorial.instruction or requested.objective.goal,
                execution_mode=execution_mode,
            )
            memorial.effective_governance_contract = existing
            return prepared

        return self._adapter_registry.prepare(
            requested,
            run_id=memorial.id,
            instruction=memorial.instruction or requested.objective.goal,
            execution_mode=execution_mode,
        )

    def _persist_effective_contract(
        self,
        edict: Edict,
        memorial: Memorial,
        prepared: PreparedExecutor,
    ) -> None:
        existing = self._storage.get_effective_governance_contract(memorial.id)
        if existing is None:
            self._storage.save_effective_governance_contract(
                memorial.id,
                edict.id,
                prepared.effective,
            )
        elif existing.content_hash != prepared.effective.content_hash:
            raise RuntimeError(f"effective governance contract drift for memorial {memorial.id}")
        memorial.effective_governance_contract = prepared.effective

    async def _prepare_runtime_executor(
        self,
        edict: Edict,
        memorial: Memorial,
        *,
        execution_mode: ExecutionMode,
    ) -> tuple[PreparedExecutor, BoundWorkspace | None]:
        prepared = self._resolve_governed_executor(
            edict,
            memorial,
            execution_mode=execution_mode,
        )
        workspace = await self._workspace_runtime.prepare(prepared.effective, memorial)
        try:
            if workspace.effective.content_hash != prepared.effective.content_hash:
                prepared = self._adapter_registry.bind_effective(
                    workspace.effective,
                    run_id=memorial.id,
                    instruction=prepared.prepared.instruction,
                    execution_mode=execution_mode,
                )
            self._persist_effective_contract(edict, memorial, prepared)
            return prepared, workspace.bound
        except BaseException as exc:
            await self._workspace_runtime.finalize(workspace.bound, TaskStatus.FAILED)
            if not isinstance(exc, Exception):
                raise
            raise WorkspaceContractError(f"workspace startup failed: {exc}") from exc

    async def _prepare_runtime_or_cancel(
        self,
        edict: Edict,
        memorial: Memorial,
        *,
        execution_mode: ExecutionMode,
        dag_id: str | None = None,
        defer_root_terminal: bool = False,
    ) -> tuple[PreparedExecutor, BoundWorkspace | None]:
        try:
            return await self._prepare_runtime_executor(
                edict,
                memorial,
                execution_mode=execution_mode,
            )
        except asyncio.CancelledError:
            await shield_workspace_lifecycle(
                self._cancel_before_running(
                    edict,
                    memorial,
                    dag_id=dag_id,
                    defer_root_terminal=defer_root_terminal,
                )
            )
            raise

    async def _cancel_before_running(
        self,
        edict: Edict,
        memorial: Memorial,
        *,
        dag_id: str | None,
        defer_root_terminal: bool = False,
    ) -> None:
        memorial.status = TaskStatus.CANCELLED
        memorial.error = "Task was cancelled before execution started"
        memorial.completed_at = datetime.now(UTC)
        if not defer_root_terminal:
            self._storage.update_memorial(memorial)
        lease = self._storage.get_workspace_lease_by_run(memorial.id)
        change_set = (
            self._storage.get_latest_canonical_change_set_for_lease(lease.id)
            if lease is not None
            else None
        )
        evidence = WorkspaceTerminalEvidence(
            lease_id=lease.id if lease is not None else None,
            change_set=change_set,
            lease_state=lease.state if lease is not None else None,
        )
        payload: dict[str, object] = {
            "status": memorial.status.value,
            "error": memorial.error,
            "failure_reason": resolve_failure_reason(
                memorial.status.value,
                memorial.error,
                memorial.failure_reason,
            ),
            **evidence.event_payload(),
        }
        if dag_id is not None:
            payload["dag_id"] = dag_id
        try:
            if not defer_root_terminal:
                await self._bus.emit(
                    make_event(
                        "execution.cancelled",
                        edict_id=edict.id,
                        memorial_id=memorial.id,
                        producer="executor",
                        payload=payload,
                    )
                )
        except Exception:
            logger.exception(
                "Failed to emit pre-running cancellation for memorial %s",
                memorial.id,
            )

    async def _reject_executor_mode(
        self,
        edict: Edict,
        memorial: Memorial,
        exc: UnsupportedExecutorMode,
        *,
        defer_root_terminal: bool = False,
    ) -> None:
        memorial.status = TaskStatus.FAILED
        memorial.error = str(exc)
        memorial.completed_at = datetime.now(UTC)
        if defer_root_terminal:
            return
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "execution.rejected",
                edict_id=edict.id,
                memorial_id=memorial.id,
                producer="executor",
                payload={
                    "code": "executor_mode_unsupported",
                    "adapter_id": exc.adapter_id,
                    "execution_mode": exc.execution_mode,
                },
            )
        )

    async def _reject_capability_mismatch(
        self,
        edict: Edict,
        memorial: Memorial,
        exc: MandatoryCapabilityMismatch,
        *,
        defer_root_terminal: bool = False,
    ) -> None:
        memorial.status = TaskStatus.FAILED
        memorial.error = str(exc)
        memorial.completed_at = datetime.now(UTC)
        if defer_root_terminal:
            return
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "execution.rejected",
                edict_id=edict.id,
                memorial_id=memorial.id,
                producer="executor",
                payload={
                    "code": "governance_capability_mismatch",
                    "mismatches": [item.model_dump(mode="json") for item in exc.mismatches],
                },
            )
        )

    def _snapshot_keqing(self, edict: Edict, memorial: Memorial) -> None:
        """客卿执行后对其隔离工作区打影子快照并落台账（放手四保险③）。

        影子仓独立 GIT_DIR、不碰用户 .git;失败只告警不阻断（快照是退路,
        不是执行前置）。
        """
        try:
            from datetime import UTC, datetime

            from ulid import ULID

            from tianshu.executor.shadow_snapshot import ShadowSnapshot

            work = self._keqing.work_dir(edict.id)
            if not work.exists():
                return
            shadow = ShadowSnapshot(work, edict.id)
            if not shadow.init():
                return
            snap = shadow.snapshot(f"keqing:{memorial.id}")
            if snap is None:
                return
            self._storage.save_shadow_snapshot(
                {
                    "id": str(ULID()),
                    "edict_id": edict.id,
                    "memorial_id": memorial.id,
                    "sha": snap.sha,
                    "label": snap.label,
                    "work_tree": str(work),
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
        except Exception:
            logger.exception("[keqing] shadow snapshot failed for edict %s", edict.id)

    def _apply_memorial_override(
        self,
        edict: Edict,
        memorial: Memorial | None,
    ) -> Edict:
        """合并 memorial 的 runtime_override / acceptance_override 到 edict 副本。

        - runtime_override：dict 字段级浅合并（用户未填字段保留 edict 原值）
        - acceptance_override：整体替换 edict.acceptance（None = 沿用）
        - 不修改原 edict 行，仅本次执行内生效。
        """
        if memorial is None:
            return edict
        update_kwargs: dict = {}
        if memorial.runtime_override:
            try:
                update_kwargs["runtime"] = edict.runtime.model_copy(
                    update=memorial.runtime_override,
                )
            except Exception as e:
                logger.warning(
                    "apply runtime_override failed for memorial %s: %s; falling back to edict.runtime",
                    memorial.id,
                    e,
                )
        if memorial.acceptance_override is not None:
            update_kwargs["acceptance"] = memorial.acceptance_override
        if not update_kwargs:
            return edict
        logger.info(
            "[EXEC] Edict %s memorial %s: applied override(runtime=%s, acceptance=%s)",
            edict.id,
            memorial.id,
            "runtime" in update_kwargs,
            "acceptance" in update_kwargs,
        )
        return edict.model_copy(update=update_kwargs)

    async def _execute_outer_loop(
        self,
        edict: Edict,
        memorial: Memorial | None,
        *,
        _defer_terminal: bool = False,
        attempt_authority: AttemptAuthority | None = None,
    ) -> None:
        """通过 orchestrator 跑长任务 outer loop。"""
        from tianshu.executor.orchestrator import run as orch_run

        if memorial is None:
            memorial = Memorial(
                edict_id=edict.id,
                instruction=edict.goal,
                status=TaskStatus.SUBMITTED,
            )
            self._storage.save_memorial(memorial)
        try:
            prepared_executor, bound_workspace = await self._prepare_runtime_or_cancel(
                edict,
                memorial,
                execution_mode="outer_loop",
                defer_root_terminal=_defer_terminal,
            )
        except MandatoryCapabilityMismatch as exc:
            await self._reject_capability_mismatch(
                edict, memorial, exc, defer_root_terminal=_defer_terminal
            )
            return
        except UnsupportedExecutorMode as exc:
            await self._reject_executor_mode(
                edict, memorial, exc, defer_root_terminal=_defer_terminal
            )
            return
        except (WorkspaceContractError, WorkspaceError) as exc:
            await self._reject_workspace_runtime(
                edict, memorial, exc, defer_root_terminal=_defer_terminal
            )
            return

        cancelled_error: asyncio.CancelledError | None = None
        suspended_error: ManagedRunSuspended | None = None
        terminal_evidence = WorkspaceTerminalEvidence()
        binding = bind_workspace(bound_workspace) if bound_workspace is not None else nullcontext()
        with binding:
            try:
                memorial.status = TaskStatus.RUNNING
                memorial.started_at = datetime.now(UTC)
                self._stamp_universe(memorial)
                self._storage.update_memorial(memorial)
                orchestrator_ctx = copy(self._orchestrator_ctx)
                orchestrator_ctx.agent = prepared_executor
                orchestrator_ctx.execution_context = prepared_executor.execution_context(edict)
                orchestrator_ctx.attempt_authority = attempt_authority
                if bound_workspace is not None:
                    orchestrator_ctx.workspace_root = bound_workspace.root
                governed_edict = edict.model_copy(
                    update={"goal": prepared_executor.prepared.instruction}
                )
                result = await orch_run(governed_edict, memorial, orchestrator_ctx)
                memorial.status = result.status
                memorial.result = result.final_output
                memorial.final_output = result.final_output
                memorial.error = result.error
            except asyncio.CancelledError as exc:
                cancelled_error = exc
                memorial.status = TaskStatus.CANCELLED
                memorial.error = "Task was cancelled"
            except ManagedRunSuspended as exc:
                suspended_error = exc
            except Exception as exc:
                logger.exception("orchestrator failed for edict %s", edict.id)
                memorial.status = TaskStatus.FAILED
                memorial.error = f"orchestrator error: {exc}"
            finally:
                if suspended_error is None:
                    memorial.completed_at = datetime.now(UTC)
                try:
                    terminal_evidence, terminal_cancellation = await complete_workspace_lifecycle(
                        self._workspace_runtime.finalize(
                            bound_workspace,
                            memorial.status,
                        )
                    )
                    if terminal_cancellation is not None and cancelled_error is None:
                        cancelled_error = terminal_cancellation
                finally:
                    # 同 execute_edict：终态可见 ⟹ 变更集已可读；finalize 失败也不能
                    # 让 memorial 卡在非终态。
                    if not _defer_terminal:
                        try:
                            self._storage.update_memorial(memorial)
                        except Exception:
                            logger.exception("Failed to update memorial %s", memorial.id)
                        else:
                            try:
                                self._storage.finalize_outer_loop_terminal(edict.id)
                            except Exception:
                                logger.exception(
                                    "Failed to finalize outer-loop state for edict %s",
                                    edict.id,
                                )

        event_type = {
            TaskStatus.COMPLETED: "execution.completed",
            TaskStatus.CANCELLED: "execution.cancelled",
        }.get(memorial.status, "execution.failed")
        if suspended_error is not None:
            raise suspended_error
        if not _defer_terminal:
            await self._bus.emit(
                make_event(
                    event_type,
                    edict_id=edict.id,
                    memorial_id=memorial.id,
                    producer="executor",
                    payload={
                        "status": memorial.status.value,
                        "error": memorial.error,
                        "failure_reason": resolve_failure_reason(
                            memorial.status.value,
                            memorial.error,
                            memorial.failure_reason,
                        ),
                        **terminal_evidence.event_payload(),
                    },
                )
            )
        if cancelled_error is not None:
            raise cancelled_error

    async def execute_edict(
        self,
        edict: Edict,
        plan: Plan | None = None,
        memorial: Memorial | None = None,
        history: list[dict] | None = None,
        user_content: str | None = None,
        *,
        _defer_terminal: bool = False,
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

        # follow-up 时本次 memorial 可能携带 runtime/acceptance override，
        # 合并到 edict 副本上（不写回 edict 行）。
        edict = self._apply_memorial_override(edict, memorial)

        # 合并后若有 acceptance（情况 2：follow-up 升级到长任务）→ 切到 outer loop。
        if edict.acceptance is not None and self._orchestrator_ctx is not None:
            logger.info(
                "[EXEC] Edict %s memorial %s: 走 orchestrator outer loop 路径（profile=%s）",
                edict.id,
                memorial.id,
                edict.execution_profile,
            )
            await self._execute_outer_loop(
                edict,
                memorial,
                _defer_terminal=_defer_terminal,
            )
            return

        try:
            prepared_executor, bound_workspace = await self._prepare_runtime_or_cancel(
                edict,
                memorial,
                execution_mode="single",
                defer_root_terminal=_defer_terminal,
            )
        except MandatoryCapabilityMismatch as exc:
            await self._reject_capability_mismatch(
                edict, memorial, exc, defer_root_terminal=_defer_terminal
            )
            return
        except UnsupportedExecutorMode as exc:
            await self._reject_executor_mode(
                edict, memorial, exc, defer_root_terminal=_defer_terminal
            )
            return
        except (WorkspaceContractError, WorkspaceError) as exc:
            await self._reject_workspace_runtime(
                edict, memorial, exc, defer_root_terminal=_defer_terminal
            )
            return

        # Set persona_id: 显式指派 > 规划分派 > selector 兜底 > 默认执行官
        # 派官是规划的产物（规划 prompt 已含名册与 assigned_official 契约）；
        # 只有规划失败（passthrough 落 DEFAULT_EXECUTOR_ID 这类无效 id）时才
        # 需要兜底——此时 LLM 大概率不可用，故用 selector 关键词就地选官，零 LLM。
        if not memorial.persona_id:
            plan_persona = None
            if plan and plan.tasks:
                plan_persona = plan.tasks[0].assigned_official
            persona_id = edict.assigned_persona_id or plan_persona
            if (
                self._official_selector is not None
                and not edict.assigned_persona_id
                and (
                    not persona_id
                    or self._persona_loader is None
                    or self._persona_loader.get(persona_id) is None
                )
            ):
                try:
                    fallback = self._official_selector.select_for_task(edict.goal or "")
                except Exception:  # noqa: BLE001 - 兜底选官绝不阻断执行
                    fallback = None
                if fallback is not None:
                    persona_id = fallback.id
                    self._storage.append_event(
                        edict.id,
                        memorial.id,
                        "official.selected",
                        {"persona_id": persona_id, "source": "selector_fallback"},
                    )
            memorial.persona_id = persona_id or DEFAULT_EXECUTOR_ID
        logger.debug(
            "[EXEC] Edict %s: start execution, persona=%s, timeout=%ds, max_iter=%d",
            edict.id,
            memorial.persona_id,
            edict.runtime.timeout_seconds,
            edict.runtime.max_iterations,
        )
        # 跟进批示的多轮上下文：回放本敕令先前奏折（native 路径；客卿会话档
        # 自带连续性，单发客卿的 prompt 形态不消费 history）。
        # 周期性敕令（cron/interval）每次触发都是独立任务，不回放——否则历次
        # 输出线性累积，第 N 次触发要付前 N-1 次的全部 token。
        if (
            history is None
            and edict.schedule.type not in ("cron", "interval")
            and not prepared_executor.adapter.adapter_id.startswith("keqing:")
        ):
            from tianshu.executor.conversation import build_conversation_history

            history = (
                build_conversation_history(
                    edict,
                    self._storage.list_memorials_by_edict(edict.id),
                    exclude_memorial_id=memorial.id,
                )
                or None
            )
        result = None
        event_type = "execution.failed"
        cancelled_error: asyncio.CancelledError | None = None
        suspended_error: Exception | None = None
        retry_memorial: Memorial | None = None
        try:
            memorial.status = TaskStatus.RUNNING
            memorial.started_at = datetime.now(UTC)
            self._stamp_universe(memorial)
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
            binding = (
                bind_workspace(bound_workspace) if bound_workspace is not None else nullcontext()
            )
            with binding:
                if (
                    edict.runtime.policy_profile is not None
                    and self._session_rule_store is not None
                ):
                    await self._expand_policy_profile(edict, memorial)

                await self._hooks.run(
                    HookType.SESSION_START,
                    edict=edict,
                    memorial=memorial,
                )
                hook_result = await self._hooks.run(
                    HookType.BEFORE_AGENT_START,
                    edict=edict,
                    memorial=memorial,
                    plan=plan,
                )
                if hook_result.block:
                    memorial.status = TaskStatus.FAILED
                    memorial.error = f"Blocked by hook: {hook_result.reason}"
                else:
                    if hook_result.modified_args:
                        memory_history = hook_result.modified_args.get("memory_history")
                        if memory_history:
                            # 记忆是对话前的背景，放在对话历史之前——否则会插在
                            # 上一轮 assistant 与本轮 user 之间，形成连续多条 user
                            # 消息（严格交替校验的端点 400），且语义上把陈旧记忆
                            # 排得比真实上一轮答复更近。
                            history = memory_history + (history or [])

                    persona = None
                    if self._persona_loader and memorial.persona_id:
                        persona = self._persona_loader.get(memorial.persona_id)

                    def on_event(event: dict) -> None:
                        self._storage.append_event(
                            edict.id,
                            memorial.id,
                            event["type"],
                            event,
                        )

                    result = await asyncio.wait_for(
                        prepared_executor.execute(
                            edict,
                            memorial=memorial,
                            on_event=on_event,
                            history=history,
                            user_content=user_content,
                            persona=persona,
                        ),
                        timeout=edict.runtime.timeout_seconds,
                    )
                    if (
                        prepared_executor.adapter.adapter_id.startswith("keqing:")
                        and bound_workspace is None
                    ):
                        self._snapshot_keqing(edict, memorial)
                    memorial.status = result.status
                    memorial.summary = result.summary
                    memorial.result = result.result
                    memorial.final_output = result.result
                    memorial.usage = result.usage
                    memorial.error = result.error
                    memorial.reasoning_content = result.reasoning_content
                event_type = {
                    TaskStatus.COMPLETED: "execution.completed",
                    TaskStatus.FAILED: "execution.failed",
                    TaskStatus.CANCELLED: "execution.cancelled",
                }.get(memorial.status, "execution.failed")
        except asyncio.CancelledError as exc:
            cancelled_error = exc
            memorial.status = TaskStatus.CANCELLED
            memorial.error = "Task was cancelled"
            event_type = "execution.cancelled"
        except TimeoutError:
            memorial.status = TaskStatus.FAILED
            memorial.error = f"Execution timed out after {edict.runtime.timeout_seconds}s"
            memorial.failure_reason = "provider_timeout"
            event_type = "execution.failed"
        except ManagedRunSuspended as exc:
            suspended_error = exc
        except Exception as exc:
            logger.exception("Unexpected error executing edict %s", edict.id)
            memorial.status = TaskStatus.FAILED
            memorial.error = str(exc)
            memorial.failure_reason = _retryable_failure_reason(exc)
            event_type = "execution.failed"
        finally:
            if suspended_error is None:
                memorial.completed_at = datetime.now(UTC)

        async def finish_terminal_lifecycle() -> WorkspaceTerminalEvidence:
            terminal_binding = (
                bind_workspace(bound_workspace) if bound_workspace is not None else nullcontext()
            )
            with terminal_binding:
                evidence = WorkspaceTerminalEvidence()
                try:
                    agent_end_ctx: dict = {"edict": edict, "memorial": memorial}
                    if result is not None:
                        agent_end_ctx["exit_reason"] = result.exit_reason
                        agent_end_ctx["iteration_count"] = result.iteration_count
                        agent_end_ctx["events"] = result.events
                    await self._hooks.run(HookType.AGENT_END, **agent_end_ctx)
                    await self._hooks.run(
                        HookType.SESSION_END,
                        edict=edict,
                        memorial=memorial,
                        usage=memorial.usage,
                    )

                    if self._session_rule_store is not None:
                        try:
                            await self._session_rule_store.clear_edict(edict.id)
                        except Exception:
                            logger.exception(
                                "[EXEC] Edict %s: failed to clear edict session rules",
                                edict.id,
                            )
                finally:
                    evidence = await self._workspace_runtime.finalize(
                        bound_workspace,
                        memorial.status,
                    )
                return evidence

        try:
            terminal_evidence, terminal_cancellation = await complete_workspace_lifecycle(
                finish_terminal_lifecycle()
            )
        finally:
            # 终态只有在变更集捕获之后才对外可见：客户端看到 memorial=completed 就会
            # 立刻取 /workspace-runs/{run}/changes，先落终态会让这个再正常不过的序列
            # 撞上 changes_unavailable。放 finally 是因为 finalize 失败也绝不能让
            # memorial 卡在非终态。
            if not _defer_terminal:
                try:
                    self._storage.update_memorial(memorial)
                except Exception:
                    logger.exception("Failed to update memorial %s", memorial.id)
        if terminal_cancellation is not None and cancelled_error is None:
            cancelled_error = terminal_cancellation
        if suspended_error is not None:
            raise suspended_error
        terminal_payload: dict[str, object] = {
            "status": memorial.status.value,
            "error": memorial.error,
            "failure_reason": resolve_failure_reason(
                memorial.status.value,
                memorial.error,
                memorial.failure_reason,
            ),
            **terminal_evidence.event_payload(),
        }

        if (
            not _defer_terminal
            and memorial.status == TaskStatus.FAILED
            and edict.runtime.retry_limit > 0
            and memorial.attempt < edict.runtime.retry_limit
        ):
            retry_memorial = Memorial(
                edict_id=edict.id,
                instruction=memorial.instruction or edict.goal,
                attempt=memorial.attempt + 1,
                parent_memorial_id=memorial.id,
                runtime_override=memorial.runtime_override,
                acceptance_override=memorial.acceptance_override,
            )
            self._storage.save_memorial(retry_memorial)

        if not _defer_terminal:
            try:
                await self._bus.emit(
                    make_event(
                        event_type,
                        edict_id=edict.id,
                        memorial_id=memorial.id,
                        producer="executor",
                        payload=terminal_payload,
                    )
                )
            except Exception:
                logger.exception("Failed to emit %s for memorial %s", event_type, memorial.id)

        if retry_memorial is not None:
            logger.info(
                "Auto-retry edict %s: attempt %d/%d",
                edict.id,
                retry_memorial.attempt,
                edict.runtime.retry_limit,
            )
            retry_task = asyncio.create_task(
                self.execute_edict(
                    edict,
                    memorial=retry_memorial,
                    user_content=retry_memorial.instruction,
                )
            )
            self._running_tasks.add(retry_task)
            retry_task.add_done_callback(self._running_tasks.discard)
        if cancelled_error is not None:
            raise cancelled_error

    async def _expand_policy_profile(self, edict: Edict, memorial: Memorial) -> None:
        try:
            from tianshu.tools.policy_profile import PolicyProfile, expand_profile_to_rules

            payload = edict.runtime.policy_profile
            assert payload is not None and self._session_rule_store is not None
            profile = PolicyProfile(
                allowed_paths=tuple(payload.allowed_paths),
                allowed_bash_prefixes=tuple(payload.allowed_bash_prefixes),
                tier_overrides=dict(payload.tier_overrides),
                auto_approve_max_tier=int(payload.auto_approve_max_tier),
                expires_after_seconds=payload.expires_after_seconds,
                template_name=payload.template_name,
            )
            created = await expand_profile_to_rules(
                profile,
                edict,
                self._session_rule_store,
            )
            self._storage.append_event(
                edict.id,
                memorial.id,
                "policy.profile_applied",
                {
                    "template_name": profile.template_name,
                    "rules_created": created,
                    "allowed_paths": list(profile.allowed_paths),
                    "allowed_bash_prefixes": list(profile.allowed_bash_prefixes),
                },
            )
        except Exception:
            logger.exception("[EXEC] Edict %s: failed to expand policy profile", edict.id)

    async def _reject_workspace_runtime(
        self,
        edict: Edict,
        memorial: Memorial,
        exc: Exception,
        *,
        defer_root_terminal: bool = False,
    ) -> None:
        memorial.status = TaskStatus.FAILED
        memorial.error = str(exc)
        memorial.completed_at = datetime.now(UTC)
        if defer_root_terminal:
            return
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "execution.rejected",
                edict_id=edict.id,
                memorial_id=memorial.id,
                producer="executor",
                payload={"code": "workspace_contract_rejected", "error": str(exc)},
            )
        )

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
        if not execution.root_memorial_id:
            raise ValueError("DAG execution has no governed root Memorial")
        self._fenced_completion.cancel_root(
            execution.root_memorial_id,
            reason=f"DAG {dag_id} cancellation",
        )

        canceller = CascadeCanceller(self._storage, worker_pool)
        cancelled = await canceller.cancel(execution)

        await self._bus.emit(
            make_event(
                "dag.cancelled",
                edict_id=execution.edict_id,
                producer="executor",
                payload={"dag_id": dag_id, "cancelled_nodes": cancelled},
            )
        )

        return cancelled

    async def retry_dag(
        self,
        dag_id: str,
        *,
        idempotency_key: str,
        from_node_ids: list[str] | None = None,
    ) -> list[str]:
        ingress = self._managed_run_ingress
        if ingress is None:
            raise RuntimeError("managed run ingress is not configured")
        result = await self.managed_run_ingress.retry_dag(
            dag_id=dag_id,
            idempotency_key=idempotency_key,
            from_node_ids=from_node_ids,
        )
        return list(result.reset_node_ids)

    async def shutdown(self) -> None:
        for task in list(self._running_tasks):
            task.cancel()
        await asyncio.gather(*self._running_tasks, return_exceptions=True)


def _retryable_failure_reason(exc: Exception) -> str | None:
    if isinstance(exc, TimeoutError):
        return "provider_timeout"
    if isinstance(exc, ConnectionError):
        return "provider_connection_error"
    if isinstance(exc, OSError):
        return "transient_execution_error"
    return None
