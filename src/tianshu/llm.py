"""LLM Client - wraps LiteLLM for unified chat and function calling."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import litellm
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tianshu.models import UsageSummary

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
        blocks = [dict(b) if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content]
    elif isinstance(content, dict):
        blocks = [dict(content)]
    else:
        blocks = [{"type": "text", "text": str(content)}]

    if blocks:
        blocks[-1]["cache_control"] = marker
    return blocks


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
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._max_retries = max_retries
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._timeout = timeout

    @retry(
        wait=wait_exponential(min=1, max=4),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(
            (litellm.RateLimitError, litellm.Timeout, litellm.ServiceUnavailableError)
        ),
        reraise=True,
    )
    async def chat(
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
            model, len(messages), len(tools or []), self._temperature,
        )
        response = await litellm.acompletion(**kwargs)
        choice = response.choices[0]
        message = choice.message

        usage = UsageSummary()
        if response.usage:
            usage = UsageSummary(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )

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
            model, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
            len(tool_calls or []), bool(getattr(message, "reasoning_content", None)),
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

        response = await litellm.acompletion(**kwargs)

        collected_content = ""
        collected_tool_calls: list[dict] = []
        finish_reason = None
        usage = UsageSummary()

        async for chunk in response:
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
                usage = UsageSummary(
                    prompt_tokens=chunk.usage.prompt_tokens or 0,
                    completion_tokens=chunk.usage.completion_tokens or 0,
                    total_tokens=chunk.usage.total_tokens or 0,
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
