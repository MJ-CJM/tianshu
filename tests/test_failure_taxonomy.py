"""失败分类学锚点(迭代 2「证明」)——规则序移植 multica classify.go 的核心用例。

改名任何 FailureReason 值或调换规则序都属破坏性变更,须先动这里。
"""

from __future__ import annotations

import pytest

from tianshu.models.failure import FailureReason, classify_failure, resolve_failure_reason


class TestPlatformSideRules:
    """天枢平台侧文案:特异性最高,先于 agent 侧判定。"""

    def test_budget_exhausted(self):
        raw = "budget_exhausted: cost (usage_ratio=1.02)"
        assert classify_failure(raw) is FailureReason.BUDGET_EXCEEDED

    def test_iteration_limit(self):
        assert classify_failure("Max iterations (25) reached") is FailureReason.ITERATION_LIMIT

    def test_orphan_recovered(self):
        raw = "orphaned: no heartbeat for 600s"
        assert classify_failure(raw) is FailureReason.ORPHAN_RECOVERED


class TestAgentSideRuleOrder:
    """multica 规则序锚点:more-specific 在前,防宽规则抢走窄场景。"""

    def test_token_limit_is_context_overflow_not_quota(self):
        # "token limit" 若先撞 quota 的 "limit" 规则即分类错误(classify.go 注释场景)
        assert classify_failure("input token limit exceeded") is FailureReason.CONTEXT_OVERFLOW

    def test_missing_api_key_is_config_not_auth(self):
        # "missing api_key" 是配置错不是认证被拒
        assert classify_failure("missing API_KEY in environment") is FailureReason.MISSING_CONFIG

    def test_auth_401(self):
        raw = "litellm.AuthenticationError: 401 invalid api key"
        assert classify_failure(raw) is FailureReason.PROVIDER_AUTH_OR_ACCESS

    def test_quota_402(self):
        assert classify_failure("402 insufficient_balance") is FailureReason.PROVIDER_QUOTA_LIMIT

    def test_rate_limit_429(self):
        assert (
            classify_failure("RateLimitError: 429 Too Many Requests")
            is FailureReason.PROVIDER_CAPACITY_OR_RATE_LIMIT
        )

    def test_5xx_anchored_regex_avoids_false_positive(self):
        # "1500ms" 不得误入 provider_server_error(锚定正则场景)
        assert classify_failure("request took 1500ms then hung") is not (
            FailureReason.PROVIDER_SERVER_ERROR
        )
        assert classify_failure("upstream returned 503") is FailureReason.PROVIDER_SERVER_ERROR

    def test_network(self):
        assert (
            classify_failure("APIConnectionError: connection refused")
            is FailureReason.PROVIDER_NETWORK
        )

    def test_model_not_found(self):
        assert (
            classify_failure("model gpt-99 not found")
            is FailureReason.MODEL_NOT_FOUND_OR_UNAVAILABLE
        )

    def test_agent_timeout(self):
        assert classify_failure("Execution timed out after 300s") is FailureReason.AGENT_TIMEOUT

    def test_process_failure_is_last_specific_rule(self):
        # 崩溃常由上游错误引起:带 429 的崩溃应归限流而非进程失败
        assert (
            classify_failure("process exited after 429 rate limit")
            is FailureReason.PROVIDER_CAPACITY_OR_RATE_LIMIT
        )
        assert classify_failure("process exited with signal") is FailureReason.PROCESS_FAILURE

    @pytest.mark.parametrize("raw", ["", None, "something entirely novel"])
    def test_unknown_catchall(self, raw):
        assert classify_failure(raw) is FailureReason.UNKNOWN


class TestResolveFailureReason:
    """落库写路径的归因决策语义。"""

    def test_explicit_wins(self):
        assert resolve_failure_reason("failed", "401 unauthorized", "custom.reason") == (
            "custom.reason"
        )

    def test_non_failed_status_is_none(self):
        for status in ("completed", "approved", "cancelled", "rejected", "running"):
            assert resolve_failure_reason(status, "401 unauthorized", None) is None

    def test_failed_classifies_error_text(self):
        assert (
            resolve_failure_reason("failed", "Execution timed out after 60s", None)
            == "agent_error.agent_timeout"
        )

    def test_failed_empty_error_falls_to_unknown(self):
        assert resolve_failure_reason("failed", None, None) == "agent_error.unknown"


class TestTaxonomyStability:
    """线上稳定性:17 个规范值的字符串形态持久化进 DB,改名即破坏。"""

    def test_canonical_value_count(self):
        assert len(FailureReason) == 17

    def test_agent_error_prefix_split(self):
        agent_side = [r for r in FailureReason if r.is_agent_error]
        platform_side = [r for r in FailureReason if not r.is_agent_error]
        assert len(agent_side) == 14  # multica 14 类照搬
        assert len(platform_side) == 3  # 天枢平台侧:预算/迭代闸/孤儿回收
