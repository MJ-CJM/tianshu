"""scoped token 的 run-token contextvar + resolver 接线测试:
会话执行器 spawn 前 set 的明文,能被同上下文的 secret_resolver 读到并注入客卿 env;
raw provider key 永不经此。"""

from tianshu.secrets.scoped_token import (
    get_current_run_token,
    reset_current_run_token,
    set_current_run_token,
)


class TestContextVar:
    def test_set_get_reset(self):
        assert get_current_run_token() is None
        handle = set_current_run_token("tskq_abc")
        assert get_current_run_token() == "tskq_abc"
        reset_current_run_token(handle)
        assert get_current_run_token() is None

    def test_resolver_returns_run_token_for_gateway_ref(self):
        from tianshu.bootstrap.wiring_tools import _runtime_secret_resolver
        from tianshu.config import TianshuSettings

        resolve = _runtime_secret_resolver(TianshuSettings())
        handle = set_current_run_token("tskq_run42")
        try:
            assert resolve("keqing-run:gateway-token") == "tskq_run42"
            # 其他 keqing-run: ref 不泄漏(返回 None)
            assert resolve("keqing-run:something-else") is None
        finally:
            reset_current_run_token(handle)

    def test_resolver_gateway_ref_none_when_unset(self):
        from tianshu.bootstrap.wiring_tools import _runtime_secret_resolver
        from tianshu.config import TianshuSettings

        resolve = _runtime_secret_resolver(TianshuSettings())
        # 无 run token 上下文时返回 None(不落 raw key)
        assert resolve("keqing-run:gateway-token") is None
