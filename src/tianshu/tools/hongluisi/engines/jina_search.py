"""Jina Search engine：s.jina.ai/?q=<query>。Spec Section 5.3。

解析策略：
- 优先请求 JSON（Accept: application/json）→ 结构化 data[] 直接转 SearchResult
- fallback 到 text/plain 正则解析 [n] Title / [n] URL Source / [n] Description 段
- 最后兜底：整段作为单条结果（过去的行为）
"""

from __future__ import annotations

import logging
import re
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
        headers = {
            "Accept": "application/json",
            "X-Retain-Images": "none",  # 省 token
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            resp = await client._client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise RuntimeError(f"jina_search_http_error:{type(e).__name__}") from e
        if resp.status_code >= 400:
            raise RuntimeError(f"jina_search_status:{resp.status_code}")

        # 1. 优先走 JSON 解析
        results = _parse_json(resp)
        # 2. 退回 markdown 解析
        if not results:
            results = _parse_markdown(resp.text)
        # 3. 最终兜底：单条 SearchResult
        if not results:
            body = resp.text.strip()
            results = [
                SearchResult(
                    title=f"Jina Search: {query}",
                    url=url,
                    snippet=body[:4000],
                    score=None,
                )
            ]

        return SearchOutcome(
            results=tuple(results[:max_results]),
            raw_api_meta={
                "bytes": len(resp.content or b""),
                "parsed_count": len(results),
                "max_results": max_results,
            },
        )


# ---- parsers ----

def _parse_json(resp: httpx.Response) -> list[SearchResult]:
    """Jina JSON: {'code': 200, 'data': [{'title', 'url', 'description', 'content'?}, ...]}"""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "application/json" not in ctype:
        return []
    try:
        payload = resp.json()
    except Exception:
        return []
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    out: list[SearchResult] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        link = (it.get("url") or "").strip()
        snippet = (it.get("description") or it.get("content") or "").strip()
        if not title or not link:
            continue
        out.append(
            SearchResult(
                title=title,
                url=link,
                snippet=snippet[:1000],  # 单条 snippet 限制 1KB，给 LLM 看预览足够
                score=None,
            )
        )
    return out


# 匹配 "[1] Title: Xxx" / "[1] URL Source: https://..." 这样的 Jina 文本分段
_KV_PATTERN = re.compile(r"^\[(\d+)\]\s+([A-Za-z][A-Za-z0-9 _-]*):\s*(.+)$", re.M)


def _parse_markdown(body: str) -> list[SearchResult]:
    """从 Jina 的 text/plain markdown 里按 [n] 分块还原 SearchResult。"""
    if not body:
        return []
    # 按 [n] 分组，每组里找 Title / URL Source / Description / Content
    groups: dict[int, dict[str, str]] = {}
    for m in _KV_PATTERN.finditer(body):
        idx = int(m.group(1))
        key = m.group(2).strip().lower()
        val = m.group(3).strip()
        groups.setdefault(idx, {})[key] = val

    out: list[SearchResult] = []
    for idx in sorted(groups):
        grp = groups[idx]
        title = grp.get("title", "").strip()
        link = grp.get("url source") or grp.get("url", "")
        snippet = (
            grp.get("description")
            or grp.get("markdown content")
            or grp.get("content")
            or ""
        )
        if not title or not link:
            continue
        out.append(
            SearchResult(
                title=title,
                url=link,
                snippet=snippet[:1000],
                score=None,
            )
        )
    return out


def build_jina_search(store=None) -> JinaSearchEngine | None:
    """DB-first / env fallback；key 可选，无 key 也能用（unauthed 模式）。"""
    from tianshu.secrets import resolve_provider_key

    key, _source = resolve_provider_key(store, "jina", "TIANSHU_JINA_API_KEY")
    return JinaSearchEngine(api_key=key)
