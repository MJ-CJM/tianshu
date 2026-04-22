"""共享 httpx.AsyncClient + TTL 响应缓存。单例。

Spec Section 5.2。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from cachetools import TTLCache

from tianshu.tools.hongluisi.ssrf_guard import validate_url

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB
REQUEST_TIMEOUT_SEC = 15.0
CONNECT_TIMEOUT_SEC = 5.0
CACHE_MAXSIZE = 32
CACHE_TTL_SEC = 300


class BodyTooLarge(Exception):
    def __init__(self, bytes_read: int) -> None:
        super().__init__(f"body exceeded {MAX_BODY_BYTES} bytes")
        self.bytes_read = bytes_read


class SharedHttpClient:
    """跨 Edict 共享连接池、DNS 缓存、TTL 响应缓存。"""

    _instance: "SharedHttpClient | None" = None

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SEC, connect=CONNECT_TIMEOUT_SEC),
            follow_redirects=True,
            max_redirects=5,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            headers={"User-Agent": "tianshu-hongluisi/0.1 (+https://github.com/)"},
            event_hooks={"response": [self._on_redirect]},
        )
        self._cache: TTLCache = TTLCache(maxsize=CACHE_MAXSIZE, ttl=CACHE_TTL_SEC)

    @classmethod
    def instance(cls) -> "SharedHttpClient":
        if cls._instance is None:
            cls._instance = SharedHttpClient()
        return cls._instance

    @classmethod
    async def reset(cls) -> None:
        if cls._instance is not None:
            await cls._instance._client.aclose()
            cls._instance = None

    async def _on_redirect(self, response: httpx.Response) -> None:
        if response.is_redirect:
            loc = response.headers.get("location")
            if loc:
                await validate_url(loc)

    async def get_cached(
        self, url: str, *, engine: str, credential_name: str | None = None
    ) -> tuple[str, dict[str, Any], bool]:
        """返回 (body_text, meta, cached_flag)。未命中时做真实 GET。"""
        key = (url, engine, credential_name)
        if key in self._cache:
            body, meta = self._cache[key]
            return body, meta, True

        bytes_read = 0
        chunks: list[bytes] = []
        async with self._client.stream("GET", url) as resp:
            # 预检 Content-Length
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
                raise BodyTooLarge(int(cl))
            async for chunk in resp.aiter_bytes():
                bytes_read += len(chunk)
                if bytes_read > MAX_BODY_BYTES:
                    raise BodyTooLarge(bytes_read)
                chunks.append(chunk)
            body_bytes = b"".join(chunks)
            body = body_bytes.decode(resp.encoding or "utf-8", errors="replace")
            meta: dict[str, Any] = {
                "http_status": resp.status_code,
                "content_type": resp.headers.get("content-type", ""),
                "final_url": str(resp.url),
                "bytes_fetched": bytes_read,
            }
        self._cache[key] = (body, meta)
        return body, meta, False

    async def post_json(
        self,
        url: str,
        json_body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """返回 (parsed_json, http_status)。不缓存。"""
        resp = await self._client.post(url, json=json_body, headers=headers or {})
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:500]}
        return data, resp.status_code
