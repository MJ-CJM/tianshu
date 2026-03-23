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
        # Infer litellm provider prefix from api_base when model has none,
        # so litellm applies the correct parameter adaptation per provider.
        _PROVIDER_HINTS: dict[str, str] = {
            "deepseek": "deepseek",
            "minimaxi": "openai",
            "minimax": "openai",
        }

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
        )
