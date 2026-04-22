"""Jina Search engine：s.jina.ai/?q=<query>。Spec Section 5.3。"""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

import httpx

from tianshu.tools.hongluisi.engines import SearchOutcome, SearchResult
from tianshu.tools.hongluisi.http_client import SharedHttpClient

logger = logging.getLogger(__name__)

JINA_SEARCH_BASE = "https://s.jina.ai"


class JinaSearchEngine:
    name = "jina"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        client = SharedHttpClient.instance()
        url = f"{JINA_SEARCH_BASE}/?q={quote_plus(query)}"
        headers = {"Accept": "text/plain"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            resp = await client._client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise RuntimeError(f"jina_search_http_error:{type(e).__name__}") from e
        if resp.status_code >= 400:
            raise RuntimeError(f"jina_search_status:{resp.status_code}")

        # Jina Search 返回 markdown 文本，每条结果大致是 "[n] Title\nURL\nSnippet" 段
        # 为统一协议，我们把 raw 放在 snippet 里，url / title 留空让上层文本化
        # 简单起见：整段作为一个 SearchResult（title=query，snippet=body）
        body = resp.text.strip()
        results = (
            SearchResult(
                title=f"Jina Search: {query}",
                url=url,
                snippet=body[:4000],  # 防爆
                score=None,
            ),
        )
        return SearchOutcome(
            results=results[:max_results] if max_results < len(results) else results,
            raw_api_meta={"bytes": len(body)},
        )


def build_jina_search(store=None) -> JinaSearchEngine | None:
    """DB-first / env fallback；key 可选，无 key 也能用（unauthed 模式）。"""
    from tianshu.secrets import resolve_provider_key

    key, _source = resolve_provider_key(store, "jina", "TIANSHU_JINA_API_KEY")
    return JinaSearchEngine(api_key=key)
