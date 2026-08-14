"""引擎异常的归因：缺依赖必须把「装什么」带到用户面前（issue #68）。

真实踩到：装好 scrapling 后抓取仍次次失败，事件流只显示
`engine_exception:ImportError`，看上去像两个免费引擎都坏了；实际是
trafilatura 缺席，两者都倒在 HTML→Markdown 那一步。markdown_extract 本来写了
「请执行: pip install 'tianshu-agent-os[web]'」，却被压成了异常类名。
"""

from __future__ import annotations

import pytest

from tianshu.tools.hongluisi.failure_reason import describe_engine_exception


class TestDescribeEngineException:
    def test_missing_dependency_keeps_the_actionable_hint(self):
        exc = ImportError("本地网页正文提取依赖未安装，请执行: pip install 'tianshu-agent-os[web]'")

        reason = describe_engine_exception(exc)

        assert reason.startswith("missing_dependency:")
        # 用户要能直接照着做，而不是去 grep 源码
        assert "pip install 'tianshu-agent-os[web]'" in reason

    def test_module_not_found_is_also_a_missing_dependency(self):
        reason = describe_engine_exception(ModuleNotFoundError("No module named 'trafilatura'"))

        assert reason.startswith("missing_dependency:")
        assert "trafilatura" in reason

    def test_other_exceptions_keep_type_and_message(self):
        reason = describe_engine_exception(TimeoutError("read timed out after 30s"))

        assert reason.startswith("engine_exception:TimeoutError")
        assert "read timed out" in reason

    def test_message_is_truncated_to_stay_loggable(self):
        reason = describe_engine_exception(RuntimeError("x" * 500))

        assert len(reason) < 300

    def test_message_less_exception_still_names_its_type(self):
        reason = describe_engine_exception(RuntimeError())

        assert reason == "engine_exception:RuntimeError"

    @pytest.mark.parametrize("exc", [ImportError(""), ModuleNotFoundError("")])
    def test_bare_import_error_still_flags_missing_dependency(self, exc):
        """即使上游没给 message，也要归成缺依赖而不是泛泛的异常。"""
        assert describe_engine_exception(exc).startswith("missing_dependency")


class TestRouterSurfacesTheHint:
    """整条链路视角：router 是把归因写进事件流的那一环。"""

    async def test_router_keeps_missing_dependency_hint_in_attempts(self):
        from tianshu.tools.hongluisi.policy import NetworkPolicy
        from tianshu.tools.hongluisi.router import FetchRouter

        class _MissingDep:
            name = "local"

            async def fetch(self, url):
                raise ImportError(
                    "本地网页正文提取依赖未安装，请执行: pip install 'tianshu-agent-os[web]'"
                )

        router = FetchRouter(
            {"local": _MissingDep()},
            NetworkPolicy(fetch_engines=("local",), fallback_mode="on_error_or_empty"),
            None,
        )

        outcome, attempts = await router.dispatch("https://example.com")

        # 用户在鸿胪寺「最近访问」里看到的就是这行 reason
        assert outcome.reason.startswith("missing_dependency:")
        assert "pip install 'tianshu-agent-os[web]'" in outcome.reason
        assert attempts[0].reason == outcome.reason
