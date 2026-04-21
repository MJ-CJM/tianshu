"""Fetch / Search Engine 协议定义 + 共享数据类。

Spec Section 5.3。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

FetchStatus = Literal["ok", "empty", "error"]


@dataclass(frozen=True)
class FetchOutcome:
    content: str  # Markdown；失败/empty 时可能为空
    status: FetchStatus
    http_status: int | None
    reason: str | None
    bytes_fetched: int
    final_url: str | None
    cached: bool = False


@dataclass(frozen=True)
class FetchAttempt:
    engine: str
    status: str  # "ok" | "empty" | "error" | "skipped"
    reason: str | None


class FetchEngine(Protocol):
    name: str

    async def fetch(self, url: str) -> FetchOutcome: ...


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float | None = None


@dataclass(frozen=True)
class SearchOutcome:
    results: tuple[SearchResult, ...]
    raw_api_meta: dict  # provider 返回的 usage / response_time 等


class SearchEngine(Protocol):
    name: str

    async def search(self, query: str, max_results: int) -> SearchOutcome: ...
