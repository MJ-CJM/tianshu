"""SkillsLoader / metrics / register_skill_tools / SkillCurator / hot-reload 装配。

四个函数对应原 lifespan() 中不相邻的四段代码，顺序依赖各不相同，因此没有
合并成一个函数：

- `wire_skills`：`# --- Skills ---` 分区，创建 SkillsLoader + SkillMetricsStore。
  必须早于 persona/memory 装配（它们要用 skills/metrics_store）。
- `wire_skill_tools`：原分区注释写明"register_skill_tools 移到 config_manager
  定义之后"，对应的正是这段没有独立 `# --- X ---` 标题、但紧跟在
  `# --- LLM Config Manager ---` 之后的桥接代码。必须晚于 config_manager。
- `wire_skill_curator`：`# --- SkillCurator ---` 分区，晚于 provider_manager/
  config_manager，此时 skills/metrics_store 已经进了 app.state，直接用
  service-locator 取即可，不需要显式传参。
- `wire_skills_watcher`：`# --- Skills hot-reload watcher ---` 分区，
  在最靠后的位置创建；skills_watcher 从未写入 app.state（原代码只是
  lifespan 的局部变量，关停时在 yield 之后直接引用），因此由本函数返回，
  lifespan 显式持有到 shutdown 段使用。
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI

from tianshu.application.edicts import EdictApplicationService
from tianshu.config import TianshuSettings
from tianshu.evolution.candidate_service import CandidateLiveAuthorities, CandidateService
from tianshu.evolution.gates import GateEvaluator
from tianshu.evolution.promotion import (
    PromotionService,
    SkillPromotionAdapter,
    UnavailablePromotionAdapter,
)
from tianshu.evolution.reconciler import EvolutionRollbackReconciler
from tianshu.models.evolution_candidate import CandidateKind, EvolutionContractV1, GateName
from tianshu.resources.overlay import packaged_defaults
from tianshu.skills.curator import SkillCurator
from tianshu.skills.install_service import SkillInstallService
from tianshu.skills.loader import SkillsLoader, SkillsWatcher
from tianshu.skills.metrics import SkillMetricsStore
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.skill_tools import register_skill_tools
from tianshu.universe.router import ChallengerRouter

logger = logging.getLogger(__name__)


def runtime_skills_target() -> Path:
    override = os.environ.get("TIANSHU_RUNTIME_SKILLS_DIR")
    return Path(override or "~/.tianshu/skills").expanduser()


def wire_evolution_services(
    app: FastAPI,
    settings: TianshuSettings,
    *,
    skill_target: Path,
) -> None:
    """Wire the Task 3 candidate, gate, and authenticated skill proposal authorities."""

    authorities = CandidateLiveAuthorities(
        memory_root=Path(settings.memory_dir).expanduser().resolve(),
        skill_target=Path(skill_target).expanduser().resolve(),
        policy_root=(Path(settings.workspace_dir).expanduser().resolve() / ".tianshu/policies"),
        persona_root=Path(settings.runtime_personas_dir).expanduser().resolve(),
        code_worktree=Path(settings.workspace_dir).expanduser().resolve(),
    )
    candidates = CandidateService(
        app.state.storage,
        app.state.artifact_store,
        live_authorities=authorities,
    )

    def skill_contract(name: str) -> EvolutionContractV1:
        policy_digest = hashlib.sha256(b"tianshu.skill-gates.lean-preview.v1").hexdigest()
        return EvolutionContractV1(
            kind=CandidateKind.SKILL,
            subject_key=f"skill:{name}",
            governance_contract_hash=policy_digest,
            required_gates=tuple(GateName),
            regression_policy_artifact_digest=policy_digest,
            sample_policy_artifact_digest=policy_digest,
            budget_policy_artifact_digest=policy_digest,
            minimum_canary_samples=10,
            max_canary_allocation_basis_points=500,
            rollback_slo_seconds=30,
        )

    app.state.candidate_service = candidates
    gate_evaluator = GateEvaluator(
        app.state.storage,
        artifact_verifier=app.state.artifact_store,
    )
    app.state.evolution_gate_evaluator = gate_evaluator
    skill_promotion = SkillPromotionAdapter(
        app.state.artifact_store,
        live_root=authorities.skill_target,
    )
    unavailable_promotion = UnavailablePromotionAdapter()
    promotion_adapters = {
        kind: (skill_promotion if kind is CandidateKind.SKILL else unavailable_promotion)
        for kind in CandidateKind
    }
    app.state.promotion_service = PromotionService(
        app.state.storage,
        gate_evaluator,
        adapter_resolver=promotion_adapters.__getitem__,
    )
    app.state.evolution_reconciler = EvolutionRollbackReconciler(app.state.promotion_service)
    challenger_router = ChallengerRouter(
        app.state.storage,
        allocation_secret=settings.evolution_routing_secret.encode() or None,
        payload_resolver=candidates.resolve_effective_payload_current,
    )
    app.state.challenger_router = challenger_router
    app.state.edict_application_service = EdictApplicationService(
        app.state.storage,
        challenger_router=challenger_router,
    )
    app.state.skill_install_service = SkillInstallService(
        candidates,
        app.state.storage,
        contract_factory=skill_contract,
    )


def wire_skills(app: FastAPI, settings: TianshuSettings) -> tuple[SkillsLoader, SkillMetricsStore]:
    """创建 SkillsLoader + SkillMetricsStore（尚不写入 app.state）。

    返回 (skills, metrics_store)：两者要到 `# --- Agent ---` 分区才
    `app.state.skills_loader` / `app.state.skill_metrics_store`，但
    memory palace（PromptBuilder）与 skill 工具注册在那之前就需要用到，
    属有状态对象、不可重新构造，由 lifespan 显式向后传递。
    """
    storage = app.state.storage

    # --- Skills ---
    builtin_skills_dir = packaged_defaults().builtin_skills_dir()
    workspace_path = (
        Path(settings.workspace_dir).resolve() if settings.workspace_dir != "." else None
    )
    # 修撰效果门(迭代 6,ADR-0007)配对评估:子进程经此 env 重定向到变体技能库(同 personas 机制)
    user_skills_dir = runtime_skills_target()
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    if not hasattr(app.state, "challenger_router"):
        wire_evolution_services(app, settings, skill_target=user_skills_dir)
    skills = SkillsLoader(
        builtin_dir=builtin_skills_dir,
        workspace_dir=workspace_path,
        user_dir=user_skills_dir,
        char_budget=settings.skills_char_budget,
    )
    metrics_store = SkillMetricsStore(storage._conn)
    # register_skill_tools 移到 config_manager 定义之后（它依赖 config_manager.agent_config）
    return skills, metrics_store


def wire_skill_tools(
    app: FastAPI,
    settings: TianshuSettings,
    tools: ToolRegistry,
    skills: SkillsLoader,
    metrics_store: SkillMetricsStore,
) -> None:
    """注册 skill 工具——依赖 config_manager.agent_config，须晚于 wire_llm_config。"""
    config_manager = app.state.config_manager
    event_bus = app.state.event_bus

    # skill 工具注册：依赖 config_manager.agent_config（guard 开关）；
    # tools / skills / metrics_store / event_bus 均已在前面定义
    register_skill_tools(
        tools,
        skills,
        metrics_store=metrics_store,
        guard_agent_created=config_manager.agent_config.skill_guard_agent_created,
        event_bus=event_bus,
    )


def wire_skill_curator(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 SkillCurator（修撰）——周期性技能库自优化。"""
    provider_manager = app.state.provider_manager
    skills = app.state.skills_loader
    metrics_store = app.state.skill_metrics_store
    storage = app.state.storage
    config_manager = app.state.config_manager
    event_bus = app.state.event_bus

    # --- SkillCurator (修撰) — 周期性技能库自优化 ---
    # 修撰效果门(迭代 6,ADR-0007):惰性配对评估器——eval_harness 在 wire_universe 才装配
    from tianshu.skills.effect_gate import SkillEffectEvaluator

    skill_curator = SkillCurator(
        llm_client=provider_manager.get_client(),
        loader=skills,
        metrics_store=metrics_store,
        storage=storage,
        config_manager=config_manager,
        runtime_dir=Path("~/.tianshu/runtime").expanduser(),
        effect_evaluator=SkillEffectEvaluator(app, config_manager, skills),
    )
    skill_curator.attach_event_bus(event_bus)
    app.state.skill_curator = skill_curator


def wire_skills_watcher(app: FastAPI, settings: TianshuSettings) -> SkillsWatcher | None:
    """启动技能热重载 watcher。

    返回 skills_watcher（或 None）：从未写入 app.state，原代码里就是
    lifespan 的局部变量，关停时在 yield 之后直接引用；由 lifespan 显式
    持有以保持这条行为不变。
    """
    skills = app.state.skills_loader

    # --- Skills hot-reload watcher ---
    skills_watcher = SkillsWatcher(skills)
    try:
        skills_watcher.start()
    except Exception:
        logger.warning("SkillsWatcher failed to start (watchdog may not be installed)")
        return None
    return skills_watcher
