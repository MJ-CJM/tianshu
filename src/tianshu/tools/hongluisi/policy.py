"""NetworkPolicy — 鸿胪寺的 per-Edict 策略配置。

Spec Section 3.4。作为 PolicyProfile 的子字段嵌入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NetworkPolicy:
    """对外网络通讯策略。

    - fetch_engines: 按优先级排序的 engine 名字（length=1 即钉死）
    - fallback_mode: 什么情况下跳下一个 engine
        - "none": 只用 fetch_engines[0]
        - "on_error_or_empty": 硬错误 / 4xx-5xx / 空内容 都 fallback
    - search_provider: 单 provider，None 表示禁用 search
    """

    fetch_engines: tuple[str, ...] = ("local",)
    fallback_mode: Literal["none", "on_error_or_empty"] = "none"
    search_provider: Literal["tavily", "jina"] | None = None
    max_fallback_depth: int = 3
    web_fetch_rate_per_min: int = 20
    web_search_rate_per_min: int = 10
    # api_request (L1) + web_extract (L4) — Spec §7.1
    allow_api_request: bool = False
    api_request_methods: tuple[str, ...] = ("GET", "HEAD")
    api_request_rate_per_min: int = 30
    web_extract_rate_per_min: int = 10


# 三档预设 —— 由 PolicyProfile 引用
NETWORK_OFFLINE = NetworkPolicy(
    fetch_engines=("local",),
    fallback_mode="none",
    search_provider=None,
)

NETWORK_DEFAULT = NetworkPolicy(
    fetch_engines=("local", "jina"),
    fallback_mode="on_error_or_empty",
    search_provider="tavily",
)

NETWORK_RESEARCH = NetworkPolicy(
    fetch_engines=("local", "jina", "firecrawl"),
    fallback_mode="on_error_or_empty",
    search_provider="tavily",
)
