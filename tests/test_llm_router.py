"""LLM Router 可靠性配置化(spec P1-A / 迭代 1)——结构、注入路径与指纹重建。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.llm import LLMClient
from tianshu.llm_router import build_router, configs_fingerprint
from tianshu.providers.manager import ProviderManager


def _cfg(name: str, *, enabled: bool = True, api_key: str = "sk-x") -> LLMConfigState:
    return LLMConfigState(name=name, model=f"openai/{name}", api_key=api_key, enabled=enabled)


class TestBuildRouter:
    def test_deployments_and_fallback_chain(self):
        router = build_router([_cfg("main"), _cfg("backup")], active_name="main")
        assert router is not None
        names = {d["model_name"] for d in router.model_list}
        assert names == {"main", "backup"}
        # 每个配置的 fallback 链 = 其余启用配置(active 优先)
        assert {"main": ["backup"]} in router.fallbacks
        assert {"backup": ["main"]} in router.fallbacks

    def test_single_config_no_fallbacks(self):
        router = build_router([_cfg("only")])
        assert router is not None
        assert router.fallbacks in ([], None)

    def test_disabled_or_keyless_excluded(self):
        router = build_router([_cfg("on"), _cfg("off", enabled=False), _cfg("nokey", api_key="")])
        assert router is not None
        assert {d["model_name"] for d in router.model_list} == {"on"}

    def test_no_usable_config_returns_none(self):
        assert build_router([_cfg("off", enabled=False)]) is None

    def test_retry_policy_business_errors_zero(self):
        """业务型错误(认证/请求体/内容策略)重试 0 次——zeroclaw 语料的核心分类。"""
        router = build_router([_cfg("a"), _cfg("b")])
        rp = router.retry_policy
        assert rp.AuthenticationErrorRetries == 0
        assert rp.BadRequestErrorRetries == 0
        assert rp.ContentPolicyViolationErrorRetries == 0
        assert rp.RateLimitErrorRetries == 3


class TestFingerprint:
    def test_change_sensitivity(self):
        base = [_cfg("a"), _cfg("b")]
        assert configs_fingerprint(base) == configs_fingerprint(list(reversed(base)))
        assert configs_fingerprint(base) != configs_fingerprint([_cfg("a")])
        assert configs_fingerprint(base) != configs_fingerprint(
            [_cfg("a"), _cfg("b", enabled=False)]
        )


class _FakeRouter:
    """记录 acompletion 调用参数,返回 litellm 形状的最小响应。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="openai/main",
        )


class TestLLMClientRouterPath:
    async def test_router_receives_config_name_and_no_credentials(self):
        fake = _FakeRouter()
        client = LLMClient(
            model="openai/main",
            api_key="sk-real",
            router=fake,
            router_model_name="main",
        )
        resp = await client.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "ok"
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["model"] == "main"  # 按配置名路由
        assert "api_key" not in call  # 凭证由 deployment 自带,不透传
        assert "api_base" not in call

    async def test_without_router_name_falls_back_direct(self, monkeypatch):
        """只给 router 不给 router_model_name → 保持直连路径(向后兼容)。"""
        import tianshu.llm as llm_mod

        fake = _FakeRouter()
        called = {}

        async def fake_acompletion(**kwargs):
            called.update(kwargs)
            return await fake.acompletion(**kwargs)

        monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
        client = LLMClient(model="openai/main", api_key="sk-real", router=fake)
        await client.chat([{"role": "user", "content": "hi"}])
        assert called["model"] == "openai/main"
        assert called["api_key"] == "sk-real"


class TestProviderManagerRouterWiring:
    @pytest.fixture
    def manager(self, storage):
        cm = ConfigManager(_cfg("main"))
        cm.add_config(_cfg("backup"))
        return ProviderManager(storage, cm), cm

    def test_router_cached_until_configs_change(self, manager):
        pm, cm = manager
        r1 = pm._get_router()
        r2 = pm._get_router()
        assert r1 is r2  # 指纹未变 → 复用
        cm.update_config("backup", enabled=False)
        r3 = pm._get_router()
        assert r3 is not r1  # 配置变更 → 重建

    def test_fallback_client_carries_router(self, manager):
        pm, _ = manager
        client = pm._fallback_client()
        assert client._router is pm._get_router()
        assert client._router_model_name == "main"

    def test_unknown_config_name_gets_no_router(self, manager):
        pm, _ = manager
        assert pm._router_kwargs("ghost") == {}
