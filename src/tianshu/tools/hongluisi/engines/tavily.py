"""Tavily search engine：api.tavily.com/search。Spec Section 5.3."""

from __future__ import annotations

import logging

import httpx

from tianshu.tools.hongluisi.engines import SearchOutcome, SearchResult
from tianshu.tools.hongluisi.http_client import SharedHttpClient

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilySearchEngine:
    name = "tavily"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("TavilySearchEngine requires api_key")
        self._api_key = api_key

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        client = SharedHttpClient.instance()
        body = {
            "api_key": self._api_key,  # Tavily 支持 body 里传 key
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_raw_content": False,
        }
        try:
            data, http_status = await client.post_json(TAVILY_ENDPOINT, body)
        except httpx.HTTPError as e:
            raise RuntimeError(f"tavily_http_error:{type(e).__name__}") from e

        if http_status >= 400:
            raise RuntimeError(f"tavily_status:{http_status}")

        results: list[SearchResult] = []
        for item in data.get("results") or []:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    score=item.get("score"),
                )
            )
        meta = {
            "response_time": data.get("response_time"),
            "usage": data.get("usage"),
        }
        return SearchOutcome(results=tuple(results), raw_api_meta=meta)


def build_tavily(store=None) -> TavilySearchEngine | None:
    """DB-first / env fallback；无 key 时返回 None，上层不注册该 provider。"""
    from tianshu.secrets import resolve_provider_key

    key, _source = resolve_provider_key(store, "tavily", "TIANSHU_TAVILY_API_KEY")
    if not key:
        return None
    return TavilySearchEngine(api_key=key)
