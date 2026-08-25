"""装配冒烟测试——app.state 服务键全量断言（拆分行为锚点）。

`tianshu.app.lifespan()` 把 ~50 个服务对象挂到 `app.state` 上。本测试在
lifespan 从单体函数拆分为 `bootstrap/` wiring 函数序列（B2-T4）之前先跑绿，
锁定"哪些键必须存在、必须非 None"这条行为基线；拆分之后再跑一次必须仍然绿，
证明装配顺序与最终状态未变。

key 清单来自对拆分前 app.py 的全量 grep：`app.state\\.[a-zA-Z_]* =`。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from tianshu import bootstrap as bootstrap_module
from tianshu.app import create_app, lifespan
from tianshu.bootstrap.wiring_tools import _runtime_secret_resolver
from tianshu.config import TianshuSettings
from tianshu.evolution.runtime_context import current_run_binding
from tianshu.models import Edict, EdictRuntime, EdictSchedule, Memorial

# 全部在 lifespan() 中被赋值、且默认测试环境下保证非 None 的 app.state 键。
NON_NULLABLE_STATE_KEYS = [
    "storage",
    "event_bus",
    "hook_registry",
    "execution_gateway",
    "mcp_manager",
    "_mcp_start_task",
    "persona_loader",
    "runtime_personas_dir",
    "template_library",
    "drawer_store",
    "memory_config",
    "config_manager",
    "provider_manager",
    "agent",
    "skills_loader",
    "skill_metrics_store",
    "tool_registry",
    "prompt_builder",
    "personas_dir",
    "worker_pool",
    "lane_manager",
    "auditor",
    "channel_registry",
    "notifier",
    "session_rule_store",
    "executor",
    "generation_controller",
    "generation_reconciler",
    "generation_recovery_report",
    "dag_scheduler",
    "approval_manager",
    "cost_manager",
    "bot_manager",
    "policy_engine",
    "policy_hook",
    "memory_manager",
    "consultation",
    "orchestrator_ctx",
    "evaluator",
    "official_selector",
    "planner",
    "scheduler",
    "plugin_api",
    "system_snapshot_resolver",
    "profile_synthesizer",
    "profile_trigger",
    "skill_curator",
    "universe_manager",
    "universe_execution_context_factory",
    "code_gate",
    "code_sandbox",
    "universe_evolver",
    "digest_generator",
    "_digest_task",
    "running_tasks",
]

# 按设计允许为 None：默认测试环境未配置 feishu/telegram，
# ChannelBotManager.get() 对未注册的实例返回 None（见 bot_manager.py）。
NULLABLE_STATE_KEYS = [
    "feishu_bot",
    "telegram_bot",
]

ALL_STATE_KEYS = [*NON_NULLABLE_STATE_KEYS, *NULLABLE_STATE_KEYS]


def test_runtime_secret_resolver_never_falls_unknown_settings_refs_back_to_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("settings:private_runtime_value", "must-not-resolve")
    resolver = _runtime_secret_resolver(TianshuSettings())

    assert resolver("settings:private_runtime_value") is None


@pytest.fixture
async def booted_app():
    app = create_app()
    async with lifespan(app):
        yield app


class TestBootstrapSmoke:
    async def test_no_missing_state_keys(self, booted_app):
        missing = [key for key in ALL_STATE_KEYS if not hasattr(booted_app.state, key)]
        assert not missing, f"app.state 缺少以下 key: {missing}"

    @pytest.mark.parametrize("key", NON_NULLABLE_STATE_KEYS)
    async def test_state_key_is_not_none(self, booted_app, key):
        assert getattr(booted_app.state, key) is not None, f"app.state.{key} 不应为 None"

    @pytest.mark.parametrize("key", NULLABLE_STATE_KEYS)
    async def test_nullable_state_key_present(self, booted_app, key):
        assert hasattr(booted_app.state, key), f"app.state.{key} 应存在（值可为 None）"

    async def test_one_execution_gateway_is_injected_into_high_risk_callers(self, booted_app):
        process_gateway = booted_app.state.execution_gateway
        assert booted_app.state.executor._execution_gateway is process_gateway
        assert booted_app.state.executor._keqing._execution_gateway is process_gateway
        assert booted_app.state.orchestrator_ctx.execution_gateway is process_gateway
        assert booted_app.state.mcp_manager._execution_gateway is process_gateway
        assert booted_app.state.code_gate.execution_gateway is process_gateway
        assert booted_app.state.code_sandbox.execution_gateway is process_gateway
        assert (
            booted_app.state.code_gate.context_factory
            is booted_app.state.code_sandbox.context_factory
            is booted_app.state.universe_execution_context_factory
        )

    async def test_system_snapshot_resolver_is_late_bound_after_router_wiring(self, booted_app):
        assert (
            booted_app.state.challenger_router._snapshot_resolver()
            is booted_app.state.system_snapshot_resolver
        )
        assert booted_app.state.scheduled_run_preparer._require_runtime_binding is True

    async def test_system_snapshot_can_be_disabled_for_the_full_lifespan(
        self,
        tmp_path,
        monkeypatch,
    ):
        runtime_paths = {
            name: tmp_path / name
            for name in (
                "artifacts",
                "memory",
                "personas",
                "logs",
                "plugins",
                "universes",
                "workspaces",
            )
        }
        for path in runtime_paths.values():
            path.mkdir()
        monkeypatch.setenv("TIANSHU_RUNTIME_SKILLS_DIR", str(tmp_path / "runtime-skills"))
        monkeypatch.setattr(
            "tianshu.app.bootstrap.wire_skills_watcher",
            lambda _app, _settings: None,
        )
        app = create_app(
            TianshuSettings(
                _env_file=None,
                system_snapshot_enabled=False,
                system_snapshot_strict=False,
                db_path=str(tmp_path / "snapshot-disabled.db"),
                artifact_dir=str(runtime_paths["artifacts"]),
                memory_dir=str(runtime_paths["memory"]),
                runtime_personas_dir=str(runtime_paths["personas"]),
                log_dir=str(runtime_paths["logs"]),
                plugins_dir=str(runtime_paths["plugins"]),
                universe_root=str(runtime_paths["universes"]),
                workspace_staging_root=str(runtime_paths["workspaces"]),
            )
        )

        async with lifespan(app):
            assert app.state.system_snapshot_resolver is None
            assert app.state.challenger_router._snapshot_resolver() is None
            assert app.state.scheduled_run_preparer._require_runtime_binding is False

            scheduled_at = datetime(2035, 1, 1, tzinfo=UTC)
            schedule = EdictSchedule(
                type="interval",
                interval_seconds=60,
                concurrency_policy="allow",
            )
            edict = Edict(
                id="snapshot-disabled-scheduled-edict",
                goal="static scheduled execution",
                schedule=schedule,
                runtime=EdictRuntime(executor="keqing:pi"),
            )
            app.state.storage.save_edict(edict)
            app.state.storage.save_scheduler_job(
                "snapshot-disabled-job",
                edict.id,
                schedule.type,
                interval_seconds=60,
                next_run=scheduled_at,
            )

            scheduled = app.state.scheduled_run_preparer.prepare(
                job_id="snapshot-disabled-job",
                scheduled_at=scheduled_at,
            )
            manual = app.state.scheduled_run_preparer.prepare_manual(
                job_id="snapshot-disabled-job",
                idempotency_key="snapshot-disabled-run-now",
                scheduled_at=scheduled_at,
            )

            assert scheduled.attempt_id is not None
            assert manual.attempt_id is not None
            assert (
                app.state.storage._conn.execute(  # noqa: SLF001
                    "SELECT COUNT(*) FROM run_system_bindings"
                ).fetchone()[0]
                == 0
            )
            assert (
                app.state.storage._conn.execute(  # noqa: SLF001
                    "SELECT COUNT(*) FROM run_generation_bindings "
                    "WHERE state='bound' AND generation_ids_json='[]'"
                ).fetchone()[0]
                == 2
            )

    async def test_blocking_generation_readiness_stops_before_attempt_claim(
        self,
        tmp_path,
        monkeypatch,
    ):
        runtime_paths = {
            name: tmp_path / name
            for name in (
                "artifacts",
                "memory",
                "personas",
                "logs",
                "plugins",
                "universes",
                "workspaces",
            )
        }
        for path in runtime_paths.values():
            path.mkdir()
        db_path = tmp_path / "generation-readiness-blocked.db"
        monkeypatch.setenv("TIANSHU_RUNTIME_SKILLS_DIR", str(tmp_path / "runtime-skills"))
        monkeypatch.setattr(
            "tianshu.app.bootstrap.wire_skills_watcher",
            lambda _app, _settings: None,
        )
        original_wire_scheduling = bootstrap_module.wire_scheduling

        def wire_scheduling_and_seed_attempt(app, settings):
            original_wire_scheduling(app, settings)
            edict = Edict(id="readiness-edict", goal="must remain claimable")
            memorial = Memorial(id="readiness-memorial", edict_id=edict.id)
            app.state.storage.save_edict(edict)
            app.state.storage.save_memorial(memorial)
            with app.state.storage.unit_of_work() as unit_of_work:
                app.state.storage.attempt_repo.enqueue_initial(
                    unit_of_work.connection,
                    memorial_id=memorial.id,
                    available_at=datetime.now(UTC),
                    max_attempts=1,
                    attempt_id="readiness-attempt",
                )
                unit_of_work.commit()

        monkeypatch.setattr(
            "tianshu.app.bootstrap.wire_scheduling",
            wire_scheduling_and_seed_attempt,
        )
        monkeypatch.setattr(
            "tianshu.evolution.reconciler.GenerationReconciler.readiness_snapshot",
            lambda _self: (False, ("generation_binding_resolver_unavailable",)),
        )
        app = create_app(
            TianshuSettings(
                _env_file=None,
                db_path=str(db_path),
                artifact_dir=str(runtime_paths["artifacts"]),
                memory_dir=str(runtime_paths["memory"]),
                runtime_personas_dir=str(runtime_paths["personas"]),
                log_dir=str(runtime_paths["logs"]),
                plugins_dir=str(runtime_paths["plugins"]),
                universe_root=str(runtime_paths["universes"]),
                workspace_staging_root=str(runtime_paths["workspaces"]),
            )
        )

        with pytest.raises(RuntimeError, match="generation control plane is unavailable"):
            async with lifespan(app):
                raise AssertionError("lifespan must not start")

        connection = sqlite3.connect(db_path)
        try:
            status = connection.execute(
                "SELECT status FROM execution_attempts WHERE attempt_id='readiness-attempt'"
            ).fetchone()[0]
        finally:
            connection.close()
        assert status == "claimable"

    @pytest.mark.parametrize("startup_profile", ("live", "demo"))
    async def test_empty_generation_full_lifespan_preserves_static_executor(
        self,
        tmp_path,
        monkeypatch,
        startup_profile,
    ):
        runtime_paths = {
            name: tmp_path / name
            for name in (
                "artifacts",
                "memory",
                "personas",
                "logs",
                "plugins",
                "universes",
                "workspaces",
            )
        }
        for path in runtime_paths.values():
            path.mkdir()
        monkeypatch.setenv("TIANSHU_RUNTIME_SKILLS_DIR", str(tmp_path / "runtime-skills"))
        monkeypatch.setattr(
            "tianshu.app.bootstrap.wire_skills_watcher",
            lambda _app, _settings: None,
        )
        app = create_app(
            TianshuSettings(
                _env_file=None,
                startup_profile=startup_profile,
                db_path=str(tmp_path / "empty-generation.db"),
                artifact_dir=str(runtime_paths["artifacts"]),
                memory_dir=str(runtime_paths["memory"]),
                runtime_personas_dir=str(runtime_paths["personas"]),
                log_dir=str(runtime_paths["logs"]),
                plugins_dir=str(runtime_paths["plugins"]),
                universe_root=str(runtime_paths["universes"]),
                workspace_staging_root=str(runtime_paths["workspaces"]),
            )
        )
        edict = Edict(
            id="edict-empty-generation",
            goal="preserve the static Pi executor",
            runtime={"executor": "keqing:pi"},
        )
        memorial = Memorial(
            id="memorial-empty-generation",
            edict_id=edict.id,
        )
        async with lifespan(app):
            app.state.storage.save_edict(edict)
            app.state.storage.save_memorial(memorial)
            app.state.challenger_router.assign(memorial.id)
            controller = app.state.generation_controller
            registry = app.state.executor.adapter_registry
            static_adapter = registry.get("keqing:pi")

            assert controller.status_for_scope("executor:keqing:pi") is None
            try:
                with app.state.challenger_router.bind_runtime(
                    memorial.id,
                    attempt_id="attempt-empty-generation",
                ):
                    binding = current_run_binding()
                    assert binding is not None
                    assert binding.generation_ids == ()
                    prepared = app.state.executor._resolve_governed_executor(  # noqa: SLF001
                        edict,
                        memorial,
                        execution_mode="single",
                    )
                    assert prepared.adapter is static_adapter
                    assert prepared.generation_ids == ()
                    assert prepared.generation_bundle is None
            finally:
                controller.release_binding("attempt-empty-generation")

            assert controller.status_for_scope("executor:keqing:pi") is None
            assert registry.get("keqing:pi") is static_adapter

    async def test_lifespan_closes_drawer_store(self):
        # 不复用 booted_app fixture：需要在 lifespan 退出*之后*断言 close 是否
        # 被调用，而 booted_app 的 teardown（lifespan __aexit__）发生在测试体
        # 结束之后，body 内看不到 teardown 的效果，因此这里手动管理上下文。
        # mock.patch 的 with 块会在其自身退出时把 close 还原，若还原发生在
        # 断言之前会掩盖调用记录，故用手动包装函数采集调用证据。
        app = create_app()
        calls: list[int] = []
        async with lifespan(app):
            drawer_store = app.state.drawer_store  # 属性不存在时此处即红
            orig_close = drawer_store.close

            def _tracking_close() -> None:
                calls.append(1)
                orig_close()

            drawer_store.close = _tracking_close
        assert calls, "lifespan 退出应调用 drawer_store.close()"

    async def test_sandbox_cleanup_failure_does_not_skip_remaining_teardown(self):
        app = create_app()
        calls: list[str] = []
        storage = None
        async with lifespan(app):
            storage = app.state.storage

            async def _failing_sandbox_shutdown() -> None:
                calls.append("sandbox")
                raise PermissionError("sandbox cleanup failed")

            original_mcp_shutdown = app.state.mcp_manager.shutdown

            async def _tracking_mcp_shutdown() -> None:
                calls.append("mcp")
                await original_mcp_shutdown()

            original_bot_stop = app.state.bot_manager.stop_all

            async def _tracking_bot_stop() -> None:
                calls.append("bots")
                await original_bot_stop()

            original_drawer_close = app.state.drawer_store.close

            def _tracking_drawer_close() -> None:
                calls.append("drawer")
                original_drawer_close()

            original_storage_close = app.state.storage.close

            def _tracking_storage_close() -> None:
                calls.append("storage")
                original_storage_close()

            app.state.code_sandbox.shutdown = _failing_sandbox_shutdown
            app.state.mcp_manager.shutdown = _tracking_mcp_shutdown
            app.state.bot_manager.stop_all = _tracking_bot_stop
            app.state.drawer_store.close = _tracking_drawer_close
            app.state.storage.close = _tracking_storage_close

        assert storage is not None
        assert calls == ["sandbox", "mcp", "bots", "drawer", "storage"]
