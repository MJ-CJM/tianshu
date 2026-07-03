"""HTML → Markdown 提取。仅 LocalFetchEngine 使用。

Spec Section 5.3。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MIN_EXTRACTED_LEN = 500  # 小于此数视为 empty（router 判断是否 fallback）


def extract_markdown(html: str, url: str | None = None) -> str:
    """从 HTML 提取可读 Markdown。失败或几乎空时返回空串。"""
    if not html or not html.strip():
        return ""
    # 惰性导入：trafilatura 是可选依赖（web extra），只有真正提取时才需要，
    # 保持 tianshu.app 的核心 import 链不触碰它。
    try:
        import trafilatura
    except ImportError as exc:
        raise ImportError(
            "本地网页正文提取依赖未安装，请执行: pip install 'tianshu[web]'"
        ) from exc
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
