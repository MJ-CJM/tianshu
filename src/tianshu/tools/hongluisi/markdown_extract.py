"""HTML → Markdown 提取。仅 LocalFetchEngine 使用。

Spec Section 5.3。
"""

from __future__ import annotations

import logging

import trafilatura

logger = logging.getLogger(__name__)

MIN_EXTRACTED_LEN = 500  # 小于此数视为 empty（router 判断是否 fallback）


def extract_markdown(html: str, url: str | None = None) -> str:
    """从 HTML 提取可读 Markdown。失败或几乎空时返回空串。"""
    if not html or not html.strip():
        return ""
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=True,
            favor_precision=True,
        )
    except Exception as e:
        logger.warning("trafilatura.extract raised: %s", e)
        return ""
    return extracted or ""


def is_empty(content: str) -> bool:
    return len(content.strip()) < MIN_EXTRACTED_LEN
