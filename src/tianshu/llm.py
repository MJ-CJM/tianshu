"""LLM Client - wraps LiteLLM for unified chat and function calling."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import litellm
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tianshu.cost.tracker import estimate_cost
from tianshu.models import UsageSummary
from tianshu.observability import genai_span, record_usage

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict] | None
    usage: UsageSummary
    reasoning_content: str | None = None
    finish_reason: str | None = None


_PROVIDER_HINTS: dict[str, str] = {
    "deepseek": "deepseek",
    "minimaxi": "openai",
    "minimax": "openai",
}

_ANTHROPIC_PREFIXES = ("claude", "anthropic/")


def _is_anthropic_model(model: str) -> bool:
    """Check if model is an Anthropic Claude model."""
    lower = model.lower()
    return any(lower.startswith(p) for p in _ANTHROPIC_PREFIXES)


def _apply_prompt_caching(messages: list[dict], model: str) -> list[dict]:
    """Add cache_control breakpoints for Anthropic models.

    Strategy: mark system messages and the last 3 non-system messages with
    cache_control: {"type": "ephemeral"}. This enables Anthropic's prompt
    caching (~75% input token savings on cached portions).

    For non-Anthropic models, returns messages unchanged.
    """
    if not _is_anthropic_model(model):
        return messages

    result = [dict(m) for m in messages]
    cache_marker = {"type": "ephemeral"}

    # Mark system messages
    for msg in result:
        if msg.get("role") == "system":
            msg["content"] = _add_cache_control(msg.get("content", ""), cache_marker)

    # Mark last 3 non-system messages
    non_system = [i for i, m in enumerate(result) if m.get("role") != "system"]
    for idx in non_system[-3:]:
        msg = result[idx]
        if msg.get("role") in ("user", "assistant"):
            msg["content"] = _add_cache_control(msg.get("content", ""), cache_marker)

    return result


def _add_cache_control(content: str | list | dict, marker: dict) -> list[dict]:
    """Convert content to block format and add cache_control to the last block."""
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = [
            dict(b) if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content
        ]
    elif isinstance(content, dict):
        blocks = [dict(content)]
    else:
        blocks = [{"type": "text", "text": str(content)}]

    if blocks:
        blocks[-1]["cache_control"] = marker
    return blocks


def _extract_model_echo(response: object) -> tuple[str | None, str | None]:
    """从 litellm response 中提取上游真实回显的模型名与 provider。

    用于验证「我们请求的模型」与「网关/上游实际使用的模型」是否一致 ——
    新中转网关偶尔会做 model rewrite/降级，仅靠请求侧字段无法发现。

    返回 (actual_model, custom_llm_provider)，任一字段不可得时为 None。
    """
    actual_model = getattr(response, "model", None)
    hidden = getattr(response, "_hidden_params", None) or {}
    provider = None
    if isinstance(hidden, dict):
        provider = hidden.get("custom_llm_provider") or hidden.get("model_id")
    # 边界防御：回显字段须为 str|None（异常类型 → None），避免污染 UsageSummary
    # 的 str|None 校验（如测试 mock 或畸形上游响应）。
    if not isinstance(actual_model, str):
        actual_model = None
    if not isinstance(provider, str):
        provider = None
    return actual_model, provider


# 进程级缓存：(api_base, requested_model) → (actual_model, provider)。
# 用于日志降噪 —— 同一路由首次或回显发生变化时打 INFO，否则降到 DEBUG。
_MODEL_ECHO_CACHE: dict[tuple[str, str], tuple[str | None, str | None]] = {}


def _model_base(name: str | None) -> str:
    """剥掉 provider 前缀后的裸模型名，用于 mismatch 比较。"""
    if not name:
        return ""
    return name.split("/", 1)[-1].lower()


def _log_model_echo(
    requested: str,
    actual: str | None,
    provider: str | None,
    api_base: str,
    streaming: bool = False,
) -> None:
    """按路由变化分级输出模型回显日志。

    分级规则：
    - 首次见到 (api_base, requested) 路由 → INFO（用户能确认配置生效）
    - actual base 与 requested base 不同 → WARNING（网关静默改写或降级，需告警）
    - 路由 + 回显都没变 → DEBUG（绝大多数情况，避免日志风暴）
    - 路由相同但回显变了（罕见，例如上游切了通道）→ INFO 重新打一次
    """
    key = (api_base or "<default>", requested)
    cached = _MODEL_ECHO_CACHE.get(key)
    new_value = (actual, provider)
    suffix = " (stream)" if streaming else ""
    msg_args = (requested, actual, provider, api_base or "<default>")

    requested_base = _model_base(requested)
    actual_base = _model_base(actual)
    mismatch = bool(actual_base) and bool(requested_base) and actual_base != requested_base

    if mismatch:
        logger.warning(
            "[LLM] model mismatch%s: requested=%s actual=%s provider=%s api_base=%s "
            "（上游可能改写或降级了模型，请核查网关配置）",
            suffix,
            *msg_args,
        )
    elif cached is None:
        logger.info(
            "[LLM] model echo%s: requested=%s actual=%s provider=%s api_base=%s",
            suffix,
            *msg_args,
        )
    elif cached != new_value:
        logger.info(
            "[LLM] model echo changed%s: requested=%s actual=%s provider=%s api_base=%s "
            "(was actual=%s provider=%s)",
            suffix,
            *msg_args,
            cached[0],
            cached[1],
        )
    else:
        logger.debug(
            "[LLM] model echo%s: requested=%s actual=%s provider=%s api_base=%s",
            suffix,
            *msg_args,
        )

    _MODEL_ECHO_CACHE[key] = new_value


def _extract_cache_read_tokens(usage_obj: object, model: str) -> int:
    """从不同 provider 的 usage 对象提取 cache hit token 数。

    多 provider 字段差异（litellm 没统一）：
    - claude/anthropic: usage.cache_read_input_tokens
    - deepseek:         usage.prompt_cache_hit_tokens
    - openai/兼容:      usage.prompt_tokens_details.cached_tokens
    - 其它:             0（保守）

    用 getattr + dict.get 双路径访问（litellm Usage 对象有时是 pydantic、有时是 dict）。
    """
    if usage_obj is None:
        return 0
    model_lower = (model or "").lower()
    base = model_lower.split("/")[-1] if "/" in model_lower else model_lower

    def _get(obj: object, key: str, default: int = 0) -> int:
        if obj is None:
            return default
        v = getattr(obj, key, None)
        if v is None and isinstance(obj, dict):
            v = obj.get(key)
        return int(v) if v else default

    # claude/anthropic 系列
    if "claude" in base or "anthropic" in base:
        return _get(usage_obj, "cache_read_input_tokens")
    # deepseek 系列
    if "deepseek" in base:
        return _get(usage_obj, "prompt_cache_hit_tokens")
    # openai/兼容（gpt、qwen-openai-compat 等）
    details = getattr(usage_obj, "prompt_tokens_details", None)
    if details is None and isinstance(usage_obj, dict):
        details = usage_obj.get("prompt_tokens_details")
    if details:
        return _get(details, "cached_tokens")
    return 0


class LLMClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: str = "",
        max_retries: int = 3,
        temperature: float = 0.7,
        top_p: float = 1.0,
        max_tokens: int = 4096,
        timeout: int = 300,
        provider_name: str | None = None,
        pricing_override: tuple[float, float, float] | None = None,
        router: object | None = None,
        router_model_name: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._max_retries = max_retries
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._provider_name = provider_name
        self._pricing_override = pricing_override
        # litellm.Router 注入(可选):有 router + router_model_name 时,chat/chat_stream
        # 走 Router(fallback/按类型重试/冷却见 llm_router.py),凭证由 deployment 自带;
        # 否则保持直连 acompletion(沙箱评估凭证隔离、doctor 等场景依赖此路径)。
        self._router = router
        self._router_model_name = router_model_name

    @property
    def provider_name(self) -> str | None:
        return self._provider_name

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        if self._model == "mock/tianshu-demo":
            # 精确 opt-in 的确定性零网络 demo；在任何 LiteLLM/Router 调用之前分派。
            # live 模型的错误在下方原路径中保持为错误，永不落入 demo。
            from tianshu.providers.demo import demo_chat

            return await demo_chat(messages, tools)
        if self._router is not None and self._router_model_name:
            # Router 路径:重试/降级/冷却由 Router 全权负责(llm_router.py 配置),
            # 外层不再叠加 tenacity,避免重试次数相乘。
            return await self._chat_once(messages, tools)
        async for attempt in AsyncRetrying(
            wait=wait_exponential(min=1, max=4),
            stop=stop_after_attempt(3),
            retry=retry_if_exception_type(
                (litellm.RateLimitError, litellm.Timeout, litellm.ServiceUnavailableError)
            ),
            reraise=True,
        ):
            with attempt:
                return await self._chat_once(messages, tools)
        raise AssertionError("unreachable: AsyncRetrying(reraise=True)")

    async def _chat_once(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        model, api_base = self._resolve_model()
        messages = _apply_prompt_caching(messages, model)

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
            "timeout": self._timeout,
            "drop_params": True,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if api_base:
            kwargs["api_base"] = api_base
        if tools:
            kwargs["tools"] = tools

        logger.debug(
            "[LLM] request: model=%s, messages=%d, tools=%d, temperature=%.1f",
            model,
            len(messages),
            len(tools or []),
            self._temperature,
        )
        # OTel GenAI inference span(迭代 3):未启用时零成本 no-op
        with genai_span("chat", model=model) as _span:
            if self._router is not None and self._router_model_name:
                r_kwargs = dict(kwargs)
                r_kwargs.pop("api_key", None)  # 凭证由 Router deployment 自带
                r_kwargs.pop("api_base", None)
                r_kwargs["model"] = self._router_model_name
                response = await self._router.acompletion(**r_kwargs)  # type: ignore[attr-defined]
            else:
                response = await litellm.acompletion(**kwargs)
            actual_model, upstream_provider = _extract_model_echo(response)
            _log_model_echo(model, actual_model, upstream_provider, api_base)
            choice = response.choices[0]
            message = choice.message

            usage = UsageSummary(
                actual_model=actual_model,
                upstream_provider=upstream_provider,
            )
            if response.usage:
                pt = response.usage.prompt_tokens or 0
                ct = response.usage.completion_tokens or 0
                cr = _extract_cache_read_tokens(response.usage, model)
                usage = UsageSummary(
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    total_tokens=response.usage.total_tokens or 0,
                    cache_read_tokens=cr,
                    cost_cny=estimate_cost(
                        model,
                        pt,
                        ct,
                        cache_read_tokens=cr,
                        provider_pricing=self._pricing_override,
                    ),
                    actual_model=actual_model,
                    upstream_provider=upstream_provider,
                )
                record_usage(_span, input_tokens=pt, output_tokens=ct)

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": tc.function.arguments,
                }
                for tc in message.tool_calls
            ]

        logger.debug(
            "[LLM] response: model=%s, tokens=%d/%d/%d, tool_calls=%d, has_reasoning=%s",
            model,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
            len(tool_calls or []),
            bool(getattr(message, "reasoning_content", None)),
        )
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            reasoning_content=getattr(message, "reasoning_content", None),
            finish_reason=getattr(choice, "finish_reason", None),
        )

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ):
        """Streaming chat — yields LLMResponse chunks. Final chunk has full usage."""
        if self._model == "mock/tianshu-demo":
            from tianshu.providers.demo import demo_chat_stream

            async for chunk in demo_chat_stream(messages, tools):
                yield chunk
            return
        model, api_base = self._resolve_model()
        messages = _apply_prompt_caching(messages, model)

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
            "timeout": self._timeout,
            "drop_params": True,
            "stream": True,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if api_base:
            kwargs["api_base"] = api_base
        if tools:
            kwargs["tools"] = tools

        if self._router is not None and self._router_model_name:
            r_kwargs = dict(kwargs)
            r_kwargs.pop("api_key", None)  # 凭证由 Router deployment 自带
            r_kwargs.pop("api_base", None)
            r_kwargs["model"] = self._router_model_name
            response = await self._router.acompletion(**r_kwargs)  # type: ignore[attr-defined]
        else:
            response = await litellm.acompletion(**kwargs)

        collected_content = ""
        collected_tool_calls: list[dict] = []
        finish_reason = None
        usage = UsageSummary()
        echoed_actual: str | None = None
        echoed_provider: str | None = None
        echoed = False

        async for chunk in response:
            if not echoed:
                actual_model, upstream_provider = _extract_model_echo(chunk)
                if actual_model or upstream_provider:
                    _log_model_echo(
                        model, actual_model, upstream_provider, api_base, streaming=True
                    )
                    echoed_actual = actual_model
                    echoed_provider = upstream_provider
                    echoed = True
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            finish_reason = chunk.choices[0].finish_reason or finish_reason

            if delta.content:
                collected_content += delta.content
                yield LLMResponse(
                    content=delta.content,
                    tool_calls=None,
                    usage=UsageSummary(),
                    finish_reason=None,
                )

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    while len(collected_tool_calls) <= idx:
                        collected_tool_calls.append({"id": "", "name": "", "args": ""})
                    tc = collected_tool_calls[idx]
                    if tc_delta.id:
                        tc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc["args"] += tc_delta.function.arguments

            if chunk.usage:
                pt = chunk.usage.prompt_tokens or 0
                ct = chunk.usage.completion_tokens or 0
                cr = _extract_cache_read_tokens(chunk.usage, model)
                usage = UsageSummary(
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    total_tokens=chunk.usage.total_tokens or 0,
                    cache_read_tokens=cr,
                    cost_cny=estimate_cost(
                        model,
                        pt,
                        ct,
                        cache_read_tokens=cr,
                        provider_pricing=self._pricing_override,
                    ),
                    actual_model=echoed_actual,
                    upstream_provider=echoed_provider,
                )

        final_tool_calls = None
        if collected_tool_calls:
            final_tool_calls = [
                {"id": tc["id"], "name": tc["name"], "args": tc["args"]}
                for tc in collected_tool_calls
                if tc["name"]
            ]

        yield LLMResponse(
            content=collected_content or None,
            tool_calls=final_tool_calls or None,
            usage=usage,
            finish_reason=finish_reason,
        )

    def _resolve_model(self) -> tuple[str, str]:
        """Resolve model name with provider prefix and clean api_base."""
        model = self._model
        api_base = self._api_base.strip() if self._api_base else ""
        if "/" not in model and api_base:
            base_lower = api_base.lower()
            prefix = "openai"
            for hint, provider in _PROVIDER_HINTS.items():
                if hint in base_lower:
                    prefix = provider
                    break
            model = f"{prefix}/{model}"
        return model, api_base
