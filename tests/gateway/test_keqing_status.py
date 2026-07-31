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


def _app(gateway_enabled: bool = False):
    app = FastAPI()
    app.include_router(keqing_router)
    app.state.config_manager = SimpleNamespace(
        agent_config=SimpleNamespace(keqing_gateway_enabled=gateway_enabled)
    )
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
            }

    def test_pi_exposes_capabilities_and_pinned_version(self):
        data = _app().get("/keqing/status").json()["data"]
        pi = next(b for b in data["backends"] if b["backend"] == "pi")
        assert pi["pinned_version"] == "0.81.1"
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
        assert _pinned_version("pi") == "0.81.1"
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
