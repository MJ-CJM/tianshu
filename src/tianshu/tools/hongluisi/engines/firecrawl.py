"""Firecrawl engine：api.firecrawl.dev/v1/scrape。Spec Section 5.3。"""

from __future__ import annotations

import logging

import httpx

from tianshu.tools.hongluisi.engines import FetchOutcome
from tianshu.tools.hongluisi.http_client import SharedHttpClient
from tianshu.tools.hongluisi.markdown_extract import is_empty
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"


class FirecrawlEngine:
    name = "firecrawl"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("FirecrawlEngine requires api_key")
        self._api_key = api_key

    async def fetch(self, url: str) -> FetchOutcome:
        try:
            clean_url = await validate_url(url)
        except SSRFViolation as v:
            return FetchOutcome(
                content="",
                status="error",
                http_status=None,
                reason=v.code,
                bytes_fetched=0,
                final_url=None,
            )
        client = SharedHttpClient.instance()
        body = {
            "url": clean_url,
            "formats": ["markdown"],
            "onlyMainContent": True,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            data, http_status = await client.post_json(
                FIRECRAWL_ENDPOINT, body, headers=headers
            )
        except httpx.TimeoutException:
            return FetchOutcome(
                content="",
                status="error",
                http_status=None,
                reason="timeout",
                bytes_fetched=0,
                final_url=None,
            )
        except httpx.HTTPError as e:
            return FetchOutcome(
                content="",
                status="error",
                http_status=None,
                reason=f"http_error:{type(e).__name__}",
                bytes_fetched=0,
                final_url=None,
            )
        if http_status >= 400 or not data.get("success", True):
            return FetchOutcome(
                content="",
                status="error",
                http_status=http_status,
                reason=f"firecrawl_error:{http_status}",
                bytes_fetched=0,
                final_url=None,
            )
        markdown = (data.get("data") or {}).get("markdown", "")
        bytes_read = len(markdown.encode("utf-8", errors="ignore"))
        status = "empty" if is_empty(markdown) else "ok"
        return FetchOutcome(
            content=markdown,
            status=status,
            http_status=http_status,
            reason=None if status == "ok" else "firecrawl_empty",
            bytes_fetched=bytes_read,
            final_url=clean_url,
        )


def build_firecrawl(store=None) -> FirecrawlEngine | None:
    """DB-first / env fallback；无 key 时返回 None，上层不注册该引擎。"""
    from tianshu.secrets import resolve_provider_key

    key, _source = resolve_provider_key(
        store, "firecrawl", "TIANSHU_FIRECRAWL_API_KEY"
    )
    if not key:
        return None
    return FirecrawlEngine(api_key=key)
