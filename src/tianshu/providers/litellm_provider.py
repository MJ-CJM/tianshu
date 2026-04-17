"""LiteLLM-based provider factory."""

from __future__ import annotations

import logging

from tianshu.llm import LLMClient

logger = logging.getLogger(__name__)


def create_llm_client(
    model: str,
    api_key: str = "",
    api_base: str = "",
    **kwargs,
) -> LLMClient:
    """Create an LLMClient for a specific provider configuration."""
    return LLMClient(
        model=model,
        api_key=api_key,
        api_base=api_base,
        **kwargs,
    )
