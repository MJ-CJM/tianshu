"""Scheduler / Auditor / Planner / consultation / 事件订阅注册 / 插件装配。

四个函数对应原 lifespan() 中互不相邻的四段代码（中间穿插了大量其它子系统
的分区，因此没有合并成一个函数，只是都放在本文件里，各自在原来的位置
调用）：

- `wire_auditor`：`# --- Auditor ---` 分区。
- `wire_consultation`：`# --- ConsultationSession ---` +
  `# --- OrchestratorContext for long-task outer loop ---` 两个相邻分区，
  合并。
- `wire_scheduling`：`# --- Planner ---` + `# --- Scheduler ---` +
  schedule_edict 注册桥接代码 + `# --- EventBus subscriptions ---`，
  四段原文相邻，合并。
- `wire_plugins`：`# --- PluginApi ---` 分区（含紧随其后、没有独立分区
  注释的 hook_registry.set_event_writer 桥接代码）。目标结构草图未提及
  PluginApi 归属，按其在原文件中的位置（EventBus 订阅注册之后、Hook
  registrations 之前，同属"跨子系统收尾装配"）放在本文件，已在报告中
  记录。

跨区变量处置：
- `feishu_channel_cfg`：wire_persona（wiring_persona.py）里已经读过一次
  `storage.get_channel_config("feishu")` 用于敕令工具组开关；
  `wire_scheduling` 里 schedule_edict 的开关判断复用同一逻辑，按设计
  约定 #2 重新读一次（幂等的 DB 查询），不做跨函数传值。
- `_update_universe_fitness`：闭包提到 `bootstrap/universe_hooks.py`
  （见该文件顶部说明），此处只在注册处用一层具名函数转发绑定
  config_manager/storage，避免直接把 `functools.partial` 对象交给
  EventBus（它在 handler 抛异常时会访问 `handler.__qualname__`）。
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI

from tianshu.application.fenced_run_completion import FencedRunCompletion
from tianshu.application.managed_run_ingress import ManagedRunIngress
from tianshu.application.plan_review_lifecycle import PlanReviewAttemptCoordinator
from tianshu.application.run_dispatcher import RunDispatcher
from tianshu.application.run_execution import ProductionAttemptCompleter, ProductionRunRunner
from tianshu.application.run_reconciler import ControlPlaneUnavailable, RunReconciler
from tianshu.application.scheduled_runs import ScheduledRunPreparer
from tianshu.auditor.auditor import Auditor
from tianshu.bootstrap.universe_hooks import _update_universe_fitness
from tianshu.bootstrap.wiring_llm import _assistant_persona_id_provider
from tianshu.config import TianshuSettings
from tianshu.consultation.session import ConsultationSession
from tianshu.evolution.reconciler import GENERATION_CLEANUP_ONLY_ERRORS
from tianshu.executor.managed_tools import ManagedToolEffectExecutor
from tianshu.executor.orchestrator import OrchestratorContext
from tianshu.models.events import EventEnvelope
from tianshu.planner.planner import Planner
from tianshu.plugins.api import PluginApi
from tianshu.plugins.loader import PluginLoader
from tianshu.scheduler.scheduler import Scheduler
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.tools.schedule_edict import register_schedule_edict

logger = logging.getLogger(__name__)


def _require_restart_safe_legacy_plan(storage, connection, event: EventEnvelope) -> None:
    if event.event_type != "plan.completed":
        return
    decision_id = event.payload.get("decision_request_id")
    if not isinstance(decision_id, str) or event.memorial_id is None:
        raise RuntimeError(
            "legacy plan.completed retained: restart-safe canonical plan binding is missing"
        )
    state = storage.run_state_repo.load(connection, event.memorial_id)
    record = storage.decision_repo.get(connection, decision_id)
    root = connection.execute(
        "SELECT edict_id FROM memorials WHERE id=?",
        (event.memorial_id,),
    ).fetchone()
    binding_error = PlanReviewAttemptCoordinator._binding_error(  # noqa: SLF001
        state=state,
        record=record,
        memorial_id=event.memorial_id,
        decision_id=decision_id,
        memorial_edict_id=str(root["edict_id"]) if root is not None else None,
    )
    if binding_error is not None:
        raise RuntimeError(
            "legacy plan.completed retained: canonical plan binding conflicts with durable state"
        )


def wire_auditor(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 Auditor。"""
    event_bus = app.state.event_bus
    storage = app.state.storage
    config_manager = app.state.config_manager

    # --- Auditor ---
    # 审计规则 YAML 可配(迭代 7,D13):缺省文件不存在则全默认(向后兼容)
    from pathlib import Path

    from tianshu.auditor.rules_config import load_audit_rules

    rules_path = Path("~/.tianshu/audit_rules.yaml").expanduser()
    auditor = Auditor(
        event_bus=event_bus,
        storage=storage,
        config_manager=config_manager,
        rules_config=load_audit_rules(rules_path if rules_path.exists() else None),
    )
    app.state.auditor = auditor


def wire_consultation(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 ConsultationSession + OrchestratorContext（长任务外层循环）。"""
    persona_loader = app.state.persona_loader
    config_manager = app.state.config_manager
    provider_manager = app.state.provider_manager
    memory_manager = app.state.memory_manager
    agent = app.state.agent
    storage = app.state.storage
    event_bus = app.state.event_bus
    notifier = app.state.notifier
    approval_manager = app.state.approval_manager
    executor = app.state.executor

    # --- ConsultationSession ---
    consultation = ConsultationSession(
        persona_loader=persona_loader,
        config_manager=config_manager,
        provider_manager=provider_manager,
        memory_manager=memory_manager,
        storage=storage,
        notifier=notifier,
        # 官员据此拥有与执行敕令时同等的工具链（只读档），先查证再发言（issue #59）
        agent=agent,
        tools=app.state.tools,
    )
    app.state.consultation = consultation
    # 后台廷议任务的强引用集合（丢引用会被 GC 回收，见 gateway/api.py::_spawn_consultation）
    app.state.consultation_tasks = set()
    # 上次进程留下的 running/pending 廷议不可能再被推进，启动时一律判死，
    # 否则前端会对着一条永远不动的记录无限轮询。
    stale = storage.mark_stale_consultations_failed("interrupted by server restart")
    if stale:
        logger.info("Marked %d stale consultation(s) as failed on startup", stale)

    # --- OrchestratorContext for long-task outer loop ---
    orch_ctx = OrchestratorContext(
        agent=agent,
        storage=storage,
        bus=event_bus,
        actor_llm=provider_manager.get_client(),
        critic_llm=provider_manager.get_client(),
        critic_fallback_llm=None,
        consultation_session=consultation,
        notifier=notifier,
        approvals=approval_manager,  # ApprovalManager outer-loop 接口（wait_for_outer_loop_decision）
        persona_loader=persona_loader,
        provider_manager=provider_manager,
        execution_gateway=app.state.execution_gateway,
        workspace_root=Path(settings.workspace_dir).resolve(),
        cost_manager=app.state.cost_manager,
    )
    executor.set_orchestrator_context(orch_ctx)
    app.state.orchestrator_ctx = orch_ctx


def wire_scheduling(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 Planner + Scheduler，注册 schedule_edict 工具与全部事件订阅。"""
    event_bus = app.state.event_bus
    storage = app.state.storage
    config_manager = app.state.config_manager
    official_selector = app.state.official_selector
    persona_loader = app.state.persona_loader
    prompt_builder = app.state.prompt_builder
    tools = app.state.tool_registry
    executor = app.state.executor
    auditor = app.state.auditor
    cost_manager = app.state.cost_manager
    memory_manager = app.state.memory_manager
    notifier = app.state.notifier

    # --- Planner ---
    planner = Planner(
        event_bus=event_bus,
        storage=storage,
        config_manager=config_manager,
        official_selector=official_selector,
        persona_loader=persona_loader,
        prompt_builder=prompt_builder,
        tool_registry=tools,
        approval_manager=app.state.approval_manager,
        provider_manager=app.state.provider_manager,
    )
    app.state.planner = planner

    # --- Durable managed execution ---
    tools.set_managed_effect_executor(
        ManagedToolEffectExecutor(storage, app.state.decision_service)
    )
    production_runner = ProductionRunRunner(planner, executor)
    fenced_completion = FencedRunCompletion(storage.unit_of_work, storage.attempt_repo)
    executor.set_fenced_completion(fenced_completion)
    app.state.approval_manager.set_fenced_completion(fenced_completion)
    production_completer = ProductionAttemptCompleter(
        fenced_completion,
        storage.attempt_repo,
        production_runner,
    )
    generation_audit_repository = GenerationRepository()

    def record_attempt_failure(authority, failure_code: str) -> None:
        if failure_code != "generation_retired":
            return
        with storage.unit_of_work() as unit_of_work:
            generation_audit_repository.record_retired(
                unit_of_work.connection,
                memorial_id=authority.memorial_id,
                attempt_id=authority.attempt_id,
            )
            unit_of_work.commit()

    run_dispatcher = RunDispatcher(
        storage.attempt_repo,
        production_runner,
        owner_id=f"run-{uuid4().hex}",
        completer=production_completer,
        exit_cleanup=production_runner.discard_projection,
        generation_release=app.state.generation_controller.release_binding,
        failure_audit=record_attempt_failure,
        challenger_router=app.state.challenger_router,
    )
    plan_review_coordinator = PlanReviewAttemptCoordinator(storage)

    def reconcile_control_planes() -> int:
        evolution_count = app.state.evolution_reconciler.reconcile_once()
        generation_count = app.state.generation_reconciler.reconcile_once()
        _generation_ready, generation_errors = app.state.generation_reconciler.readiness_snapshot()
        blocking_generation_errors = set(generation_errors) - GENERATION_CLEANUP_ONLY_ERRORS
        if blocking_generation_errors:
            raise ControlPlaneUnavailable(
                "generation control plane is unavailable: "
                + ",".join(sorted(blocking_generation_errors))
            )
        plan_review_count = plan_review_coordinator.reconcile_once()
        dead_letter_count = fenced_completion.reconcile_dead_lettered_roots()
        return evolution_count + generation_count + plan_review_count + dead_letter_count

    run_reconciler = RunReconciler(
        storage.attempt_repo,
        run_dispatcher,
        before_scan=reconcile_control_planes,
    )
    managed_run_ingress = ManagedRunIngress(
        storage,
        run_reconciler,
        challenger_router=app.state.challenger_router,
    )
    executor.set_managed_run_ingress(managed_run_ingress)
    scheduled_run_preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        app.state.challenger_router,
        require_runtime_binding=settings.system_snapshot_enabled,
    )
    app.state.production_run_runner = production_runner
    app.state.fenced_run_completion = fenced_completion
    app.state.run_dispatcher = run_dispatcher
    app.state.plan_review_attempt_coordinator = plan_review_coordinator
    app.state.run_reconciler = run_reconciler
    app.state.managed_run_ingress = managed_run_ingress
    app.state.scheduled_run_preparer = scheduled_run_preparer

    # --- Scheduler ---
    scheduler = Scheduler(
        event_bus=event_bus,
        storage=storage,
        scheduled_run_preparer=scheduled_run_preparer,
        run_reconciler=run_reconciler,
        run_cancellation=fenced_completion,
    )
    app.state.scheduler = scheduler

    # schedule_edict tool —— 对话中安排定时/周期敕令（需 scheduler 就绪后注册）
    # 与 submit_edict 同属"敕令工具组"，同一 enable_edict_submission toggle 控制。
    register_schedule_edict(
        tools,
        storage=storage,
        scheduler=scheduler,
        persona_loader=persona_loader,
        edict_application_service=app.state.edict_application_service,
        # 派官收口需要知道谁是助手（issue #49）：与 agent 的 ASSISTANT_ONLY 过滤
        # 同源，复用同一个 provider，避免两处各判一次导致语义漂移。
        assistant_persona_id_provider=functools.partial(_assistant_persona_id_provider, storage),
    )
    feishu_channel_cfg = storage.get_channel_config("feishu")
    if not (feishu_channel_cfg and feishu_channel_cfg.get("enable_edict_submission")):
        tools.disable("schedule_edict")

    # --- EventBus subscriptions ---
    event_bus.on(
        "edict.submitted",
        scheduler.handle_submitted,
        consumer_name="scheduler.edict_submitted.v1",
    )

    async def _adopt_legacy_execution_event(event: EventEnvelope) -> None:
        """Turn pre-4B pending chain events into durable attempt work."""
        await managed_run_ingress.adopt_legacy(event)

    for legacy_event_type in ("edict.scheduled", "plan.completed", "edict.resume"):
        event_bus.on(
            legacy_event_type,
            _adopt_legacy_execution_event,
            consumer_name=f"managed.legacy_{legacy_event_type.replace('.', '_')}.v1",
        )
    event_bus.on(
        "execution.completed",
        auditor.handle_execution_completed,
        consumer_name="auditor.execution_completed.v1",
    )
    event_bus.on(
        "execution.failed",
        auditor.handle_execution_failed,
        consumer_name="auditor.execution_failed.v1",
    )
    event_bus.on(
        "execution.completed",
        cost_manager.handle_execution_completed,
        consumer_name="cost.execution_completed.v1",
        priority=150,
    )
    event_bus.on(
        "execution.completed",
        memory_manager.handle_execution_completed,
        consumer_name="memory.execution_completed.v1",
        priority=200,
    )
    event_bus.on(
        "execution.failed",
        notifier.handle_execution_failed,
        consumer_name="notifier.execution_failed.v1",
    )
    event_bus.on(
        "execution.failed",
        cost_manager.handle_execution_failed,
        consumer_name="cost.execution_failed.v1",
        priority=150,
    )
    event_bus.on(
        "audit.completed",
        notifier.handle_audit_completed,
        consumer_name="notifier.audit_completed.v1",
    )
    event_bus.on(
        "audit.completed",
        app.state.evidence_service.handle_audit_completed,
        consumer_name="evidence.audit_completed.v1",
        priority=50,
    )
    event_bus.on(
        "decree.approved",
        app.state.evidence_service.handle_decree_approved,
        consumer_name="evidence.decree_approved.v1",
        priority=50,
    )
    event_bus.on(
        "audit.completed",
        memory_manager.handle_audit_completed,
        consumer_name="memory.audit_completed.v1",
        priority=200,
    )
    event_bus.on(
        "cost.budget_exceeded",
        notifier.handle_execution_failed,
        consumer_name="notifier.cost_budget_exceeded.v1",
    )

    # 平行位面：memorial 完成 → 重算其所属位面的适应度
    async def _on_universe_fitness_event(event: EventEnvelope) -> None:
        await _update_universe_fitness(event, config_manager=config_manager, storage=storage)

    event_bus.on(
        "execution.completed",
        _on_universe_fitness_event,
        consumer_name="universe.execution_completed_fitness.v1",
        priority=250,
    )
    event_bus.on(
        "execution.failed",
        _on_universe_fitness_event,
        consumer_name="universe.execution_failed_fitness.v1",
        priority=250,
    )
    event_bus.on(
        "audit.completed",
        _on_universe_fitness_event,
        consumer_name="universe.audit_completed_fitness.v1",
        priority=250,
    )
    # 长任务 outer loop 事件实时广播到 WebSocket
    for outer_loop_event in (
        "outer_loop.started",
        "outer_loop.iteration.started",
        "outer_loop.iteration.finished",
        "outer_loop.checks.failed",
        "outer_loop.escalated",
        "outer_loop.completed",
        "outer_loop.exhausted",
        "outer_loop.approval.requested",
        "outer_loop.approval.received",
        "outer_loop.supervision_completed",
        "outer_loop.resumed",
    ):
        event_bus.on(
            outer_loop_event,
            notifier.handle_outer_loop_event,
            consumer_name="notifier.outer_loop_event.v1",
        )


def wire_plugins(app: FastAPI, settings: TianshuSettings) -> None:
    """创建扩展门面，登记本地 manifest，并接上 hook 执行事件写入。"""
    storage = app.state.storage
    tools = app.state.tool_registry
    hook_registry = app.state.hook_registry
    channel_registry = app.state.channel_registry
    provider_manager = app.state.provider_manager
    skills = app.state.skills_loader

    # --- PluginApi ---
    plugin_api = PluginApi(
        storage=storage,
        tool_registry=tools,
        hook_registry=hook_registry,
        channel_registry=channel_registry,
        provider_manager=provider_manager,
        skills_loader=skills,
    )
    app.state.plugin_api = plugin_api

    # Discovery is metadata-only. Entry points are deliberately not imported or executed.
    plugins_dir = Path(settings.plugins_dir).expanduser()
    plugin_loader = PluginLoader(plugins_dir)
    for manifest in plugin_loader.discover():
        plugin_api.register_plugin(manifest)

    # Wire event writer for hook execution events (8.2 support)
    hook_registry.set_event_writer(storage)
