"""DuckDuckGo search engine：爬 html.duckduckgo.com/html/ 结果页。免费、无 key。"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx
import lxml.html

from tianshu.tools.hongluisi.engines import SearchOutcome, SearchResult
from tianshu.tools.hongluisi.http_client import SharedHttpClient

logger = logging.getLogger(__name__)

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class DuckDuckGoSearchEngine:
    name = "duckduckgo"

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        client = SharedHttpClient.instance()
        url = f"{DDG_HTML_ENDPOINT}?q={quote_plus(query)}"
        try:
            resp = await client._client.get(url, headers={"User-Agent": _UA})
        except httpx.HTTPError as e:
            raise RuntimeError(f"duckduckgo_http_error:{type(e).__name__}") from e
        if resp.status_code >= 400:
            raise RuntimeError(f"duckduckgo_status:{resp.status_code}")

        results = _parse(resp.text)[:max_results]
        return SearchOutcome(
            results=tuple(results),
            raw_api_meta={"parsed_count": len(results), "max_results": max_results},
        )


def _parse(html_text: str) -> list[SearchResult]:
    """从 DuckDuckGo HTML 结果页解析 SearchResult 列表。

    按 .result 容器逐块解析：每块内取 .result__a（标题+链接）与
    .result__snippet（摘要）。结构化配对而非全局索引配对——避免广告/无摘要块
    导致后续 snippet 整体错位。
    """
    if not html_text:
        return []
    try:
        tree = lxml.html.fromstring(html_text)
    except Exception:
        logger.warning("duckduckgo: failed to parse HTML")
        return []

    out: list[SearchResult] = []
    for block in tree.find_class("result"):
        anchors = block.find_class("result__a")
        if not anchors:
            continue
        anchor = anchors[0]
        title = anchor.text_content().strip()
        real_url = _unwrap_ddg(anchor.get("href") or "")
        if not title or not real_url:
            continue
        snippet_els = block.find_class("result__snippet")
        snippet = snippet_els[0].text_content().strip() if snippet_els else ""
        out.append(
            SearchResult(
                title=title,
                url=real_url,
                snippet=snippet[:1000],
                score=None,
            )
        )
    return out


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo 跳转链接 //duckduckgo.com/l/?uddg=<encoded> 还原真实 URL。"""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return uddg[0]
    return href


def build_duckduckgo() -> DuckDuckGoSearchEngine:
    """无依赖、无 key，总是可注册。"""
    return DuckDuckGoSearchEngine()
