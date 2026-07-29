"""结构化 Doctor 的只读性、canonical 输出、脱敏与检查语义（G1.5）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tianshu.config import TianshuSettings
from tianshu.diagnostics import (
    SCHEMA_VERSION,
    DoctorCheck,
    ReadinessInputs,
    _resolve_effective_llm_config,
    _safe_evidence,
    assess_readiness,
    run_doctor_checks,
)

_EXPECTED_CHECK_IDS = [
    "runtime.profile",
    "auth.mode",
    "provider.config",
    "database.connectivity",
    "database.migrations",
    "resources.package",
    "overlay.writable",
    "workspace.git",
    "server.port",
    "server.live",
    "server.ready",
    "sandbox.capability",
    "network.search_provider",
    "mcp.integration",
    "optional.feishu",
    "optional.telegram",
    "universe.repo_root",
    "evals.repo_root",
    "build.identity",
]

_SECRET = "sk-red-team-SECRET-1234567890abcdef"


def _fresh_settings(tmp_path: Path, **overrides) -> TianshuSettings:
    home = tmp_path / "fresh home の家"
    values = dict(
        _env_file=None,
        db_path=str(home / "tianshu.db"),
        memory_dir=str(home / "memory"),
        runtime_personas_dir=str(home / "personas"),
        workspace_dir=str(tmp_path / "ws"),
        startup_profile="demo",
    )
    values.update(overrides)
    return TianshuSettings(**values)


def _tree_snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_doctor_json_is_canonical_versioned_and_order_stable(tmp_path):
    settings = _fresh_settings(tmp_path)
    a = run_doctor_checks(settings, probe_server=False).to_json()
    b = run_doctor_checks(settings, probe_server=False).to_json()
    assert a == b
    payload = json.loads(a)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert [c["id"] for c in payload["checks"]] == _EXPECTED_CHECK_IDS


def test_every_check_has_stable_contract_fields(tmp_path):
    report = run_doctor_checks(_fresh_settings(tmp_path), probe_server=False)
    for check in report.checks:
        assert check.id in _EXPECTED_CHECK_IDS
        assert check.status in ("pass", "degraded", "fail", "skipped")
        assert isinstance(check.required, bool)
        assert isinstance(check.evidence, dict)
        assert isinstance(check.remediation, str)


def test_doctor_default_is_read_only_and_never_constructs_llm_client(tmp_path, monkeypatch):
    import tianshu.llm as llm_module

    def _boom(*a, **k):  # pragma: no cover - 触发即失败
        raise AssertionError("Doctor must not construct LLMClient by default")

    monkeypatch.setattr(llm_module.LLMClient, "__init__", _boom)
    settings = _fresh_settings(tmp_path)
    before = _tree_snapshot(tmp_path)
    run_doctor_checks(settings, probe_server=False)
    after = _tree_snapshot(tmp_path)
    assert before == after, "Doctor 默认必须只读：目录树前后一致"
    assert not Path(settings.db_path).expanduser().exists(), "Doctor 不得创建数据库"


def test_fresh_home_missing_database_is_diagnosed_without_creation(tmp_path):
    report = run_doctor_checks(_fresh_settings(tmp_path), probe_server=False)
    by_id = {c.id: c for c in report.checks}
    assert by_id["database.connectivity"].status == "degraded"
    assert by_id["database.connectivity"].evidence == {"exists": False, "created": False}
    assert by_id["database.migrations"].status == "skipped"


def test_existing_database_passes_and_pending_migration_fails(tmp_path):
    from tianshu.storage import Storage

    db = tmp_path / "existing.db"
    storage = Storage(str(db))
    storage.init_db()
    storage.close()
    settings = _fresh_settings(tmp_path, db_path=str(db))
    report = run_doctor_checks(settings, probe_server=False)
    by_id = {c.id: c for c in report.checks}
    assert by_id["database.connectivity"].status == "pass"
    assert by_id["database.migrations"].status == "pass"

    # 删掉最后一条 ledger 行制造 pending —— Doctor 只读检出而不修复
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute(
        "DELETE FROM schema_migrations WHERE version = (SELECT MAX(version) FROM schema_migrations)"
    )
    conn.commit()
    conn.close()
    report2 = run_doctor_checks(settings, probe_server=False)
    by_id2 = {c.id: c for c in report2.checks}
    assert by_id2["database.migrations"].status == "fail"
    assert by_id2["database.migrations"].evidence["pending_count"] >= 1


@pytest.mark.parametrize(
    "field, value",
    [
        ("llm_api_key", _SECRET),
        ("llm_api_base", f"https://api.example.com/v1?token={_SECRET}"),
        ("db_path", f"~/{_SECRET}/tianshu.db"),
    ],
)
def test_red_team_secrets_never_appear_in_report(tmp_path, field, value):
    settings = _fresh_settings(tmp_path, startup_profile="live", **{field: value})
    report = run_doctor_checks(settings, probe_server=False)
    blob = report.to_json() + report.to_table_text()
    assert _SECRET not in blob
    assert _SECRET[:12] not in blob, "secret 前缀也不得出现"


def test_demo_profile_provider_check_passes_without_key(tmp_path):
    report = run_doctor_checks(_fresh_settings(tmp_path, llm_api_key=""), probe_server=False)
    by_id = {c.id: c for c in report.checks}
    assert by_id["provider.config"].status == "pass"
    assert by_id["provider.config"].evidence["profile"] == "demo"


def test_live_profile_without_key_fails_provider_check(tmp_path):
    report = run_doctor_checks(
        _fresh_settings(tmp_path, startup_profile="live", llm_api_key=""), probe_server=False
    )
    by_id = {c.id: c for c in report.checks}
    assert by_id["provider.config"].status == "fail"
    assert by_id["provider.config"].required is True


def test_repo_root_checks_warn_when_unconfigured_outside_git(tmp_path, monkeypatch):
    import tianshu as pkg

    fake_pkg_file = tmp_path / "site" / "tianshu" / "__init__.py"
    fake_pkg_file.parent.mkdir(parents=True)
    fake_pkg_file.write_text("")
    monkeypatch.setattr(pkg, "__file__", str(fake_pkg_file))
    report = run_doctor_checks(_fresh_settings(tmp_path), probe_server=False)
    by_id = {c.id: c for c in report.checks}
    for check_id in ("universe.repo_root", "evals.repo_root"):
        assert by_id[check_id].status == "degraded"
        assert by_id[check_id].evidence == {"explicit": False, "inferred_is_git": False}
        assert "REPO_ROOT" in by_id[check_id].remediation


def test_evidence_allowlist_rejects_paths_and_long_values():
    with pytest.raises(ValueError):
        _safe_evidence({"path": "/Users/someone/secret"})
    with pytest.raises(ValueError):
        _safe_evidence({"blob": "x" * 65})
    with pytest.raises(ValueError):
        _safe_evidence({"obj": object()})
    assert _safe_evidence({"ok": True, "n": 3, "tag": "demo"}) == {
        "ok": True,
        "n": 3,
        "tag": "demo",
    }


# --- readiness 纯评估 ---


def _inputs(**overrides) -> ReadinessInputs:
    values = dict(
        database_ok=lambda: True,
        migrations_current=lambda: True,
        scheduler_ready=lambda: True,
        worker_ready=lambda: True,
        outbox_ready=lambda: True,
        dispatcher_ready=lambda: True,
        decision_ready=lambda: True,
        attempt_ready=lambda: True,
        artifact_ready=lambda: True,
        delivery_ready=lambda: True,
        resources_ok=lambda: True,
        provider_ready=lambda: True,
        provider_profile=lambda: "demo",
        workspace_ready=lambda: True,
        evolution_rollback_ready=lambda: True,
        optional_integrations=lambda: {"mcp": None},
    )
    values.update(overrides)
    return ReadinessInputs(**values)


def test_readiness_all_pass_is_ready():
    report = assess_readiness(_inputs())
    assert report.status == "ready"
    assert report.to_summary_dict() == {"schema_version": SCHEMA_VERSION, "status": "ready"}


@pytest.mark.parametrize(
    "field",
    [
        "database_ok",
        "migrations_current",
        "scheduler_ready",
        "worker_ready",
        "outbox_ready",
        "dispatcher_ready",
        "decision_ready",
        "attempt_ready",
        "artifact_ready",
        "delivery_ready",
        "resources_ok",
        "workspace_ready",
        "provider_ready",
    ],
)
def test_each_required_failure_is_not_ready(field):
    report = assess_readiness(_inputs(**{field: lambda: False}))
    assert report.status == "not_ready"


def test_required_callback_exception_is_not_ready():
    def _raise() -> bool:
        raise RuntimeError("boom")

    report = assess_readiness(_inputs(database_ok=_raise))
    assert report.status == "not_ready"


def test_required_probe_exception_leaves_error_evidence():
    """探针抛错必须留下证据；静默当成 False 会让"探针本身坏了"和"依赖坏了"混为一谈。"""

    def _raise() -> bool:
        raise RuntimeError("boom")

    report = assess_readiness(_inputs(database_ok=_raise))
    database = next(c for c in report.checks if c.id == "database")
    assert database.status == "fail"
    assert database.evidence.get("probe_error") is True


def test_optional_probe_exception_surfaces_instead_of_vanishing():
    """可选探针抛错时，检查绝不能整个消失（那正是 MCP 假属性 bug 的隐身衣）。"""

    def _raise() -> dict[str, bool | None]:
        raise AttributeError("'MCPServerSession' object has no attribute 'is_connected'")

    report = assess_readiness(_inputs(optional_integrations=_raise))
    optional_ids = {c.id for c in report.checks if c.id.startswith("optional.")}
    assert optional_ids, "可选探针异常不得让 optional 检查蒸发"
    assert report.status == "degraded"
    probe = next(c for c in report.checks if c.id == "optional.probe")
    assert probe.status == "degraded" and probe.evidence.get("probe_error") is True


def test_unusable_provider_is_not_ready():
    """provider 的判定源是真实可用性（provider_ready），不是 profile 字符串回显。"""
    report = assess_readiness(_inputs(provider_ready=lambda: False))
    assert report.status == "not_ready"
    provider = next(c for c in report.checks if c.id == "provider")
    assert provider.status == "fail" and provider.required is True


def test_unknown_profile_label_does_not_fake_provider_health():
    report = assess_readiness(_inputs(provider_profile=lambda: None))
    provider = next(c for c in report.checks if c.id == "provider")
    assert provider.evidence["profile"] == "unknown"


def test_optional_integration_failure_degrades_only():
    report = assess_readiness(_inputs(optional_integrations=lambda: {"mcp": False}))
    assert report.status == "degraded"
    check = {c.id: c for c in report.checks}["optional.mcp"]
    assert check.status == "degraded"
    assert check.required is False


def test_unconfigured_optional_integration_is_skipped_not_degraded():
    report = assess_readiness(_inputs(optional_integrations=lambda: {"mcp": None}))
    assert report.status == "ready"


def test_summary_dict_never_contains_check_details():
    report = assess_readiness(_inputs(database_ok=lambda: False))
    summary = report.to_summary_dict()
    assert set(summary) == {"schema_version", "status"}


def test_doctor_check_is_immutable():
    check = DoctorCheck(id="x", status="pass", required=False)
    with pytest.raises(AttributeError):
        check.status = "fail"  # type: ignore[misc]


# --- S1.4 审查修复：只读真值 / sandbox 探测真值 / remediation env 名真值 ---


def _real_wal_database(path: Path) -> None:
    """用生产 Storage 建库并干净关闭（WAL 模式，关闭后无 -wal/-shm 残留）。"""
    from tianshu.storage import Storage

    storage = Storage(str(path))
    storage.init_db()
    storage.close()


def test_doctor_never_materializes_wal_sidecar_files(tmp_path):
    home = tmp_path / "fresh home の家"
    home.mkdir(parents=True)
    db_path = home / "tianshu.db"
    _real_wal_database(db_path)
    sidecars_before = [p.name for p in home.iterdir() if p.name.startswith("tianshu.db-")]
    assert sidecars_before == [], "前提：干净关闭的库不应有 -wal/-shm"

    settings = _fresh_settings(tmp_path)
    before = _tree_snapshot(tmp_path)
    run_doctor_checks(settings, probe_server=False)
    after = _tree_snapshot(tmp_path)
    assert before == after, "Doctor 只读契约：不得落盘 -wal/-shm 等任何文件"
    sidecars_after = [p.name for p in home.iterdir() if p.name.startswith("tianshu.db-")]
    assert sidecars_after == [], f"Doctor 物化了 WAL sidecar: {sidecars_after}"


def test_healthy_database_in_read_only_directory_is_not_failed(tmp_path):
    import os

    home = tmp_path / "fresh home の家"
    home.mkdir(parents=True)
    db_path = home / "tianshu.db"
    _real_wal_database(db_path)
    settings = _fresh_settings(tmp_path)
    os.chmod(home, 0o555)
    try:
        report = run_doctor_checks(settings, probe_server=False)
    finally:
        os.chmod(home, 0o755)
    by_id = {c.id: c for c in report.checks}
    assert by_id["database.connectivity"].status != "fail", "只读目录下的健康库不得误判 fail"
    assert by_id["database.migrations"].status != "fail"


def test_sandbox_capability_reflects_real_probe_fields(tmp_path, monkeypatch):
    import tianshu.executor.capabilities as caps

    real = caps.probe_host_capabilities()
    monkeypatch.setattr(
        caps,
        "probe_host_capabilities",
        lambda: real.model_copy(update={"sandbox_backend": "docker"}),
    )
    report = run_doctor_checks(_fresh_settings(tmp_path), probe_server=False)
    sandbox = next(c for c in report.checks if c.id == "sandbox.capability")
    assert sandbox.evidence["container_hint"] is True, (
        "container_hint 必须反映真实 probe 字段（sandbox_backend），不得恒为 False"
    )

    monkeypatch.setattr(
        caps,
        "probe_host_capabilities",
        lambda: real.model_copy(update={"sandbox_backend": None}),
    )
    report_none = run_doctor_checks(_fresh_settings(tmp_path), probe_server=False)
    sandbox_none = next(c for c in report_none.checks if c.id == "sandbox.capability")
    assert sandbox_none.evidence["container_hint"] is False


def test_wal_without_shm_is_degraded_and_never_materializes_shm(tmp_path):
    """有 -wal 无 -shm（备份/崩溃残留）：读它必须物化 -shm，故 Doctor 只能 degraded。

    绝不可改用 immutable=1 兜底——那会静默丢弃 WAL 里已提交的帧，
    让 Doctor 给出比事实更强的结论。
    """
    home = tmp_path / "fresh home の家"
    home.mkdir(parents=True)
    db_path = home / "tianshu.db"
    _real_wal_database(db_path)
    (home / "tianshu.db-wal").write_bytes(b"\x00" * 4096)  # 未回放的 WAL 帧

    settings = _fresh_settings(tmp_path)
    before = _tree_snapshot(tmp_path)
    report = run_doctor_checks(settings, probe_server=False)
    after = _tree_snapshot(tmp_path)
    assert before == after, "不得物化 -shm（只读契约）"
    assert not (home / "tianshu.db-shm").exists()

    by_id = {c.id: c for c in report.checks}
    assert by_id["database.connectivity"].status == "degraded"
    assert by_id["database.connectivity"].evidence.get("wal_pending") is True
    assert by_id["database.migrations"].status == "degraded"


def test_clean_database_snapshot_mode_is_reported_honestly(tmp_path):
    """immutable 快照分支存在 TOCTOU（读期间写者启动则快照陈旧）——证据须如实标注模式。"""
    home = tmp_path / "fresh home の家"
    home.mkdir(parents=True)
    _real_wal_database(home / "tianshu.db")
    report = run_doctor_checks(_fresh_settings(tmp_path), probe_server=False)
    by_id = {c.id: c for c in report.checks}
    assert by_id["database.connectivity"].status == "pass"
    assert by_id["database.connectivity"].evidence["snapshot_mode"] == "immutable"


# --- provider 判定源：运行时实际生效的 active 配置，而不是 env ---


def _seed_active_llm_config(db_path, *, name: str, model: str, api_key: str) -> None:
    """app 启动前按新形态种子（经 Web UI 配置的部署形态）。

    key 已迁入加密凭证库（迁移 0020）：非空 key 经 provider 注册表落
    network_credentials(kind='llm_provider')，配置行只存 provider_id。
    需要 TIANSHU_SECRET_MASTER_KEY 在场（见 _master_key_env fixture）。
    """
    from tianshu.providers.model_catalog import ModelCatalog
    from tianshu.providers.registry import ModelProviderRegistry
    from tianshu.secrets.store import CredentialStore
    from tianshu.secrets.vault import get_vault
    from tianshu.storage import Storage

    storage = Storage(str(db_path))
    storage.init_db()
    provider_id = ""
    if api_key:
        vault = get_vault()
        assert vault is not None, "seeding a key requires TIANSHU_SECRET_MASTER_KEY"
        registry = ModelProviderRegistry(storage, ModelCatalog(), CredentialStore(storage, vault))
        provider_id = registry.create_provider(profile_id="openai", api_key=api_key)["id"]
    storage.save_llm_config(
        {
            "name": name,
            "model": model,
            "provider_id": provider_id,
            "api_base": "",
            "max_retries": 3,
            "temperature": 0.7,
            "top_p": 1.0,
            "max_tokens": 4096,
            "enabled": True,
            "is_active": True,
        }
    )
    storage.close()


@pytest.fixture
def _master_key_env(monkeypatch):
    """临时主密钥 + vault 单例隔离（种子加密凭证与 doctor 判定都需要它在场）。"""
    from cryptography.fernet import Fernet

    from tianshu.secrets.vault import reset_vault

    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_vault()
    yield
    reset_vault()


def test_provider_check_reads_active_db_config_not_env(tmp_path, _master_key_env):
    """env 空但 active 配置的 key 在加密凭证库可解 → 运行时可用 → pass。"""
    db = tmp_path / "configured.db"
    _seed_active_llm_config(db, name="web-config", model="gpt-4o-mini", api_key="sk-real")
    settings = _fresh_settings(
        tmp_path,
        db_path=str(db),
        startup_profile="live",
        llm_api_key="",
        llm_model="",
    )
    provider = {c.id: c for c in run_doctor_checks(settings, probe_server=False).checks}[
        "provider.config"
    ]
    assert provider.status == "pass"
    assert provider.evidence["config_source"] == "db_active"


def test_provider_check_fails_when_active_db_config_lost_its_key(tmp_path):
    """env 有 key 但运行时 active 配置的 key 是空的 → 每条 Edict 都会炸 → fail。"""
    db = tmp_path / "cleared.db"
    _seed_active_llm_config(db, name="web-config", model="gpt-4o-mini", api_key="")
    settings = _fresh_settings(
        tmp_path,
        db_path=str(db),
        startup_profile="live",
        llm_api_key="sk-env-key",
        llm_model="gpt-4o-mini",
    )
    provider = {c.id: c for c in run_doctor_checks(settings, probe_server=False).checks}[
        "provider.config"
    ]
    assert provider.status == "fail"
    assert provider.evidence["config_source"] == "db_active"


def test_multi_active_rows_pick_the_same_config_as_config_manager(tmp_path):
    """多个 is_active 行时，Doctor 必须选中 ConfigManager 运行时会选中的那一个。

    ConfigManager 经 list_llm_configs()（ORDER BY is_active DESC, name ASC）遍历，
    _active_name 被**最后**一个 is_active 行覆盖。Doctor 若取第一行，就会对同一份
    持久化事实给出与运行时相反的结论（例如运行时用的是没 key 的那份，Doctor 却报 pass）。
    """
    from tianshu.config_manager import ConfigManager, LLMConfigState
    from tianshu.storage import Storage

    db = tmp_path / "multi-active.db"
    storage = Storage(str(db))
    storage.init_db()
    # 两行都 is_active：按 (is_active DESC, name ASC)，"b-no-key" 是最后一行 → 运行时选它
    for name, api_key in (("a-has-key", "sk-real"), ("b-no-key", "")):
        storage.save_llm_config(
            {
                "name": name,
                "model": "gpt-4o-mini",
                "api_key": api_key,
                "api_base": "",
                "max_retries": 3,
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
                "enabled": 1,
                "is_active": 1,
            }
        )
    # initial 是 env 种子（空库首启用）；库里已有行，_load_from_db 会覆盖 active
    env_seed = LLMConfigState(name="env", model="gpt-4o-mini", api_key="sk-env-key")
    runtime_active = ConfigManager(env_seed, storage=storage).state
    storage.close()

    settings = _fresh_settings(
        tmp_path,
        db_path=str(db),
        startup_profile="live",
        llm_api_key="sk-env-key",
        llm_model="gpt-4o-mini",
    )
    effective, source = _resolve_effective_llm_config(settings)
    assert source == "db_active"
    assert effective.name == runtime_active.name, (
        "Doctor 选中的配置必须与 ConfigManager 运行时选中的一致"
    )

    provider = {c.id: c for c in run_doctor_checks(settings, probe_server=False).checks}[
        "provider.config"
    ]
    expected = "pass" if runtime_active.api_key else "fail"
    assert provider.status == expected


def test_provider_check_uses_env_seed_on_first_boot(tmp_path):
    """空库首启：ConfigManager 会用 env 种子建配置，故此时 env 就是运行时真值。"""
    settings = _fresh_settings(
        tmp_path, startup_profile="live", llm_api_key="sk-env", llm_model="gpt-4o-mini"
    )
    provider = {c.id: c for c in run_doctor_checks(settings, probe_server=False).checks}[
        "provider.config"
    ]
    assert provider.status == "pass"
    assert provider.evidence["config_source"] == "env_seed"


def test_repo_root_remediation_names_real_environment_variables(tmp_path, monkeypatch):
    """remediation 里的 env 名必须真实可用（settings extra=ignore 会静默吞掉错名）。"""
    import tianshu.diagnostics as diag

    # 强制走"推断路径不是 git 仓库"的 wheel 部署分支，否则开发树永远 pass
    monkeypatch.setattr(diag, "_inferred_repo_root", lambda: tmp_path / "not-a-git-tree")
    settings = _fresh_settings(tmp_path, universe_repo_root="", eval_repo_root="")
    report = run_doctor_checks(settings, probe_server=False)
    by_id = {c.id: c for c in report.checks}
    valid_env_names = {f"TIANSHU_{name.upper()}" for name in TianshuSettings.model_fields}
    for check_id in ("universe.repo_root", "evals.repo_root"):
        check = by_id[check_id]
        assert check.status == "degraded", check_id
        quoted = [word for word in check.remediation.split() if word.startswith("TIANSHU_")]
        assert quoted, f"{check_id} 的 remediation 未给出 env 名"
        for env_name in quoted:
            assert env_name in valid_env_names, (
                f"{check_id} 的 remediation 指向不存在的环境变量 {env_name}"
            )
