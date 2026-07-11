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

from tianshu.config import TianshuSettings
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.dag_scheduler import DAGScheduler
from tianshu.executor.executor import Executor
from tianshu.executor.lanes import LaneManager
from tianshu.executor.policy_hook import PolicyHook
from tianshu.executor.worker_pool import WorkerPool
from tianshu.kernel.hooks import HookType
from tianshu.skills.reviewer import SkillReviewHandler
from tianshu.skills.validator import SkillValidator
from tianshu.tools.policy import PolicyEngine
from tianshu.tools.policy_rules import build_default_rules


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
    )
    executor.set_agent(agent)
    executor.set_persona_loader(persona_loader)
    app.state.executor = executor

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
    approval_manager = ApprovalManager(
        event_bus=event_bus,
        storage=storage,
        session_rule_store=session_rule_store,
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
        workspace_root=Path(settings.workspace_dir).resolve(),
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
    hook_registry.register(HookType.LLM_OUTPUT, cost_manager.on_llm_output, priority=50)
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
    )
    hook_registry.register(HookType.AGENT_END, skill_reviewer.on_agent_end, priority=200)
    skill_reviewer.attach_event_bus(event_bus)
