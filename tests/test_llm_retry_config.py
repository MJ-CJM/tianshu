"""LLM retry configuration must affect the actual call loop."""

from __future__ import annotations

from unittest.mock import AsyncMock

import litellm
import pytest
from tenacity import wait_none

from tianshu.llm import LLMClient


@pytest.mark.asyncio
async def test_max_retries_is_honored(monkeypatch) -> None:
    monkeypatch.setattr("tianshu.llm.wait_exponential", lambda **_: wait_none())
    client = LLMClient(model="test-model", api_key="", max_retries=2)
    failure = litellm.Timeout("timeout", "test-model", "test")
    call = AsyncMock(side_effect=failure)
    monkeypatch.setattr(client, "_chat_once", call)

    with pytest.raises(litellm.Timeout):
        await client.chat([{"role": "user", "content": "hello"}])

    assert call.await_count == 3


@pytest.mark.asyncio
async def test_zero_retries_still_makes_one_attempt(monkeypatch) -> None:
    monkeypatch.setattr("tianshu.llm.wait_exponential", lambda **_: wait_none())
    client = LLMClient(model="test-model", api_key="", max_retries=0)
    failure = litellm.Timeout("timeout", "test-model", "test")
    call = AsyncMock(side_effect=failure)
    monkeypatch.setattr(client, "_chat_once", call)

    with pytest.raises(litellm.Timeout):
        await client.chat([{"role": "user", "content": "hello"}])

    assert call.await_count == 1
