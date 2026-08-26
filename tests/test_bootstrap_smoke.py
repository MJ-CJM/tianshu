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
from types import SimpleNamespace

import pytest

from tianshu import bootstrap as bootstrap_module
from tianshu.app import create_app, lifespan
from tianshu.bootstrap.wiring_tools import _runtime_secret_resolver
from tianshu.config import TianshuSettings
from tianshu.evolution.process_snapshot import ProcessSnapshotDriftError
from tianshu.evolution.runtime_context import current_run_binding
from tianshu.models import Edict, EdictRuntime, EdictSchedule, Memorial
from tianshu.models.canonical import canonical_sha256
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage import Storage
from tianshu.storage.generation_repo import GenerationRepository

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
    "process_snapshot_report",
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

    async def test_process_snapshot_is_active_before_evolution_projection(self, booted_app):
        report = booted_app.state.process_snapshot_report
        snapshot = booted_app.state.evolution_center_service.get_snapshot(
            type("Auth", (), {"principal": type("Principal", (), {"id": "user:owner"})()})()
        )

        assert report.snapshot_digest == booted_app.state.system_snapshot_resolver.resolve().digest
        assert snapshot.active_generation == report.active_generation_id
        assert snapshot.last_good_generation == report.last_good_generation_id

    async def test_strict_process_drift_precedes_routing_audit_and_pi_recovery(
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
        plugin_dir = runtime_paths["plugins"] / "strict-ordering"
        plugin_dir.mkdir()
        (plugin_dir / "manifest.json").write_text(
            '{"name":"strict-ordering","version":"1.0.0"}',
            encoding="utf-8",
        )
        db_path = tmp_path / "strict-process-ordering.db"
        monkeypatch.setenv("TIANSHU_RUNTIME_SKILLS_DIR", str(tmp_path / "runtime-skills"))
        monkeypatch.setattr(
            "tianshu.app.bootstrap.wire_skills_watcher",
            lambda _app, _settings: None,
        )

        def snapshot(marker: str) -> SystemSnapshotV1:
            components = {"kernel": marker * 64}
            return SystemSnapshotV1(
                components=components,
                digest=canonical_sha256(components),
            )

        current_snapshot = {"value": snapshot("a")}

        def wire_snapshot(app, _settings) -> None:
            app.state.system_snapshot_resolver = SimpleNamespace(
                resolve=lambda: current_snapshot["value"]
            )

        monkeypatch.setattr(
            "tianshu.app.bootstrap.wire_system_snapshot",
            wire_snapshot,
        )

        def settings(*, strict: bool) -> TianshuSettings:
            return TianshuSettings(
                _env_file=None,
                startup_profile="demo",
                system_snapshot_strict=strict,
                evolution_routing_enabled=False,
                db_path=str(db_path),
                artifact_dir=str(runtime_paths["artifacts"]),
                memory_dir=str(runtime_paths["memory"]),
                runtime_personas_dir=str(runtime_paths["personas"]),
                log_dir=str(runtime_paths["logs"]),
                plugins_dir=str(runtime_paths["plugins"]),
                universe_root=str(runtime_paths["universes"]),
                workspace_staging_root=str(runtime_paths["workspaces"]),
            )

        first_app = create_app(settings(strict=False))
        async with lifespan(first_app):
            pass

        storage = Storage(str(db_path))
        storage.init_db()
        repository = GenerationRepository()
        manifest = {"schema_version": "1", "manifest_id": "ordering-test"}
        release_material: dict[str, object] = {
            "schema_version": 1,
            "scope": "executor:keqing:pi",
            "manifest": manifest,
            "manifest_hash": canonical_sha256(manifest),
            "cli_version": "0.83.0",
            "cli_version_source": "package_json",
            "binary_path": "/opt/tianshu/bin/pi",
            "binary_digest": "b" * 64,
            "package_name": "@earendil-works/pi-coding-agent",
            "package_entrypoint": "dist/cli.js",
            "package_digest": "c" * 64,
            "single_argv_shape": "single-v1",
            "session_argv_shape": "session-v1",
            "pi_wire_version": 3,
            "materializer_id": "ordering-test",
            "materializer_version": "1",
        }
        release = RuntimeReleaseV1(
            **release_material,
            release_digest=canonical_sha256(release_material),
        )
        now = datetime(2026, 8, 27, tzinfo=UTC)
        pi_generation = RuntimeGenerationV1(
            generation_id="rg-" + "d" * 32,
            scope=release.scope,
            release_digest=release.release_digest,
            state=RuntimeGenerationState.STAGED,
            version=1,
            created_at=now,
            updated_at=now,
        )
        with storage.unit_of_work() as unit_of_work:
            repository.insert_release(unit_of_work.connection, release, first_seen_at=now)
            repository.insert_staged(unit_of_work.connection, pi_generation)
            unit_of_work.commit()
        storage.close()

        tables = (
            "system_snapshots",
            "runtime_generation_releases",
            "runtime_generations",
            "runtime_generation_journal",
            "generation_pointers",
            "plugins",
            "system_audit_events",
            "outbox_events",
        )

        def durable_rows() -> dict[str, list[tuple[object, ...]]]:
            connection = sqlite3.connect(db_path)
            try:
                return {
                    table: connection.execute(
                        f"SELECT * FROM {table} ORDER BY 1"  # noqa: S608
                    ).fetchall()
                    for table in tables
                }
            finally:
                connection.close()

        before = durable_rows()
        current_snapshot["value"] = snapshot("b")
        second_app = create_app(settings(strict=True))

        with pytest.raises(ProcessSnapshotDriftError):
            async with lifespan(second_app):
                raise AssertionError("strict drift must reject startup")

        assert durable_rows() == before
        pi_row = next(
            row for row in before["runtime_generations"] if row[0] == pi_generation.generation_id
        )
        assert pi_row[4] == RuntimeGenerationState.STAGED.value

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
            assert app.state.process_snapshot_report is None
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
