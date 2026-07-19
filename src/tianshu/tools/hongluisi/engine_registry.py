"""按 env/DB 构造所有可用 engine。启动期 build_engines()，运行时 rebuild_engines() 热更。

Spec Section 3.5 + 引擎热更新（2026-04-22）。
"""

from __future__ import annotations

import logging
import threading

from tianshu.secrets import resolve_provider_key
from tianshu.tools.hongluisi.engines import FetchEngine, SearchEngine
from tianshu.tools.hongluisi.engines.duckduckgo_search import build_duckduckgo
from tianshu.tools.hongluisi.engines.firecrawl import build_firecrawl
from tianshu.tools.hongluisi.engines.firecrawl_extract import build_firecrawl_extract
from tianshu.tools.hongluisi.engines.jina_reader import build_jina_reader
from tianshu.tools.hongluisi.engines.jina_search import build_jina_search
from tianshu.tools.hongluisi.engines.local_fetch import LocalFetchEngine
from tianshu.tools.hongluisi.engines.scrapling_fetch import build_scrapling
from tianshu.tools.hongluisi.engines.tavily import build_tavily

logger = logging.getLogger(__name__)


_fetch_engines: dict[str, FetchEngine] = {}
_search_engines: dict[str, SearchEngine] = {}
# provider → 当前真正用的 key 来源 ("db" | "env" | "none")；UI 用它做权威 badge
_provider_sources: dict[str, str] = {"jina": "none", "tavily": "none", "firecrawl": "none"}
_api_engine = None  # ApiRequestEngine | None
_extract_engine = None  # FirecrawlExtractEngine | None
_storage = None  # Storage | None; 启动期存，rebuild 用
_rebuild_lock = threading.Lock()

PROVIDER_ENV_VARS: dict[str, str] = {
    "jina": "TIANSHU_JINA_API_KEY",
    "tavily": "TIANSHU_TAVILY_API_KEY",
    "firecrawl": "TIANSHU_FIRECRAWL_API_KEY",
}


def _do_build(
    storage,
) -> tuple[
    dict[str, FetchEngine],
    dict[str, SearchEngine],
    object,
    object,
    dict[str, str],
]:
    """内部实现：构造所有 engine 并返回；不 mutate module state。"""
    # 懒 import 避免循环
    from tianshu.secrets import CredentialInjector, CredentialStore, get_vault
    from tianshu.tools.hongluisi.api_request import ApiRequestEngine

    # 1. 构造 CredentialStore（如果 vault + storage 都齐全）
    vault = get_vault()
    cred_store = None
    api_engine = None
    if vault is not None and storage is not None:
        cred_store = CredentialStore(storage, vault)
        api_engine = ApiRequestEngine(CredentialInjector(cred_store))

    # 2. 捕获每个 provider 当前实际用的 key 来源
    sources = {
        name: resolve_provider_key(cred_store, name, env)[1]
        for name, env in PROVIDER_ENV_VARS.items()
    }

    # 3. 构造 fetch engines
    fetch: dict[str, FetchEngine] = {"local": LocalFetchEngine()}
    # Scrapling 免费引擎：http 模式默认注册；浏览器模式受 engine_preferences 开关控制
    prefs = storage.get_engine_preferences() if storage is not None else {}
    sc_http = build_scrapling("http")
    if sc_http:
        fetch["scrapling"] = sc_http
    if prefs.get("scrapling_dynamic_enabled"):
        sc_dyn = build_scrapling("dynamic")
        if sc_dyn:
            fetch["scrapling_dynamic"] = sc_dyn
    if prefs.get("scrapling_stealthy_enabled"):
        sc_sth = build_scrapling("stealthy")
        if sc_sth:
            fetch["scrapling_stealthy"] = sc_sth
    jina_r = build_jina_reader(cred_store)
    if jina_r:
        fetch["jina"] = jina_r
    fc = build_firecrawl(cred_store)
    if fc:
        fetch["firecrawl"] = fc

    # 4. 构造 search providers
    search: dict[str, SearchEngine] = {}
    ddg = build_duckduckgo()  # lxml 未装时为 None（可选依赖，不注册而非崩启动）
    if ddg:
        search["duckduckgo"] = ddg
    tv = build_tavily(cred_store)
    if tv:
        search["tavily"] = tv
    js = build_jina_search(cred_store)
    if js:
        search["jina"] = js

    # 5. 构造 extract engine
    extract = build_firecrawl_extract(cred_store)

    return fetch, search, api_engine, extract, sources


def build_engines(
    storage=None,
) -> tuple[dict[str, FetchEngine], dict[str, SearchEngine]]:
    """启动期调用一次。储存 storage 引用供 rebuild_engines 使用。"""
    global _fetch_engines, _search_engines, _provider_sources
    global _api_engine, _extract_engine, _storage

    _storage = storage  # 保留给 rebuild

    fetch, search, api, extract, sources = _do_build(storage)

    _fetch_engines = fetch
    _search_engines = search
    _api_engine = api
    _extract_engine = extract
    _provider_sources = sources

    logger.info(
        "[hongluisi] fetch engines: %s; search providers: %s; provider sources: %s; "
        "api_engine=%s; extract_engine=%s",
        list(fetch),
        list(search),
        sources,
        api is not None,
        extract is not None,
    )
    return fetch, search


def rebuild_engines() -> dict[str, str]:
    """live 重建。凭证变更后调；下次 tool 调用就用上新 key。

    返回新的 provider_sources。失败保留旧状态，抛异常让 caller 决定如何提示。
    """
    global _fetch_engines, _search_engines, _provider_sources
    global _api_engine, _extract_engine

    with _rebuild_lock:
        if _storage is None:
            raise RuntimeError("engine_registry not initialized; call build_engines(storage) first")
        fetch, search, api, extract, sources = _do_build(_storage)
        # 原子替换（dict 赋值 GIL 保护）
        _fetch_engines = fetch
        _search_engines = search
        _api_engine = api
        _extract_engine = extract
        _provider_sources = sources

    logger.info("[hongluisi] engines rebuilt. provider sources: %s", sources)
    return dict(sources)


def reset_engines() -> None:
    """测试用：清空所有 module state。"""
    global _fetch_engines, _search_engines, _provider_sources
    global _api_engine, _extract_engine, _storage
    _fetch_engines = {}
    _search_engines = {}
    _provider_sources = {"jina": "none", "tavily": "none", "firecrawl": "none"}
    _api_engine = None
    _extract_engine = None
    _storage = None


def get_provider_sources() -> dict[str, str]:
    """返回当前真正绑定的 key 来源快照（UI 用）。"""
    return dict(_provider_sources)


def get_registered_fetch_engines() -> set[str]:
    return set(_fetch_engines.keys())


def get_registered_search_providers() -> set[str]:
    return set(_search_engines.keys())


def get_fetch_engines_map() -> dict[str, FetchEngine]:
    return dict(_fetch_engines)


def get_search_providers_map() -> dict[str, SearchEngine]:
    return dict(_search_engines)


def get_api_engine():
    return _api_engine


def get_extract_engine():
    return _extract_engine
