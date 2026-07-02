"""Scrapling fetch engines：免费、无 key 的抓取引擎。

三个注册名共用一个类，靠 mode 区分：
- scrapling          → Fetcher（TLS 指纹伪装 HTTP，纯 pip，轻量）
- scrapling_dynamic  → DynamicFetcher（Playwright Chromium，渲染 JS）
- scrapling_stealthy → StealthyFetcher（Camoufox，过 Cloudflare）
"""

from __future__ import annotations

import logging

from tianshu.tools.hongluisi.engines import FetchOutcome
from tianshu.tools.hongluisi.markdown_extract import extract_markdown, is_empty
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

# mode → 注册名
MODE_TO_NAME: dict[str, str] = {
    "http": "scrapling",
    "dynamic": "scrapling_dynamic",
    "stealthy": "scrapling_stealthy",
}

# 浏览器引擎超时（毫秒）；http 模式用秒
_DYNAMIC_TIMEOUT_MS = 30_000
_STEALTHY_TIMEOUT_MS = 60_000
_HTTP_TIMEOUT_S = 30


class ScraplingFetchEngine:
    """实现 FetchEngine 协议。mode ∈ {http, dynamic, stealthy}。"""

    def __init__(self, mode: str) -> None:
        if mode not in MODE_TO_NAME:
            raise ValueError(f"unknown scrapling mode: {mode}")
        self._mode = mode
        self.name = MODE_TO_NAME[mode]

    async def fetch(self, url: str) -> FetchOutcome:
        # Scrapling 不是安全边界，先做 SSRF 校验
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

        try:
            page = await self._invoke(clean_url)
        except Exception as e:
            logger.exception("scrapling %s fetch failed", self._mode)
            return FetchOutcome(
                content="",
                status="error",
                http_status=None,
                reason=f"scrapling_error:{type(e).__name__}",
                bytes_fetched=0,
                final_url=None,
            )

        http_status = int(getattr(page, "status", 0) or 0)
        body = getattr(page, "body", b"") or b""
        bytes_read = len(body)
        encoding = getattr(page, "encoding", None) or "utf-8"
        final = getattr(page, "url", None) or clean_url

        # 重定向后的最终 URL 再校验一次 SSRF —— 浏览器模式可能跟随跳转到内网，
        # 不能把内网内容回吐给 LLM。
        if final != clean_url:
            try:
                await validate_url(final)
            except SSRFViolation as v:
                return FetchOutcome(
                    content="",
                    status="error",
                    http_status=http_status,
                    reason=v.code,
                    bytes_fetched=bytes_read,
                    final_url=final,
                )

        if http_status >= 400:
            return FetchOutcome(
                content="",
                status="error",
                http_status=http_status,
                reason=f"http_status:{http_status}",
                bytes_fetched=bytes_read,
                final_url=final,
            )

        html = body.decode(encoding, errors="ignore")
        markdown = extract_markdown(html, url=clean_url)
        status = "empty" if is_empty(markdown) else "ok"
        return FetchOutcome(
            content=markdown,
            status=status,
            http_status=http_status,
            reason=None if status == "ok" else "extracted_empty",
            bytes_fetched=bytes_read,
            final_url=final,
        )

    async def _invoke(self, url: str):
        """调对应的 Scrapling async API；返回 Scrapling Response 对象。"""
        if self._mode == "http":
            from scrapling.fetchers import AsyncFetcher

            # follow_redirects="safe" 显式声明：跳转到私网/内网 IP 会被拒，
            # 不依赖上游默认值（pin 仅 >=0.3）。
            return await AsyncFetcher.get(
                url,
                timeout=_HTTP_TIMEOUT_S,
                stealthy_headers=True,
                follow_redirects="safe",
            )
        if self._mode == "dynamic":
            from scrapling.fetchers import DynamicFetcher

            return await DynamicFetcher.async_fetch(
                url,
                timeout=_DYNAMIC_TIMEOUT_MS,
                headless=True,
            )
        from scrapling.fetchers import StealthyFetcher

        return await StealthyFetcher.async_fetch(
            url,
            timeout=_STEALTHY_TIMEOUT_MS,
            headless=True,
        )


def build_scrapling(mode: str) -> ScraplingFetchEngine | None:
    """Scrapling 未安装时返回 None（引擎不注册），沿用"无 key 即不注册"模式。"""
    try:
        import scrapling  # noqa: F401
    except ImportError:
        return None
    return ScraplingFetchEngine(mode)
