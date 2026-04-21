"""Jina Reader engine：r.jina.ai 代理。Spec Section 5.3。"""

from __future__ import annotations

import logging
import os

import httpx

from tianshu.tools.hongluisi.engines import FetchOutcome
from tianshu.tools.hongluisi.http_client import BodyTooLarge, SharedHttpClient
from tianshu.tools.hongluisi.markdown_extract import is_empty
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

JINA_READER_BASE = "https://r.jina.ai"


class JinaReaderEngine:
    name = "jina"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def fetch(self, url: str) -> FetchOutcome:
        # 先对目标 URL 做 SSRF 校验 —— 代理不是安全边界
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
        proxied = f"{JINA_READER_BASE}/{clean_url}"
        client = SharedHttpClient.instance()
        try:
            if self._api_key:
                # 带 key：通过 _client 直接请求以附加 header
                resp = await client._client.get(
                    proxied,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "text/plain",
                    },
                )
                body = resp.text
                http_status = resp.status_code
                final = str(resp.url)
                bytes_read = len(body.encode("utf-8", errors="ignore"))
                cached = False
            else:
                body, meta, cached = await client.get_cached(proxied, engine=self.name)
                http_status = int(meta["http_status"])
                final = meta["final_url"]
                bytes_read = meta["bytes_fetched"]
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

        if http_status >= 400:
            return FetchOutcome(
                content="",
                status="error",
                http_status=http_status,
                reason=f"http_status:{http_status}",
                bytes_fetched=bytes_read,
                final_url=final,
                cached=cached,
            )
        status = "empty" if is_empty(body) else "ok"
        return FetchOutcome(
            content=body,
            status=status,
            http_status=http_status,
            reason=None if status == "ok" else "jina_empty",
            bytes_fetched=bytes_read,
            final_url=final,
            cached=cached,
        )


def build_jina_reader() -> JinaReaderEngine | None:
    """按 env 构造；key 可选，无 key 也能用（20 req/min）。"""
    key = os.getenv("TIANSHU_JINA_API_KEY")
    return JinaReaderEngine(api_key=key)
