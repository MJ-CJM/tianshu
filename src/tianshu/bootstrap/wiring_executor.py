"""WorkerPool / LaneManager / Executor / DAGScheduler / Approval / policy 装配。

四个函数对应原 lifespan() 中的四段代码：

- `wire_worker_lane`：`# --- WorkerPool & LaneManager ---` 分区。
- `wire_executor`：`# --- Executor ---` + `# --- DAGScheduler ---` +
  `# --- ApprovalManager ---` 三个相邻分区，合并。
- `wire_policy`：`# --- PolicyEngine + PolicyHook ---` 分区。
- `wire_hook_registrations`：`# --- Hook registrations ---` 分区——挂的
  是 memory_manager/cost_manager/approval_manager/skill_reviewer 的 hook，
  和 PolicyHook 的 hook 注册（wire_policy 里那条）同属"往 hook_registry
  上挂 executor 生命周期钩子"这类基础设施，故放在本文件而非目标结构草图
  未提及的其它文件。

调用这些函数时，除本函数创建的对象外，其余依赖（event_bus/storage/
config_manager/hook_registry/session_rule_store/agent/persona_loader/
worker_pool/lane_manager/prompt_builder/tools/notifier/memory_manager/
cost_manager/skills/metrics_store）均已在更早的 wiring 步骤写入
app.state，直接 service-locator 取用，不需要额外传参。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from tianshu.application.continuation_recovery import ContinuationRecoveryService
from tianshu.config import TianshuSettings
from tianshu.evolution.reconciler import GenerationReconciler
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.dag_scheduler import DAGScheduler
from tianshu.executor.executor import Executor
from tianshu.executor.generation_controller import GenerationController
from tianshu.executor.keqing.generation import PiGenerationBundle, PiReleaseMaterializer
from tianshu.executor.keqing.pi_probe import verify_pi_rpc_contract
from tianshu.executor.lanes import LaneManager
from tianshu.executor.policy_hook import PolicyHook
from tianshu.executor.worker_pool import WorkerPool
from tianshu.executor.workspace_runtime import WORKSPACE_MAIN_SOURCE_ID
from tianshu.kernel.hooks import HookType
from tianshu.skills.reviewer import SkillReviewHandler
from tianshu.skills.validator import SkillValidator
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.tools.policy import PolicyEngine
from tianshu.tools.policy_rules import build_default_rules


def _snapshot_binding_available(app: FastAPI) -> bool:
    resolver = getattr(app.state, "system_snapshot_resolver", None)
    if resolver is None:
        return False
    try:
        resolver.resolve()
    except Exception:  # noqa: BLE001 - readiness exposes only a stable failure code
        return False
    return True


def wire_worker_lane(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 WorkerPool + LaneManager。"""
    # --- WorkerPool & LaneManager ---
    worker_pool = WorkerPool(max_concurrency=settings.max_global_concurrency)
    app.state.worker_pool = worker_pool

    lane_manager = LaneManager(max_global_concurrency=settings.max_global_concurrency)
    app.state.lane_manager = lane_manager


def wire_executor(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 Executor + DAGScheduler + ApprovalManager。"""
    event_bus = app.state.event_bus
    storage = app.state.storage
    config_manager = app.state.config_manager
    hook_registry = app.state.hook_registry
    session_rule_store = app.state.session_rule_store
    agent = app.state.agent
    persona_loader = app.state.persona_loader
    worker_pool = app.state.worker_pool
    lane_manager = app.state.lane_manager
    prompt_builder = app.state.prompt_builder

    # --- Executor ---
    executor = Executor(
        event_bus=event_bus,
        storage=storage,
        config_manager=config_manager,
        hook_registry=hook_registry,
        session_rule_store=session_rule_store,
        execution_gateway=app.state.execution_gateway,
        workspace_service=app.state.workspace_service,
        workspace_sources={WORKSPACE_MAIN_SOURCE_ID: app.state.workspace_roots.source},
    )
    executor.set_agent(agent)
    executor.set_persona_loader(persona_loader)
    # 规划失败兜底：selector 按旨意关键词就地选官（派官正途在规划 prompt 的
    # assigned_official 契约里，此处只兜 passthrough 场景，零 LLM 成本）。
    # 就地构造：app.state.official_selector 在 wire_persona_quality 才装配
    # （晚于本函数）；OfficialSelector 无状态，仅包一层 persona_loader。
    from tianshu.persona.selector import OfficialSelector

    executor.set_official_selector(OfficialSelector(persona_loader))
    app.state.executor = executor

    def generation_default_model(backend: str) -> str | None:
        return config_manager.agent_config.keqing_default_models.get(backend) or None

    artifact_root = Path(settings.artifact_dir).expanduser().resolve(strict=True)
    if artifact_root.parent == artifact_root.parent.parent:
        raise RuntimeError("artifact_dir must not be directly below the filesystem root")
    managed_release_root = artifact_root.parent / "runtime-releases"
    keqing_root = Path("~/.tianshu/keqing").expanduser()
    materializer = PiReleaseMaterializer(
        app.state.execution_gateway,
        release_root=managed_release_root,
        root=keqing_root,
        default_model_provider=generation_default_model,
    )

    async def warm_generation(bundle: object) -> tuple[bool, str | None]:
        if not isinstance(bundle, PiGenerationBundle):
            return False, "unsupported_generation_bundle"
        keqing_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return await verify_pi_rpc_contract(
            app.state.execution_gateway,
            workspace_root=keqing_root,
            binary_path=bundle.binary_path,
        )

    generation_repository = GenerationRepository()
    generation_controller = GenerationController(
        generation_repository,
        storage.unit_of_work,
        materializer,
        executor.adapter_registry,
        warm_probe=warm_generation,
    )
    app.state.generation_recovery_report = generation_controller.recover()
    app.state.generation_controller = generation_controller
    app.state.generation_reconciler = GenerationReconciler(
        generation_repository,
        storage.unit_of_work,
        executor.adapter_registry,
        snapshot_binding_available=lambda: _snapshot_binding_available(app),
    )

    # --- DAGScheduler ---
    dag_scheduler = DAGScheduler(
        worker_pool=worker_pool,
        agent=agent,
        storage=storage,
        event_bus=event_bus,
        persona_loader=persona_loader,
        prompt_builder=prompt_builder,
    )
    executor.set_dag_scheduler(dag_scheduler)
    executor.set_lane_manager(lane_manager)
    app.state.dag_scheduler = dag_scheduler

    # --- ApprovalManager ---
    continuation_recovery = ContinuationRecoveryService(storage)
    event_bus.on(
        "decision.resolved",
        continuation_recovery.handle_decision_resolved,
        consumer_name="continuation_recovery.v1",
    )
    app.state.continuation_recovery = continuation_recovery

    approval_manager = ApprovalManager(
        event_bus=event_bus,
        storage=storage,
        session_rule_store=session_rule_store,
        edict_application_service=app.state.edict_application_service,
        decision_service=app.state.decision_service,
    )
    event_bus.on(
        "decision.resolved",
        approval_manager.handle_decision_resolved,
        # Keep the stable consumer identity so upgrades do not replay old tool projections.
        consumer_name="approval_manager.tool_decree_projection.v1",
    )
    event_bus.on(
        "decision.expired",
        approval_manager.handle_decision_expired,
        consumer_name="approval_manager.decision_expiry_projection.v1",
    )
    app.state.approval_manager = approval_manager


def wire_policy(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 PolicyEngine + PolicyHook 并挂 BEFORE_TOOL_CALL hook。"""
    storage = app.state.storage
    tools = app.state.tool_registry
    session_rule_store = app.state.session_rule_store
    approval_manager = app.state.approval_manager
    notifier = app.state.notifier
    event_bus = app.state.event_bus
    hook_registry = app.state.hook_registry

    # --- 锦衣卫·分级急停(迭代 3):注入工具管线入口,先于所有判定 ---
    from tianshu.security.estop import EstopManager

    estop_manager = EstopManager(storage)
    tools.set_estop(estop_manager)
    app.state.estop_manager = estop_manager

    # --- PolicyEngine + PolicyHook ---
    policy_engine = PolicyEngine(rules=build_default_rules())
    app.state.policy_engine = policy_engine

    # 司礼监·代批(迭代 7):低风险 decree 自动代批,受四道闸约束(默认关)
    from tianshu.executor.silijian import Silijian

    silijian = Silijian(storage, app.state.config_manager, estop_manager=estop_manager)
    app.state.silijian = silijian

    policy_hook = PolicyHook(
        engine=policy_engine,
        workspace_root=app.state.workspace_roots.source,
        storage=storage,
        tool_registry=tools,
        session_rule_store=session_rule_store,
        approval_manager=approval_manager,
        notifier=notifier,
        event_bus=event_bus,
        silijian=silijian,
    )
    app.state.policy_hook = policy_hook
    hook_registry.register(
        HookType.BEFORE_TOOL_CALL,
        policy_hook.on_before_tool_call,
        priority=5,  # 先于 approval_manager.on_before_tool_call(priority=10) 执行
    )


def wire_hook_registrations(app: FastAPI, settings: TianshuSettings) -> None:
    """挂 memory_manager/cost_manager/approval_manager/skill_reviewer 的 hook。"""
    hook_registry = app.state.hook_registry
    memory_manager = app.state.memory_manager
    cost_manager = app.state.cost_manager
    approval_manager = app.state.approval_manager
    skills = app.state.skills_loader
    config_manager = app.state.config_manager
    metrics_store = app.state.skill_metrics_store
    event_bus = app.state.event_bus

    # --- Hook registrations ---
    hook_registry.register(
        HookType.BEFORE_AGENT_START, memory_manager.on_before_agent_start, priority=50
    )
    hook_registry.register(HookType.BEFORE_ITERATION, cost_manager.on_before_iteration, priority=10)
    hook_registry.register(HookType.AGENT_END, memory_manager.on_agent_end, priority=100)
    hook_registry.register(
        HookType.BEFORE_TOOL_CALL, approval_manager.on_before_tool_call, priority=10
    )

    # Skill review hook (learning loop)
    skill_validator = SkillValidator()
    skill_reviewer = SkillReviewHandler(
        skills,
        config_manager,
        skill_validator,
        metrics_store=metrics_store,
        governed_writes_available=False,
    )
    hook_registry.register(HookType.AGENT_END, skill_reviewer.on_agent_end, priority=200)
    skill_reviewer.attach_event_bus(event_bus)
