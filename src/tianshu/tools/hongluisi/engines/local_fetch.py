"""Local fetch engine：httpx + trafilatura。Spec Section 5.3。"""

from __future__ import annotations

import logging

import httpx

from tianshu.tools.hongluisi.engines import FetchOutcome
from tianshu.tools.hongluisi.http_client import BodyTooLarge, SharedHttpClient
from tianshu.tools.hongluisi.markdown_extract import extract_markdown, is_empty
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

SUPPORTED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/xhtml",
)


class LocalFetchEngine:
    name = "local"

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
        try:
            body, meta, cached = await client.get_cached(clean_url, engine=self.name)
        except BodyTooLarge as e:
            return FetchOutcome(
                content="",
                status="error",
                http_status=None,
                reason=f"response_too_large:{e.bytes_read}",
                bytes_fetched=e.bytes_read,
                final_url=None,
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

        http_status = int(meta["http_status"])
        if http_status >= 400:
            return FetchOutcome(
                content="",
                status="error",
                http_status=http_status,
                reason=f"http_status:{http_status}",
                bytes_fetched=meta["bytes_fetched"],
                final_url=meta["final_url"],
                cached=cached,
            )

        content_type = meta.get("content_type", "").lower()
        if content_type and not any(t in content_type for t in SUPPORTED_CONTENT_TYPES):
            return FetchOutcome(
                content="",
                status="error",
                http_status=http_status,
                reason=f"unsupported_content_type:{content_type}",
                bytes_fetched=meta["bytes_fetched"],
                final_url=meta["final_url"],
                cached=cached,
            )

        # text/plain / text/markdown 直接当 Markdown，不过 trafilatura
        if "html" in content_type or "xhtml" in content_type:
            markdown = extract_markdown(body, url=clean_url)
        else:
            markdown = body

        status = "empty" if is_empty(markdown) else "ok"
        return FetchOutcome(
            content=markdown,
            status=status,
            http_status=http_status,
            reason=None if status == "ok" else "extracted_empty",
            bytes_fetched=meta["bytes_fetched"],
            final_url=meta["final_url"],
            cached=cached,
        )
