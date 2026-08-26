"""客卿健康状态端点 + agent_config 客卿治理字段测试。

客卿=外臣:状态页展示能力/健康/凭证来源,不含人格/京察/自进化(百官品类)。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tianshu.gateway.keqing_api import (
    _detect_installed_version,
    _pinned_version,
    keqing_router,
)
from tianshu.models.runtime_generation import RuntimeGenerationState
from tianshu.storage import Storage

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64


def _seed_executor_candidate(
    storage: Storage,
    *,
    candidate_id: str,
    updated_at: str,
    lifecycle: str = "ready",
    version: int = 1,
) -> None:
    storage._conn.execute(  # noqa: SLF001 - durable API fixture
        """
        INSERT INTO evolution_candidates (
            candidate_id, schema_version, kind, subject_key,
            provenance_json, provenance_hash, base_json, candidate_ref_json,
            diff_artifact_digest, evolution_contract_json, evolution_contract_hash,
            gate_snapshot_version, evidence_bundle_ids_json, routing_json,
            rollback_json, lifecycle, version, created_at, updated_at
        ) VALUES (?, 1, 'executor', 'executor:keqing:pi', '{}', ?, '{}', '{}',
                  ?, '{}', ?, 0, '[]', NULL, '{}', ?, ?, ?, ?)
        """,
        (
            candidate_id,
            _HASH_A,
            _HASH_B,
            _HASH_C,
            lifecycle,
            version,
            updated_at,
            updated_at,
        ),
    )
    storage._conn.commit()  # noqa: SLF001 - durable API fixture


def _app(gateway_enabled: bool = False, *, generation_controller=None):
    app = FastAPI()
    app.include_router(keqing_router)
    app.state.config_manager = SimpleNamespace(
        agent_config=SimpleNamespace(keqing_gateway_enabled=gateway_enabled)
    )
    if generation_controller is not None:
        app.state.generation_controller = generation_controller
    return TestClient(app)


class TestStatusEndpoint:
    def test_lists_all_backends_with_health(self):
        r = _app().get("/keqing/status")
        assert r.status_code == 200
        data = r.json()["data"]
        backends = {b["backend"]: b for b in data["backends"]}
        assert {"pi", "claude-code", "codex", "opencode"} <= set(backends)
        # 每个 backend 有体检字段
        for b in backends.values():
            assert set(b) >= {
                "id",
                "backend",
                "installed",
                "pinned_version",
                "version_drift",
                "capabilities",
                "credential_status",
                "generation",
                "evolution_candidate",
            }
        assert all(b["generation"] is None for b in backends.values())
        assert all(b["evolution_candidate"] is None for b in backends.values())

    def test_pi_exposes_read_only_generation_status(self):
        class ReadOnlyGenerationController:
            def __init__(self):
                self.scopes = []

            def status_for_scope(self, scope: str):
                self.scopes.append(scope)
                return SimpleNamespace(
                    id="rg-pi-active",
                    state=RuntimeGenerationState.ACTIVE,
                    active_runs=2,
                    last_good_id="rg-pi-previous",
                )

        controller = ReadOnlyGenerationController()
        data = _app(generation_controller=controller).get("/keqing/status").json()["data"]
        by_backend = {backend["backend"]: backend for backend in data["backends"]}

        assert by_backend["pi"]["generation"] == {
            "id": "rg-pi-active",
            "state": "active",
            "active_runs": 2,
            "last_good_id": "rg-pi-previous",
        }
        assert all(
            backend["generation"] is None for name, backend in by_backend.items() if name != "pi"
        )
        assert controller.scopes == ["executor:keqing:pi"]

    def test_pi_exposes_latest_executor_candidate_without_writing(self, monkeypatch):
        storage = Storage(":memory:")
        storage.init_db()
        _seed_executor_candidate(
            storage,
            candidate_id="candidate-executor-pi-old",
            updated_at="2026-08-25T00:00:00+00:00",
        )
        _seed_executor_candidate(
            storage,
            candidate_id="candidate-executor-pi-new",
            updated_at="2026-08-26T00:00:00+00:00",
            lifecycle="canary",
            version=3,
        )
        monkeypatch.setattr(
            "tianshu.gateway.keqing_api._detect_installed_version",
            lambda binary: "0.84.0" if binary == "pi" else None,
        )
        statements: list[str] = []
        storage._conn.set_trace_callback(statements.append)  # noqa: SLF001
        changes_before = storage._conn.total_changes  # noqa: SLF001
        client = _app()
        client.app.state.storage = storage

        try:
            data = client.get("/keqing/status").json()["data"]
        finally:
            storage._conn.set_trace_callback(None)  # noqa: SLF001
            changes_after = storage._conn.total_changes  # noqa: SLF001
            client.close()
            storage.close()

        by_backend = {backend["backend"]: backend for backend in data["backends"]}
        assert by_backend["pi"]["evolution_candidate"] == {
            "candidate_id": "candidate-executor-pi-new",
            "lifecycle": "canary",
            "version": 3,
        }
        assert all(
            backend["evolution_candidate"] is None
            for name, backend in by_backend.items()
            if name != "pi"
        )
        assert changes_after == changes_before
        assert not any(
            statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
            for statement in statements
        )

    def test_pi_exposes_capabilities_and_pinned_version(self):
        data = _app().get("/keqing/status").json()["data"]
        pi = next(b for b in data["backends"] if b["backend"] == "pi")
        assert pi["pinned_version"] == "0.83.0"
        caps = pi["capabilities"]
        # 客卿=外臣:能力声明(不是人格)
        assert caps["session_resume"] is True and caps["usage_reporting"] == "full"
        assert caps["permission_shaping"] == "none"  # P4 guard 后升级

    def test_only_pi_has_capabilities_declared(self):
        # 单发档(codex)本页不展示会话能力声明
        data = _app().get("/keqing/status").json()["data"]
        codex = next(b for b in data["backends"] if b["backend"] == "codex")
        assert codex["capabilities"] is None
        assert codex["pinned_version"] is None

    def test_stale_gateway_config_does_not_claim_unwired_capability(self):
        data = _app(gateway_enabled=True).get("/keqing/status").json()["data"]
        assert data["gateway_enabled"] is False
        for b in data["backends"]:
            assert b["credential_status"] == "self-managed"

    def test_production_app_does_not_mount_experimental_llm_proxy(self):
        from tianshu.app import create_app

        paths = {route.path for route in create_app().routes}
        assert "/api/keqing/llm/{provider}/v1/messages" not in paths
        assert "/api/keqing/llm/{provider}/v1/chat/completions" not in paths

    def test_default_mode_credential_is_self_managed(self, monkeypatch):
        # 客卿=外臣,自管凭证(自己 login/本地配置);天枢不管 → 默认「客卿自管」,
        # 与天枢进程 env 无关(即便 env 有 key 也不影响——凭证在客卿自己那边)。
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        data = _app(gateway_enabled=False).get("/keqing/status").json()["data"]
        for b in data["backends"]:
            assert b["credential_status"] == "self-managed"


class TestVersionDetection:
    def test_pinned_version_only_for_pi(self):
        assert _pinned_version("pi") == "0.83.0"
        assert _pinned_version("codex") is None

    def test_detect_version_none_for_missing_binary(self):
        assert _detect_installed_version("definitely-not-a-real-binary-xyz") is None


class TestAgentConfigKeqingFields:
    def test_state_roundtrip(self):
        from dataclasses import replace

        from tianshu.config_manager import AgentConfigState

        s = AgentConfigState()
        assert s.keqing_default_models == {} and s.keqing_gateway_enabled is False
        s2 = replace(
            s, keqing_default_models={"pi": "zai-coding-cn/glm-4.6"}, keqing_per_run_budget_cny=5.0
        )
        assert s2.keqing_default_models == {"pi": "zai-coding-cn/glm-4.6"}
        assert s2.keqing_per_run_budget_cny == 5.0

    def test_update_request_rejects_negative_budget(self):
        from tianshu.models.api import AgentConfigUpdateRequest

        with pytest.raises(ValidationError):
            AgentConfigUpdateRequest(keqing_per_run_budget_cny=-1)
