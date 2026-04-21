"""按 env 构造所有可用 engine。启动期调用一次。

Spec Section 3.5。
"""

from __future__ import annotations

import logging

from tianshu.tools.hongluisi.engines import FetchEngine, SearchEngine
from tianshu.tools.hongluisi.engines.firecrawl import build_firecrawl
from tianshu.tools.hongluisi.engines.jina_reader import build_jina_reader
from tianshu.tools.hongluisi.engines.jina_search import build_jina_search
from tianshu.tools.hongluisi.engines.local_fetch import LocalFetchEngine
from tianshu.tools.hongluisi.engines.tavily import build_tavily

logger = logging.getLogger(__name__)


_fetch_engines: dict[str, FetchEngine] = {}
_search_engines: dict[str, SearchEngine] = {}


def build_engines() -> tuple[dict[str, FetchEngine], dict[str, SearchEngine]]:
    """启动期构造；之后 get_registered_* 只读。"""
    global _fetch_engines, _search_engines

    fetch: dict[str, FetchEngine] = {"local": LocalFetchEngine()}
    jina_r = build_jina_reader()
    if jina_r:
        fetch["jina"] = jina_r
    fc = build_firecrawl()
    if fc:
        fetch["firecrawl"] = fc

    search: dict[str, SearchEngine] = {}
    tv = build_tavily()
    if tv:
        search["tavily"] = tv
    js = build_jina_search()
    if js:
        search["jina"] = js

    _fetch_engines = fetch
    _search_engines = search
    logger.info(
        "[hongluisi] fetch engines: %s; search providers: %s",
        list(fetch),
        list(search),
    )
    return fetch, search


def get_registered_fetch_engines() -> set[str]:
    return set(_fetch_engines.keys())


def get_registered_search_providers() -> set[str]:
    return set(_search_engines.keys())


def get_fetch_engines_map() -> dict[str, FetchEngine]:
    return dict(_fetch_engines)


def get_search_providers_map() -> dict[str, SearchEngine]:
    return dict(_search_engines)
