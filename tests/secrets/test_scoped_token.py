"""scoped_token 安全核心测试:铸造/校验/TTL/吊销/模型白名单/预算 402 判定。"""

from tianshu.secrets.scoped_token import (
    ScopedTokenStore,
    _hash_token,
    get_scoped_token_store,
)


def _store() -> ScopedTokenStore:
    return ScopedTokenStore()


class TestMintAndVerify:
    def test_mint_returns_opaque_prefixed_token(self):
        s = _store()
        tok = s.mint(
            edict_id="e1", run_id="r1", model_allowlist={"m"}, budget_cny=10, ttl_seconds=60
        )
        assert tok.startswith("tskq_")
        rec = s.verify(tok, now=0)
        assert rec is not None and rec.edict_id == "e1" and rec.run_id == "r1"

    def test_only_hash_stored_not_plaintext(self):
        s = _store()
        tok = s.mint(
            edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=60
        )
        # 台账里存的是哈希,不是明文
        assert _hash_token(tok) in s._by_hash  # noqa: SLF001
        assert tok not in s._by_hash  # noqa: SLF001

    def test_unknown_token_rejected(self):
        assert _store().verify("tskq_nope", now=0) is None

    def test_two_mints_differ(self):
        s = _store()
        t1 = s.mint(
            edict_id="e", run_id="r1", model_allowlist=None, budget_cny=None, ttl_seconds=60
        )
        t2 = s.mint(
            edict_id="e", run_id="r2", model_allowlist=None, budget_cny=None, ttl_seconds=60
        )
        assert t1 != t2


class TestTTL:
    def test_expired_token_rejected(self):
        s = _store()
        tok = s.mint(
            edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=100, now=0
        )
        assert s.verify(tok, now=50) is not None
        assert s.verify(tok, now=100) is None  # 到点即失效
        assert s.verify(tok, now=101) is None


class TestRevoke:
    def test_revoke_run_invalidates_token(self):
        s = _store()
        tok = s.mint(
            edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=999, now=0
        )
        assert s.verify(tok, now=1) is not None
        assert s.revoke_run("r", now=2) is True
        assert s.verify(tok, now=3) is None  # 吊销后立即失效

    def test_revoke_unknown_run_is_noop(self):
        assert _store().revoke_run("ghost") is False

    def test_remint_same_run_supersedes_old(self):
        s = _store()
        t1 = s.mint(
            edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=999, now=0
        )
        t2 = s.mint(
            edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=999, now=1
        )
        assert s.verify(t1, now=2) is None  # 旧 token 被顶掉
        assert s.verify(t2, now=2) is not None


class TestModelAllowlist:
    def test_allowlist_enforced(self):
        s = _store()
        tok = s.mint(
            edict_id="e",
            run_id="r",
            model_allowlist={"anthropic/opus"},
            budget_cny=None,
            ttl_seconds=60,
        )
        rec = s.verify(tok, now=0)
        assert rec.allows_model("anthropic/opus") is True
        assert rec.allows_model("openai/gpt-9") is False

    def test_empty_allowlist_permits_all(self):
        s = _store()
        tok = s.mint(
            edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=60
        )
        assert s.verify(tok, now=0).allows_model("anything") is True


class TestBudget402:
    def test_spend_accumulates_in_cny_and_trips_over_budget(self):
        s = _store()
        # 预算 7.2 CNY = 1.0 USD
        tok = s.mint(edict_id="e", run_id="r", model_allowlist=None, budget_cny=7.2, ttl_seconds=60)
        rec = s.record_spend(tok, 0.5)  # 0.5 USD = 3.6 CNY
        assert rec.is_over_budget() is False
        rec = s.record_spend(tok, 0.5)  # 累计 1.0 USD = 7.2 CNY ≥ 预算
        assert rec.is_over_budget() is True  # → 网关对后续请求返回 402

    def test_unlimited_budget_never_over(self):
        s = _store()
        tok = s.mint(
            edict_id="e", run_id="r", model_allowlist=None, budget_cny=None, ttl_seconds=60
        )
        rec = s.record_spend(tok, 1000.0)
        assert rec.is_over_budget() is False

    def test_record_spend_unknown_token_returns_none(self):
        assert _store().record_spend("tskq_x", 1.0) is None


class TestGC:
    def test_gc_reclaims_expired_and_revoked(self):
        s = _store()
        s.mint(
            edict_id="e", run_id="r1", model_allowlist=None, budget_cny=None, ttl_seconds=10, now=0
        )
        s.mint(
            edict_id="e", run_id="r2", model_allowlist=None, budget_cny=None, ttl_seconds=999, now=0
        )
        s.revoke_run("r2", now=1)
        reclaimed = s.gc_expired(now=100)  # r1 过期 + r2 已吊销
        assert reclaimed == 2
        assert len(s._by_hash) == 0  # noqa: SLF001


class TestSingleton:
    def test_get_store_is_singleton(self):
        assert get_scoped_token_store() is get_scoped_token_store()
