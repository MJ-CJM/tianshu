"""客卿 LLM 网关治理四关测试:401 无效/过期/吊销、403 模型越界、402 预算耗尽、
放行+记账+归因头。上游转发用注入替身,隔离治理逻辑。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway import llm_gateway_api as gw
from tianshu.gateway.llm_gateway_api import ForwardResult, llm_gateway_router, set_forward_fn
from tianshu.secrets.scoped_token import ScopedTokenStore


@pytest.fixture
def client(monkeypatch):
    store = ScopedTokenStore()
    monkeypatch.setattr(gw, "get_scoped_token_store", lambda: store)

    async def fake_forward(record, body, provider):
        return ForwardResult(status_code=200, content=b'{"ok":true}', cost_usd=0.5)

    set_forward_fn(fake_forward)
    app = FastAPI()
    app.include_router(llm_gateway_router)
    return TestClient(app), store


def _post(client, token, model="anthropic/opus"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/keqing/llm/anthropic/v1/messages",
        headers=headers,
        content=json.dumps({"model": model}),
    )


class TestAuth401:
    def test_missing_token(self, client):
        c, _ = client
        r = c.post("/keqing/llm/anthropic/v1/messages", content=b"{}")
        assert r.status_code == 401

    def test_unknown_token(self, client):
        c, _ = client
        assert _post(c, "tskq_bogus").status_code == 401

    def test_revoked_token(self, client):
        c, store = client
        tok = store.mint(edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=999)
        store.revoke_run("r")
        assert _post(c, tok).status_code == 401


class TestModel403:
    def test_model_not_in_allowlist(self, client):
        c, store = client
        tok = store.mint(
            edict_id="e", run_id="r", model_allowlist={"anthropic/opus"}, budget_cny=None, ttl_seconds=999
        )
        assert _post(c, tok, model="openai/gpt-9").status_code == 403
        assert _post(c, tok, model="anthropic/opus").status_code == 200


class TestBudget402:
    def test_over_budget_returns_402(self, client):
        c, store = client
        # 预算 3.6 CNY = 0.5 USD;一次 fake_forward 花 0.5 USD=3.6 CNY 即触顶
        tok = store.mint(edict_id="e", run_id="r", model_allowlist=None, budget_cny=3.6, ttl_seconds=999)
        assert _post(c, tok).status_code == 200  # 首次放行,花费后记账
        r2 = _post(c, tok)
        assert r2.status_code == 402  # 已 over-budget → 硬熔断断供


class TestPassthroughAndAttribution:
    def test_success_forwards_and_sets_attribution_headers(self, client):
        c, store = client
        tok = store.mint(
            edict_id="edict-123", run_id="run-456", model_allowlist=None, budget_cny=None, ttl_seconds=999
        )
        r = _post(c, tok)
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert r.headers["X-Tianshu-Edict-Id"] == "edict-123"
        assert r.headers["X-Tianshu-Run-Id"] == "run-456"

    def test_spend_recorded_after_forward(self, client):
        c, store = client
        tok = store.mint(edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=999)
        _post(c, tok)
        rec = store.verify(tok)
        assert rec.spent_cny == pytest.approx(0.5 * 7.2)  # 0.5 USD ×7.2

    def test_openai_style_endpoint_also_enforced(self, client):
        c, store = client
        tok = store.mint(edict_id="e", run_id="r", model_allowlist={"m"}, budget_cny=None, ttl_seconds=999)
        r = c.post(
            "/keqing/llm/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {tok}"},
            content=json.dumps({"model": "not-m"}),
        )
        assert r.status_code == 403


class TestXApiKeyHeader:
    def test_accepts_x_api_key_as_bearer(self, client):
        c, store = client
        tok = store.mint(edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=999)
        r = c.post(
            "/keqing/llm/anthropic/v1/messages",
            headers={"x-api-key": tok},
            content=json.dumps({"model": "m"}),
        )
        assert r.status_code == 200
