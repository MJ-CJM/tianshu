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
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._max_retries = max_retries
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens

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
        # When api_base is set and model has no provider prefix,
        # prepend "openai/" so LiteLLM routes via OpenAI-compatible path.
        model = self._model
        if self._api_base and "/" not in model:
            model = f"openai/{model}"

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if tools:
            kwargs["tools"] = tools

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

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            reasoning_content=getattr(message, "reasoning_content", None),
        )
