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
    def __init__(self, model: str, api_key: str, max_retries: int = 3) -> None:
        self._model = model
        self._api_key = api_key
        self._max_retries = max_retries

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
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
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
