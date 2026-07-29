"""liveness/readiness 分离契约（G1.5 E 段）。"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(tmp_path / "health.db"))
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "demo")
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


async def test_legacy_health_and_live_answer_200(client):
    c, _ = client
    legacy = await c.get("/health")
    assert legacy.status_code == 200 and legacy.json() == {"status": "ok"}
    live = await c.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"schema_version": "1", "status": "live"}


async def test_ready_reports_ready_with_profile_detail_in_trusted_local(client):
    c, _ = client
    resp = await c.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["schema_version"] == "1"
    assert body["profile"] == "demo"
    ids = {check["id"] for check in body["checks"]}
    assert {
        "database",
        "migrations",
        "scheduler",
        "worker",
        "outbox",
        "resources",
        "workspace",
        "provider",
    } <= ids


async def test_liveness_stays_200_when_every_dependency_is_unready(client):
    c, app = client
    app.state.storage.close()  # database/migrations 回调将抛错
    live = await c.get("/health/live")
    assert live.status_code == 200


@pytest.mark.parametrize(
    "break_state",
    ["scheduler", "worker", "workspace", "database", "provider"],
)
async def test_each_required_readiness_failure_returns_503_independently(
    client, break_state, monkeypatch
):
    c, app = client
    if break_state == "scheduler":
        app.state.scheduler._running = False
    elif break_state == "worker":
        app.state.worker_pool._closed = True
    elif break_state == "workspace":
        app.state.workspace_service._closing = True
    elif break_state == "database":
        app.state.storage.close()
    elif break_state == "provider":
        # 真实失败态：live 档位但没有凭证（不伪造组件无法产生的状态）
        app.state.settings = app.state.settings.model_copy(
            update={"startup_profile": "live", "llm_api_key": "", "llm_model": ""}
        )
    resp = await c.get("/health/ready")
    assert resp.status_code == 503, break_state
    assert resp.json()["status"] == "not_ready"


async def test_worker_shutdown_flips_readiness(client):
    c, app = client
    assert app.state.worker_pool.is_ready
    await app.state.worker_pool.shutdown()
    assert not app.state.worker_pool.is_ready
    resp = await c.get("/health/ready")
    assert resp.status_code == 503


async def test_scheduler_dead_background_task_flips_readiness(client):
    c, app = client
    scheduler = app.state.scheduler
    assert scheduler.is_ready
    scheduler._review_timeout_task.cancel()
    import asyncio

    await asyncio.sleep(0)
    assert not scheduler.is_ready
    resp = await c.get("/health/ready")
    assert resp.status_code == 503


async def test_outbox_unexpected_exit_flips_readiness_but_not_liveness(client):
    import asyncio

    c, app = client
    task = app.state.outbox_task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert not app.state.outbox_lifecycle.is_ready
    ready = await c.get("/health/ready")
    assert ready.status_code == 503
    outbox = next(check for check in ready.json()["checks"] if check["id"] == "outbox")
    assert outbox == {
        "id": "outbox",
        "status": "fail",
        "required": True,
        "evidence": {"ok": False},
    }
    assert (await c.get("/health/live")).status_code == 200


async def test_internal_delivery_unexpected_exit_flips_readiness_but_not_liveness(client):
    c, app = client
    task = app.state.internal_delivery_task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    ready = await c.get("/health/ready")
    assert ready.status_code == 503
    checks = {check["id"]: check for check in ready.json()["checks"]}
    assert set(checks) >= {
        "database",
        "migrations",
        "outbox",
        "dispatcher",
        "decision",
        "attempt",
        "artifact",
        "delivery",
    }
    assert checks["delivery"]["status"] == "fail"
    assert (await c.get("/health/live")).status_code == 200


def _bootstrap_hash(token: str = "bootstrap-token-for-tests") -> str:
    return f"sha256:{hashlib.sha256(token.encode()).hexdigest()}"


@pytest.fixture
async def secure_client(tmp_path, monkeypatch):
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(tmp_path / "health.db"))
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "demo")
    monkeypatch.setenv("TIANSHU_SECURITY_MODE", "secure-remote")
    monkeypatch.setenv("TIANSHU_PUBLIC_BASE_URL", "https://tianshu.example.com")
    monkeypatch.setenv("TIANSHU_ALLOWED_HOSTS", "tianshu.example.com")
    monkeypatch.setenv("TIANSHU_ALLOWED_ORIGINS", "https://tianshu.example.com")
    monkeypatch.setenv("TIANSHU_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("TIANSHU_AUTH_BOOTSTRAP_TOKEN_HASH", _bootstrap_hash())
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://tianshu.example.com") as c:
            yield c, app


async def test_secure_remote_anonymous_ready_body_is_summary_only(secure_client):
    c, app = secure_client
    resp = await c.get("/health/ready")
    assert resp.status_code == 200
    assert set(resp.json()) == {"schema_version", "status"}

    # required 失败时同样只暴露 summary（503 也不泄内部细节）
    app.state.worker_pool._closed = True
    resp503 = await c.get("/health/ready")
    assert resp503.status_code == 503
    assert set(resp503.json()) == {"schema_version", "status"}
    blob = resp503.text
    assert "scheduler" not in blob and "workspace" not in blob and "profile" not in blob


# --- S1.4 审查修复：provider readiness 真值 / 认证分级 / degraded 可达 / HEAD ---


@pytest.fixture
async def live_no_credentials_client(tmp_path, monkeypatch):
    """live 档位但没有任何 provider 凭证——必须 not_ready（brief E.2）。"""
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(tmp_path / "live-nocreds.db"))
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "live")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TIANSHU_LLM_API_KEY", "")
    monkeypatch.setenv("TIANSHU_LLM_MODEL", "")
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


async def test_live_profile_without_credentials_is_not_ready(live_no_credentials_client):
    c, _ = live_no_credentials_client
    resp = await c.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    provider = next(check for check in body["checks"] if check["id"] == "provider")
    assert provider["status"] == "fail"


async def test_readiness_provider_agrees_with_doctor_provider_config(
    live_no_credentials_client, client
):
    """一致性守卫：readiness 的 provider 结论必须与同 settings 的 doctor provider.config 一致。"""
    from tianshu.diagnostics import run_doctor_checks

    for fixture in (live_no_credentials_client, client):
        c, app = fixture
        resp = await c.get("/health/ready")
        readiness = next(x for x in resp.json()["checks"] if x["id"] == "provider")
        report = run_doctor_checks(app.state.settings, probe_server=False)
        doctor = next(x for x in report.checks if x.id == "provider.config")
        readiness_ok = readiness["status"] == "pass"
        doctor_ok = doctor.status == "pass"
        assert readiness_ok == doctor_ok, (
            f"readiness/doctor 对 provider 结论分叉: {readiness['status']} vs {doctor.status}"
        )


async def test_secure_remote_authenticated_caller_receives_check_detail(secure_client):
    c, _ = secure_client
    resp = await c.get(
        "/health/ready", headers={"Authorization": "Bearer bootstrap-token-for-tests"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body, "已认证调用方必须拿到检查详情（否则运维无法定位 503 原因）"
    assert {x["id"] for x in body["checks"]} >= {"database", "scheduler", "provider"}


@pytest.fixture
async def remote_peer_client(tmp_path, monkeypatch):
    """trusted-local 档位下的非 loopback 未认证调用方（容器网络 peer 场景）。"""
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(tmp_path / "health.db"))
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "demo")
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app, client=("203.0.113.9", 41000))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


async def test_trusted_local_unauthenticated_peer_gets_summary_only(remote_peer_client):
    c, _ = remote_peer_client
    resp = await c.get("/health/ready")
    assert resp.status_code == 200
    assert set(resp.json()) == {"schema_version", "status"}, "未认证调用方不得看到内部检查细节"


async def test_trusted_local_loopback_owner_still_receives_detail(client):
    c, _ = client
    body = (await c.get("/health/ready")).json()
    assert "checks" in body and body["profile"] == "demo"


async def test_head_probes_on_health_endpoints_are_allowed(client):
    c, _ = client
    for path in ("/health/live", "/health/ready"):
        resp = await c.head(path)
        assert resp.status_code != 405, f"{path} 在 auth 白名单登记了 HEAD，却未注册 HEAD 路由"


# --- S1.4 第二轮修复：provider 判定源 = 运行时 active 配置；MCP 真实健康信号 ---


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
async def live_db_configured_client(tmp_path, monkeypatch):
    """live + env 无凭证，但 llm_configs 的 active 配置可用（Web UI 配置的典型部署）。

    运行时 ProviderManager 走 ConfigManager.state（=DB active 行）——完全可用，
    绝不能被判 not_ready（否则 k8s/LB 会永久摘除这个健康实例）。
    """
    from cryptography.fernet import Fernet

    from tianshu.secrets.vault import reset_vault

    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_vault()
    db = tmp_path / "live-db-configured.db"
    _seed_active_llm_config(db, name="web-config", model="gpt-4o-mini", api_key="sk-real-key")
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(db))
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "live")
    monkeypatch.setenv("TIANSHU_LLM_API_KEY", "")
    monkeypatch.setenv("TIANSHU_LLM_MODEL", "")
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app
    reset_vault()


@pytest.fixture
async def live_env_key_client(tmp_path, monkeypatch):
    """live + env 有凭证（空库首启：ConfigManager 用 env 种子并加密落库）。"""
    from cryptography.fernet import Fernet

    from tianshu.secrets.vault import reset_vault

    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", Fernet.generate_key().decode())
    reset_vault()
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(tmp_path / "live-env-key.db"))
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "live")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TIANSHU_LLM_API_KEY", "sk-env-key")
    monkeypatch.setenv("TIANSHU_LLM_MODEL", "gpt-4o-mini")
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app
    reset_vault()


async def test_db_configured_live_instance_is_ready_without_env_credentials(
    live_db_configured_client,
):
    """env 空但 llm_configs 的 active 配置可用 → 必须 ready（读 env 会假不就绪）。"""
    c, _ = live_db_configured_client
    resp = await c.get("/health/ready")
    assert resp.status_code == 200, resp.text
    provider = next(x for x in resp.json()["checks"] if x["id"] == "provider")
    assert provider["status"] == "pass"


async def test_cleared_active_config_key_is_not_ready_even_when_env_has_key(live_env_key_client):
    """env 有 key 但运行时 active 配置的 key 被清空 → 必须 503（读 env 会假就绪）。"""
    c, app = live_env_key_client
    assert (await c.get("/health/ready")).status_code == 200
    cm = app.state.config_manager
    _, active_name = cm.list_configs()
    cm.update_config(active_name, api_key="")  # 持久化写库
    resp = await c.get("/health/ready")
    assert resp.status_code == 503, resp.text
    provider = next(x for x in resp.json()["checks"] if x["id"] == "provider")
    assert provider["status"] == "fail"


async def test_readiness_and_doctor_agree_under_every_runtime_config_state(
    client, live_env_key_client, live_db_configured_client, live_no_credentials_client
):
    """一致性守卫：四种运行时配置状态下，readiness 与 doctor 的 provider 结论必须同向。

    两者必须共用同一判定源（运行时实际生效的 active 配置），否则同一事实上
    给出相反结论——正是本轮 CRITICAL 的根因。
    """
    from tianshu.diagnostics import run_doctor_checks

    for fixture in (
        client,
        live_env_key_client,
        live_db_configured_client,
        live_no_credentials_client,
    ):
        c, app = fixture
        resp = await c.get("/health/ready")
        readiness = next(x for x in resp.json()["checks"] if x["id"] == "provider")
        report = run_doctor_checks(app.state.settings, probe_server=False)
        doctor = next(x for x in report.checks if x.id == "provider.config")
        assert (readiness["status"] == "pass") == (doctor.status == "pass"), (
            f"readiness/doctor 对 provider 结论分叉: {readiness['status']} vs {doctor.status}"
        )


def _real_mcp_session(app, name: str, status: str):
    """真实 MCPServerSession（禁止自造属性的桩——伪造 API 的桩正是本轮 bug 的隐身衣）。"""
    from pathlib import Path

    from tianshu.tools.mcp.client import MCPServerSession
    from tianshu.tools.mcp.config import MCPServerConfig, ToolFilter

    session = MCPServerSession(
        config=MCPServerConfig(
            name=name,
            transport="stdio",
            command="/bin/true",
            tools=ToolFilter(include=["health_probe"]),
        ),
        execution_gateway=app.state.execution_gateway,
        workspace_root=Path("."),
        security_mode="trusted-local",
    )
    session.status = status  # type: ignore[assignment]
    return session


def _install_mcp_state(
    manager, *, enabled_names: tuple[str, ...], sessions: dict, starting: dict | None = None
) -> None:
    from tianshu.tools.mcp.config import MCPConfig, MCPServerConfig, ToolFilter

    config = MCPConfig(
        mcp_servers={
            n: MCPServerConfig(
                name=n,
                transport="stdio",
                command="/bin/true",
                tools=ToolFilter(include=["health_probe"]),
            )
            for n in enabled_names
        }
    )
    object.__setattr__(manager, "_config", config)
    object.__setattr__(manager, "_sessions", sessions)
    object.__setattr__(manager, "_starting_sessions", dict(starting or {}))


def test_mcp_session_has_no_is_connected_attribute():
    """守卫：生产代码若再用 session.is_connected，AttributeError 会被吞掉、检查静默消失。"""
    from tianshu.tools.mcp.client import MCPServerSession

    assert not hasattr(MCPServerSession, "is_connected")


async def test_unhealthy_mcp_session_degrades_without_503(client):
    """degraded 层必须在生产可达：真实会话 status=error → degraded 且仍 200。"""
    c, app = client
    manager = app.state.mcp_manager
    _install_mcp_state(
        manager,
        enabled_names=("broken",),
        sessions={"broken": _real_mcp_session(app, "broken", "error")},
    )
    resp = await c.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    mcp_check = next(x for x in body["checks"] if x["id"] == "optional.mcp")
    assert mcp_check["status"] == "degraded"


async def test_enabled_mcp_server_that_never_connected_is_degraded(client):
    """启动就没连上的 server 压根不进 _sessions —— 最常见的生产故障必须可见。"""
    c, app = client
    _install_mcp_state(app.state.mcp_manager, enabled_names=("never-started",), sessions={})
    resp = await c.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    mcp_check = next(x for x in body["checks"] if x["id"] == "optional.mcp")
    assert mcp_check["status"] == "degraded", "配置了 enabled server 却零会话，必须 degraded"


async def test_mcp_still_starting_is_not_reported_as_degraded(client):
    """冷启动窗口（npx 拉包可达数十秒）里 server 既未连上也未失败。

    把"启动中"算作失败会让每次重启都先报一段假降级——健康信号必须只在真失败时
    才降级。
    """
    c, app = client
    manager = app.state.mcp_manager
    _install_mcp_state(
        manager,
        enabled_names=("slow-npx",),
        sessions={},
        starting={"slow-npx": _real_mcp_session(app, "slow-npx", "pending")},
    )
    assert manager.starting_names == ("slow-npx",)
    body = (await c.get("/health/ready")).json()
    assert body["status"] == "ready", "启动窗口不得报降级"
    mcp_check = next(x for x in body["checks"] if x["id"] == "optional.mcp")
    assert mcp_check["status"] == "pass"


async def test_connected_mcp_session_is_ready(client):
    c, app = client
    manager = app.state.mcp_manager
    _install_mcp_state(
        manager,
        enabled_names=("good",),
        sessions={"good": _real_mcp_session(app, "good", "connected")},
    )
    resp = await c.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    mcp_check = next(x for x in body["checks"] if x["id"] == "optional.mcp")
    assert mcp_check["status"] == "pass"


async def test_no_enabled_mcp_server_is_skipped_not_degraded(client):
    c, app = client
    _install_mcp_state(app.state.mcp_manager, enabled_names=(), sessions={})
    body = (await c.get("/health/ready")).json()
    assert body["status"] == "ready"
    mcp_check = next(x for x in body["checks"] if x["id"] == "optional.mcp")
    assert mcp_check["status"] == "skipped"


def _issue_pat(app, *, scopes: frozenset[str], label: str) -> str:
    from tianshu.models.principal import Principal

    issued = app.state.auth_service.issue_pat(
        Principal(id=f"user:{label}", kind="human", display_name=label, scopes=scopes),
        label=label,
        scopes=scopes,
    )
    return issued.raw_token


async def test_narrow_scope_token_gets_summary_only(secure_client):
    """detail 层要求读系统状态的 scope；scope 不足只拿摘要（不是 403）。

    正向对照不可省：否则"窄 token 只拿摘要"可能只是因为 token 压根没通过认证，
    测试会因错误的原因通过。
    """
    c, app = secure_client

    narrow = _issue_pat(app, scopes=frozenset({"mcp:read"}), label="narrow")
    resp = await c.get("/health/ready", headers={"Authorization": f"Bearer {narrow}"})
    assert resp.status_code == 200
    assert set(resp.json()) == {"schema_version", "status"}, (
        "scope 不足的调用方不得看到内部检查细节"
    )

    # 正向对照：同样是 PAT，带 api scope 就能拿到详情 —— 证明分野在 scope，不在认证
    wide = _issue_pat(app, scopes=frozenset({"api"}), label="wide")
    resp_wide = await c.get("/health/ready", headers={"Authorization": f"Bearer {wide}"})
    assert resp_wide.status_code == 200
    assert "checks" in resp_wide.json(), "足够 scope 的已认证调用方必须拿到详情"
