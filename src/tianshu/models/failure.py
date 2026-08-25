"""失败原因分类学 —— memorial.failure_reason 的唯一真相源(迭代 2「证明」)。

借鉴 multica server/pkg/taskfailure(MUL-1949 离线回填 SQL 提炼的 14 种
agent_error.* 子原因),叠加天枢自有的平台侧原因。分类器在落库写路径上
(storage.mappers / update_memorial),存储的 failure_reason 首次持久化时
即已细化;历史行由 migrations 用同一分类函数回填,保证在库口径统一。

两组取值:

- 平台侧(无前缀,3 类):失败可归因于天枢平台层(预算闸/迭代闸/孤儿
  回收)而非 agent 过程本身。天枢的批红拒绝/审计驳回是独立终态
  (rejected),不属于失败,不进本分类。
- agent 侧(``agent_error.`` 前缀,14 类):agent 执行过程浮出的错误
  (provider 4xx/5xx、上下文超窗、子进程崩溃等),由 classify_failure()
  从自由文本 error 判定。

线上稳定性:字符串值持久化进 memorials.failure_reason 并出现在统计面板,
改名即破坏性变更。新增值须同步 classify_failure 规则 + migrations 回填。
"""

from __future__ import annotations

import re
from enum import StrEnum

# 5xx 状态码锚定匹配:避免 "1500ms"/"1.5.0" 误入 provider_server_error
# (multica classify.go 同款正则,模块加载时编译一次)
_HTTP_5XX_RE = re.compile(r"(^|[^0-9])5[0-9][0-9]([^0-9]|$)")


class FailureReason(StrEnum):
    """memorials.failure_reason 的规范取值(17 个)。"""

    # --- 平台侧:天枢自身的闸/回收器写入的失败 ---
    BUDGET_EXCEEDED = "budget_exceeded"
    ITERATION_LIMIT = "iteration_limit"
    ORPHAN_RECOVERED = "orphan_recovered"

    # --- agent 侧:provider 错误 ---
    PROVIDER_AUTH_OR_ACCESS = "agent_error.provider_auth_or_access"
    PROVIDER_QUOTA_LIMIT = "agent_error.provider_quota_limit"
    PROVIDER_CAPACITY_OR_RATE_LIMIT = "agent_error.provider_capacity_or_rate_limit"
    PROVIDER_SERVER_ERROR = "agent_error.provider_server_error"
    PROVIDER_NETWORK = "agent_error.provider_network"

    # --- agent 侧:agent / 运行器错误 ---
    PROCESS_FAILURE = "agent_error.process_failure"
    EMPTY_OR_UNPARSEABLE_OUTPUT = "agent_error.empty_or_unparseable_output"
    AGENT_TIMEOUT = "agent_error.agent_timeout"
    CONTEXT_OVERFLOW = "agent_error.context_overflow"
    MISSING_CONFIG = "agent_error.missing_config"
    MODEL_NOT_FOUND_OR_UNAVAILABLE = "agent_error.model_not_found_or_unavailable"
    RUNTIME_VERSION_UNSUPPORTED = "agent_error.runtime_version_unsupported"
    RUNTIME_MISSING_EXECUTABLE = "agent_error.runtime_missing_executable"

    # --- 兜底:此桶占比应 <5%,升高即分类器需要新规则 ---
    UNKNOWN = "agent_error.unknown"

    @property
    def is_agent_error(self) -> bool:
        return self.value.startswith("agent_error.")

    @property
    def is_retryable(self) -> bool:
        """Whether an execution failure may consume another attempt."""

        return self in _RETRYABLE_FAILURE_REASONS


_RETRYABLE_FAILURE_REASONS = frozenset(
    {
        FailureReason.PROVIDER_CAPACITY_OR_RATE_LIMIT,
        FailureReason.PROVIDER_SERVER_ERROR,
        FailureReason.PROVIDER_NETWORK,
        FailureReason.PROCESS_FAILURE,
        FailureReason.AGENT_TIMEOUT,
    }
)


def classify_exception_failure(exc: Exception) -> FailureReason:
    """Map execution exception types to canonical failure reasons, failing closed."""

    # TimeoutError and ConnectionError are OSError subclasses, so specific
    # transport meanings must be checked before the generic process bucket.
    if isinstance(exc, TimeoutError):
        return FailureReason.AGENT_TIMEOUT
    if isinstance(exc, ConnectionError):
        return FailureReason.PROVIDER_NETWORK
    if isinstance(exc, OSError):
        return FailureReason.PROCESS_FAILURE
    return FailureReason.UNKNOWN


def _contains_any(s: str, *subs: str) -> bool:
    return any(sub in s for sub in subs)


def classify_failure(raw_error: str | None) -> FailureReason:
    """自由文本 error → 规范 FailureReason;空输入与无规则命中落 UNKNOWN。

    规则序移植 multica classify.go(其 SQL CASE 为真相源):平台侧文案
    特异性最高置前;agent 侧 more-specific 在前(context_overflow 先于
    quota,否则 "token limit" 被 "limit" 抢走)。大小写不敏感子串匹配。
    """
    lower = (raw_error or "").strip().lower()
    if not lower:
        return FailureReason.UNKNOWN

    # --- 平台侧:天枢自有文案,特异性最高 ---
    if "budget_exhausted" in lower:
        return FailureReason.BUDGET_EXCEEDED
    if "max iterations" in lower or "iteration limit" in lower:
        return FailureReason.ITERATION_LIMIT
    if "orphaned" in lower and "heartbeat" in lower:
        return FailureReason.ORPHAN_RECOVERED

    # --- agent 侧(multica 规则序) ---
    # 1. 上下文/token 超窗:先于 quota,防 "token limit" 被 "limit" 兜走
    if _contains_any(
        lower,
        "context length",
        "context_length_exceeded",
        "maximum context",
        "prompt is too long",
        "context size has been exceeded",
        "context window",
    ) or ("token" in lower and "limit" in lower):
        return FailureReason.CONTEXT_OVERFLOW
    # 2. 缺配置:先于 auth,"missing api key" 是配置错不是认证被拒
    if (
        "missing environment variable" in lower
        or ("missing" in lower and "api_key" in lower)
        or ("api key" in lower and "required" in lower)
        or "no llm provider configured" in lower
        or "no provider configured" in lower
    ):
        return FailureReason.MISSING_CONFIG
    # 3. 认证/访问
    if _contains_any(
        lower,
        "401",
        "403",
        "unauthorized",
        "login required",
        "not logged in",
        "please login again",
        "invalid api key",
        "authenticationerror",
        "does not have access",
        "you may not have access",
    ):
        return FailureReason.PROVIDER_AUTH_OR_ACCESS
    # 4. 配额/余额
    if _contains_any(
        lower,
        "402",
        "insufficient_balance",
        "balance is too low",
        "monthly usage limit",
        "usage limit",
        "you've hit your limit",
        "you’ve hit your limit",
        "credits",
        "quota",
    ):
        return FailureReason.PROVIDER_QUOTA_LIMIT
    # 5. 限流/容量
    if _contains_any(lower, "429", "rate limit", "ratelimit", "overloaded", "529"):
        return FailureReason.PROVIDER_CAPACITY_OR_RATE_LIMIT
    # 6. provider 5xx
    if _contains_any(
        lower,
        "server had an error",
        "provider returned error",
        "internal error",
        "internal server error",
        "service unavailable",
        "bad gateway",
    ) or _HTTP_5XX_RE.search(lower):
        return FailureReason.PROVIDER_SERVER_ERROR
    # 7. 网络层
    if _contains_any(
        lower,
        "stream disconnected",
        "error sending request",
        "unable to connect",
        "dial tcp",
        "connection refused",
        "connection error",
        "apiconnectionerror",
        "dns",
        "i/o timeout",
    ):
        return FailureReason.PROVIDER_NETWORK
    # 8. 模型不存在/不可用
    if ("model" in lower and "not found" in lower) or _contains_any(
        lower, "unknown model", "http 404", "404 page not found"
    ):
        return FailureReason.MODEL_NOT_FOUND_OR_UNAVAILABLE
    # 9. 空/不可解析输出
    if _contains_any(
        lower, "returned empty output", "returned no parseable output", "empty response"
    ):
        return FailureReason.EMPTY_OR_UNPARSEABLE_OUTPUT
    # 10. agent 执行超时(LLM 请求/子进程都算 agent 侧墙钟超时)
    if _contains_any(lower, "timed out", "timeout"):
        return FailureReason.AGENT_TIMEOUT
    # 11. 运行器可执行文件缺失(客卿执行器 3.5 起产)
    if "executable not found" in lower or "command not found" in lower:
        return FailureReason.RUNTIME_MISSING_EXECUTABLE
    # 12. 运行器版本不兼容
    if _contains_any(lower, "below the minimum supported version", "requires a newer version"):
        return FailureReason.RUNTIME_VERSION_UNSUPPORTED
    # 13. 进程级失败:最后判,崩溃常由更具体的上游错误引起、应让位
    if _contains_any(
        lower,
        "exit status",
        "sigsegv",
        "panic",
        "process exited",
        "pipe has been ended",
        "file already closed",
        "traceback (most recent call last)",
    ):
        return FailureReason.PROCESS_FAILURE

    return FailureReason.UNKNOWN


def resolve_failure_reason(status: str, error: str | None, explicit: str | None) -> str | None:
    """落库写路径的归因决策:显式值优先;仅 failed 终态参与自动分类。

    save_memorial / update_memorial / 回填三处共用,保证在库口径统一。
    """
    if explicit:
        return explicit
    if status != "failed":
        return None
    return classify_failure(error).value
