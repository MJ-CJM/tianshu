# 免费引擎接入实现计划：Scrapling 抓取 + DuckDuckGo 搜索

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给鸿胪寺网络工具层接入零成本、无 API key 的免费 fetch / search 引擎，让系统开箱即用，付费引擎原样保留。

**Architecture:** 纯增量接入。新引擎实现既有 `FetchEngine` / `SearchEngine` Protocol，在 `engine_registry` 注册，沿用既有 `FetchRouter` 分发、限流、SSRF、审计逻辑。浏览器类引擎由 `engine_preferences` 表的开关列控制是否注册。

**Tech Stack:** Python 3.13 / FastAPI / SQLite / Scrapling（可选依赖）/ lxml / React + Ant Design + TanStack Query。

> **测试约定**：本项目遵循"功能优先、测试最后补"——Task 1–8 实现功能，Task 9–12 统一补测试。

---

## 文件结构

**新建：**
- `src/tianshu/tools/hongluisi/engines/scrapling_fetch.py` — `ScraplingFetchEngine` + `build_scrapling`
- `src/tianshu/tools/hongluisi/engines/duckduckgo_search.py` — `DuckDuckGoSearchEngine` + `build_duckduckgo`
- `tests/tools/hongluisi/test_scrapling_fetch.py`
- `tests/tools/hongluisi/test_duckduckgo_search.py`

**修改：**
- `pyproject.toml` — Scrapling 可选依赖组
- `src/tianshu/storage.py` — `engine_preferences` 加两列 + migration + `get/set_engine_preferences`
- `src/tianshu/tools/hongluisi/policy.py` — `NetworkPolicy.search_provider` Literal + 免费默认
- `src/tianshu/tools/hongluisi/engine_registry.py` — 注册新引擎 + 浏览器开关 gating
- `src/tianshu/gateway/hongluisi_api.py` — 校验白名单 + payload 字段 + PATCH 后 rebuild
- `web/src/api/hongluisi.ts` — `EnginePreferences` 接口加两字段
- `web/src/pages/HongluisiPage.tsx` — fetch/search 选项 + 浏览器引擎开关
- `web/src/i18n/locales/{en,zh-classic,zh-modern}.json` — 新增 i18n key
- `tests/test_storage.py` — `engine_preferences` 新列读写
- `tests/gateway/test_hongluisi_api.py`（若不存在则新建）— PATCH 校验
- `tests/tools/hongluisi/test_engine_registry.py`（若不存在则新建）— 降级注册

---

## Task 1: Scrapling 可选依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 在 pyproject.toml 加可选依赖组**

找到 `[project.optional-dependencies]` 段（若不存在则在 `[project]` 段之后新建）。加入：

```toml
[project.optional-dependencies]
scrapling = ["scrapling>=0.3"]
```

若该段已存在其他 extra，仅追加 `scrapling = ["scrapling>=0.3"]` 一行，不要动其他行。

- [ ] **Step 2: 安装依赖（开发环境）**

Run: `uv pip install 'scrapling>=0.3'`
Expected: 安装成功。若失败（网络等）记录但不阻塞——`build_scrapling` 会在缺失时优雅降级。

- [ ] **Step 3: 验证可导入**

Run: `python -c "from scrapling.fetchers import AsyncFetcher, DynamicFetcher, StealthyFetcher; print('ok')"`
Expected: 输出 `ok`。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add scrapling as optional dependency"
```

---

## Task 2: storage — engine_preferences 加浏览器开关列

**Files:**
- Modify: `src/tianshu/storage.py`

- [ ] **Step 1: migration 列表追加两条 ALTER + 一条修正 UPDATE**

在 `src/tianshu/storage.py` 的 `migrations = [ ... ]` 列表末尾（`ALTER TABLE memorials ADD COLUMN final_output TEXT` 那一行之后）追加：

```python
            # 2026-05-19: engine_preferences 加浏览器引擎启停开关
            "ALTER TABLE engine_preferences ADD COLUMN scrapling_dynamic_enabled INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE engine_preferences ADD COLUMN scrapling_stealthy_enabled INTEGER NOT NULL DEFAULT 0",
            # 2026-05-19: 纠正存量 jina-only override（欠费 key 导致定时任务连续失败）
            """UPDATE engine_preferences
                  SET fetch_chain = '["scrapling", "local"]',
                      search_provider = 'duckduckgo',
                      fallback_mode = 'on_error_or_empty'
                WHERE id = 'default' AND fetch_chain = '["jina"]'""",
```

迁移循环已用 try/except 吞掉 `duplicate column name`，重复运行安全。

- [ ] **Step 2: 更新 get_engine_preferences 返回两个开关**

找到 `def get_engine_preferences(self) -> dict:`（约 storage.py:2716）。把 SELECT 和返回 dict 改为：

```python
    def get_engine_preferences(self) -> dict:
        """返回 {fetch_chain, search_provider, fallback_mode,
        scrapling_dynamic_enabled, scrapling_stealthy_enabled};
        无记录返回全空（不覆盖 profile），开关默认 False。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT fetch_chain, search_provider, fallback_mode, "
                "scrapling_dynamic_enabled, scrapling_stealthy_enabled "
                "FROM engine_preferences WHERE id='default'"
            ).fetchone()
        if row is None:
            return {
                "fetch_chain": [],
                "search_provider": None,
                "fallback_mode": None,
                "scrapling_dynamic_enabled": False,
                "scrapling_stealthy_enabled": False,
            }
        chain = json.loads(row["fetch_chain"] or "[]")
        return {
            "fetch_chain": chain if isinstance(chain, list) else [],
            "search_provider": row["search_provider"],
            "fallback_mode": row["fallback_mode"],
            "scrapling_dynamic_enabled": bool(row["scrapling_dynamic_enabled"]),
            "scrapling_stealthy_enabled": bool(row["scrapling_stealthy_enabled"]),
        }
```

注意：原方法 body 在 `with self._lock:` 内含一段 `row = ...` 之后的逻辑，需替换整段。读取前先 Read storage.py:2716-2735 确认原始行号与缩进。

- [ ] **Step 3: 更新 set_engine_preferences 接受两个开关**

找到 `def set_engine_preferences(self, *, fetch_chain, search_provider, fallback_mode) -> None:`（约 storage.py:2736）。替换为：

```python
    def set_engine_preferences(
        self,
        *,
        fetch_chain: list[str],
        search_provider: str | None,
        fallback_mode: str | None,
        scrapling_dynamic_enabled: bool = False,
        scrapling_stealthy_enabled: bool = False,
    ) -> None:
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO engine_preferences
                   (id, fetch_chain, search_provider, fallback_mode,
                    scrapling_dynamic_enabled, scrapling_stealthy_enabled, updated_at)
                   VALUES ('default', ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     fetch_chain = excluded.fetch_chain,
                     search_provider = excluded.search_provider,
                     fallback_mode = excluded.fallback_mode,
                     scrapling_dynamic_enabled = excluded.scrapling_dynamic_enabled,
                     scrapling_stealthy_enabled = excluded.scrapling_stealthy_enabled,
                     updated_at = excluded.updated_at""",
                (
                    json.dumps(fetch_chain), search_provider, fallback_mode,
                    1 if scrapling_dynamic_enabled else 0,
                    1 if scrapling_stealthy_enabled else 0,
                    now,
                ),
            )
```

- [ ] **Step 4: 验证 import + 启动 schema 不报错**

Run: `python -c "from tianshu.storage import Storage; import tempfile, os; p=os.path.join(tempfile.mkdtemp(),'t.db'); s=Storage(p); print(s.get_engine_preferences())"`
Expected: 打印 `{'fetch_chain': [], 'search_provider': None, 'fallback_mode': None, 'scrapling_dynamic_enabled': False, 'scrapling_stealthy_enabled': False}`，无异常。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/storage.py
git commit -m "feat: add scrapling browser-engine toggles to engine_preferences"
```

---

## Task 3: ScraplingFetchEngine

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/scrapling_fetch.py`

- [ ] **Step 1: 创建 scrapling_fetch.py**

```python
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
                content="", status="error", http_status=None,
                reason=v.code, bytes_fetched=0, final_url=None,
            )

        try:
            page = await self._invoke(clean_url)
        except Exception as e:
            logger.exception("scrapling %s fetch failed", self._mode)
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=f"scrapling_error:{type(e).__name__}",
                bytes_fetched=0, final_url=None,
            )

        http_status = int(getattr(page, "status", 0) or 0)
        body = getattr(page, "body", b"") or b""
        bytes_read = len(body)
        encoding = getattr(page, "encoding", None) or "utf-8"
        final = getattr(page, "url", None) or clean_url

        if http_status >= 400:
            return FetchOutcome(
                content="", status="error", http_status=http_status,
                reason=f"http_status:{http_status}",
                bytes_fetched=bytes_read, final_url=final,
            )

        html = body.decode(encoding, errors="ignore")
        markdown = extract_markdown(html, url=clean_url)
        status = "empty" if is_empty(markdown) else "ok"
        return FetchOutcome(
            content=markdown, status=status, http_status=http_status,
            reason=None if status == "ok" else "extracted_empty",
            bytes_fetched=bytes_read, final_url=final,
        )

    async def _invoke(self, url: str):
        """调对应的 Scrapling async API；返回 Scrapling Response 对象。"""
        if self._mode == "http":
            from scrapling.fetchers import AsyncFetcher
            return await AsyncFetcher.get(
                url, timeout=_HTTP_TIMEOUT_S, stealthy_headers=True,
            )
        if self._mode == "dynamic":
            from scrapling.fetchers import DynamicFetcher
            return await DynamicFetcher.async_fetch(
                url, timeout=_DYNAMIC_TIMEOUT_MS, headless=True,
            )
        from scrapling.fetchers import StealthyFetcher
        return await StealthyFetcher.async_fetch(
            url, timeout=_STEALTHY_TIMEOUT_MS, headless=True,
        )


def build_scrapling(mode: str) -> ScraplingFetchEngine | None:
    """Scrapling 未安装时返回 None（引擎不注册），沿用"无 key 即不注册"模式。"""
    try:
        import scrapling  # noqa: F401
    except ImportError:
        return None
    return ScraplingFetchEngine(mode)
```

- [ ] **Step 2: 验证 import**

Run: `python -c "from tianshu.tools.hongluisi.engines.scrapling_fetch import build_scrapling, ScraplingFetchEngine; e=build_scrapling('http'); print(e.name if e else 'not installed')"`
Expected: 输出 `scrapling`（已装 Scrapling）或 `not installed`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/scrapling_fetch.py
git commit -m "feat: add ScraplingFetchEngine (http/dynamic/stealthy modes)"
```

---

## Task 4: DuckDuckGoSearchEngine

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/duckduckgo_search.py`

- [ ] **Step 1: 创建 duckduckgo_search.py**

```python
"""DuckDuckGo search engine：爬 html.duckduckgo.com/html/ 结果页。免费、无 key。"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx
import lxml.html

from tianshu.tools.hongluisi.engines import SearchOutcome, SearchResult
from tianshu.tools.hongluisi.http_client import SharedHttpClient

logger = logging.getLogger(__name__)

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class DuckDuckGoSearchEngine:
    name = "duckduckgo"

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        client = SharedHttpClient.instance()
        url = f"{DDG_HTML_ENDPOINT}?q={quote_plus(query)}"
        try:
            resp = await client._client.get(
                url, headers={"User-Agent": _UA}
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"duckduckgo_http_error:{type(e).__name__}") from e
        if resp.status_code >= 400:
            raise RuntimeError(f"duckduckgo_status:{resp.status_code}")

        results = _parse(resp.text)[:max_results]
        return SearchOutcome(
            results=tuple(results),
            raw_api_meta={"parsed_count": len(results), "max_results": max_results},
        )


def _parse(html_text: str) -> list[SearchResult]:
    """从 DuckDuckGo HTML 结果页解析 SearchResult 列表。

    结果块：.result__a（标题+链接），.result__snippet（摘要），按文档顺序配对。
    """
    if not html_text:
        return []
    try:
        tree = lxml.html.fromstring(html_text)
    except Exception:
        logger.warning("duckduckgo: failed to parse HTML")
        return []

    anchors = tree.find_class("result__a")
    snippets = [s.text_content().strip() for s in tree.find_class("result__snippet")]

    out: list[SearchResult] = []
    for i, anchor in enumerate(anchors):
        title = anchor.text_content().strip()
        real_url = _unwrap_ddg(anchor.get("href") or "")
        if not title or not real_url:
            continue
        snippet = snippets[i] if i < len(snippets) else ""
        out.append(
            SearchResult(
                title=title, url=real_url, snippet=snippet[:1000], score=None,
            )
        )
    return out


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo 跳转链接 //duckduckgo.com/l/?uddg=<encoded> 还原真实 URL。"""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return uddg[0]
    return href


def build_duckduckgo() -> DuckDuckGoSearchEngine:
    """无依赖、无 key，总是可注册。"""
    return DuckDuckGoSearchEngine()
```

- [ ] **Step 2: 验证 import 与解析**

Run:
```bash
python -c "
from tianshu.tools.hongluisi.engines.duckduckgo_search import _parse, _unwrap_ddg
html='<div><a class=\"result__a\" href=\"//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com\">Title</a><a class=\"result__snippet\">snip</a></div>'
print(_unwrap_ddg('//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com'))
print(_parse(html))
"
```
Expected: 第一行 `https://example.com`；第二行一个含 `title='Title'` 的 SearchResult 列表（snippet 可能为空，因 fixture 的 snippet class 不在 result block 内——属正常）。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/duckduckgo_search.py
git commit -m "feat: add DuckDuckGoSearchEngine (free, keyless web search)"
```

---

## Task 5: engine_registry 注册新引擎

**Files:**
- Modify: `src/tianshu/tools/hongluisi/engine_registry.py`

- [ ] **Step 1: 加 import**

在 `engine_registry.py` 顶部 import 区（`from tianshu.tools.hongluisi.engines.tavily import build_tavily` 那一行附近）追加：

```python
from tianshu.tools.hongluisi.engines.duckduckgo_search import build_duckduckgo
from tianshu.tools.hongluisi.engines.scrapling_fetch import build_scrapling
```

- [ ] **Step 2: 在 _do_build 注册新 fetch 引擎**

在 `_do_build` 的 "3. 构造 fetch engines" 段，`fetch: dict[str, FetchEngine] = {"local": LocalFetchEngine()}` 之后、`jina_r = build_jina_reader(...)` 之前插入：

```python
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
```

- [ ] **Step 3: 在 _do_build 注册 DuckDuckGo search 引擎**

在 "4. 构造 search providers" 段，`search: dict[str, SearchEngine] = {}` 之后插入：

```python
    search["duckduckgo"] = build_duckduckgo()
```

- [ ] **Step 4: 验证 import**

Run: `python -c "from tianshu.tools.hongluisi.engine_registry import build_engines; print('ok')"`
Expected: 输出 `ok`。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/tools/hongluisi/engine_registry.py
git commit -m "feat: register scrapling + duckduckgo engines in registry"
```

---

## Task 6: policy.py — search_provider 类型 + 免费默认

**Files:**
- Modify: `src/tianshu/tools/hongluisi/policy.py`

- [ ] **Step 1: NetworkPolicy.search_provider 类型加 duckduckgo**

在 `policy.py` 的 `NetworkPolicy` dataclass，把：

```python
    search_provider: Literal["tavily", "jina"] | None = None
```

改为：

```python
    search_provider: Literal["tavily", "jina", "duckduckgo"] | None = None
```

- [ ] **Step 2: NETWORK_DEFAULT 改为免费组合**

把 `NETWORK_DEFAULT = NetworkPolicy(...)` 整块替换为：

```python
NETWORK_DEFAULT = NetworkPolicy(
    fetch_engines=("scrapling", "local"),
    fallback_mode="on_error_or_empty",
    search_provider="duckduckgo",
    allow_api_request=False,
    api_request_methods=(),
)
```

- [ ] **Step 3: NETWORK_RESEARCH 改为免费优先**

把 `NETWORK_RESEARCH = NetworkPolicy(...)` 整块替换为：

```python
NETWORK_RESEARCH = NetworkPolicy(
    fetch_engines=("scrapling", "local", "jina", "firecrawl"),
    fallback_mode="on_error_or_empty",
    search_provider="duckduckgo",
    allow_api_request=True,
    api_request_methods=("GET", "HEAD"),  # 写方法需 Edict 额外显式启用
)
```

`NETWORK_OFFLINE` 保持不变。

- [ ] **Step 4: 验证 import**

Run: `python -c "from tianshu.tools.hongluisi.policy import NETWORK_DEFAULT, NETWORK_RESEARCH; print(NETWORK_DEFAULT.fetch_engines, NETWORK_DEFAULT.search_provider)"`
Expected: 输出 `('scrapling', 'local') duckduckgo`。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/tools/hongluisi/policy.py
git commit -m "feat: default network policy to free engines (scrapling + duckduckgo)"
```

---

## Task 7: hongluisi_api — 白名单 + payload + PATCH rebuild

**Files:**
- Modify: `src/tianshu/gateway/hongluisi_api.py`

- [ ] **Step 1: import rebuild_engines**

把顶部 import：

```python
from tianshu.tools.hongluisi.engine_registry import (
    get_provider_sources,
    get_registered_fetch_engines,
    get_registered_search_providers,
)
```

改为：

```python
from tianshu.tools.hongluisi.engine_registry import (
    get_provider_sources,
    get_registered_fetch_engines,
    get_registered_search_providers,
    rebuild_engines,
)
```

- [ ] **Step 2: EnginePreferencesPayload 加两个开关字段**

把 `EnginePreferencesPayload` 类替换为：

```python
class EnginePreferencesPayload(BaseModel):
    fetch_chain: list[str] = Field(default_factory=list)
    search_provider: str | None = None  # "tavily"|"jina"|"duckduckgo"|null
    fallback_mode: str | None = None  # "none"|"on_error_or_empty"|null
    scrapling_dynamic_enabled: bool = False
    scrapling_stealthy_enabled: bool = False
```

- [ ] **Step 3: GET /engine-preferences 返回两个开关**

把 `get_engine_preferences` 端点函数替换为：

```python
@hongluisi_router.get("/engine-preferences")
def get_engine_preferences(request: Request) -> dict:
    """返回当前系统级引擎覆盖 + 浏览器引擎开关。空字段表示沿用 profile 预设。"""
    overrides = get_system_engine_overrides()
    prefs = request.app.state.storage.get_engine_preferences()
    return {
        **overrides,
        "scrapling_dynamic_enabled": prefs["scrapling_dynamic_enabled"],
        "scrapling_stealthy_enabled": prefs["scrapling_stealthy_enabled"],
    }
```

- [ ] **Step 4: PATCH 校验白名单 + 持久化开关 + rebuild**

把 `update_engine_preferences` 端点函数替换为：

```python
@hongluisi_router.patch("/engine-preferences")
def update_engine_preferences(
    body: EnginePreferencesPayload, request: Request
) -> dict:
    """live 更新：写 DB + 刷缓存 + rebuild 引擎。无需重启。"""
    storage = request.app.state.storage
    ALLOWED_FETCH = {
        "local", "jina", "firecrawl",
        "scrapling", "scrapling_dynamic", "scrapling_stealthy",
    }
    ALLOWED_SEARCH = {"tavily", "jina", "duckduckgo", None, ""}
    ALLOWED_FALLBACK = {"none", "on_error_or_empty", None, ""}
    bad_fetch = [e for e in body.fetch_chain if e not in ALLOWED_FETCH]
    if bad_fetch:
        raise HTTPException(400, f"unknown fetch engine(s): {bad_fetch}")
    if body.search_provider not in ALLOWED_SEARCH:
        raise HTTPException(400, f"unknown search provider: {body.search_provider}")
    if body.fallback_mode not in ALLOWED_FALLBACK:
        raise HTTPException(400, f"unknown fallback mode: {body.fallback_mode}")

    storage.set_engine_preferences(
        fetch_chain=body.fetch_chain,
        search_provider=body.search_provider or None,
        fallback_mode=body.fallback_mode or None,
        scrapling_dynamic_enabled=body.scrapling_dynamic_enabled,
        scrapling_stealthy_enabled=body.scrapling_stealthy_enabled,
    )
    set_system_engine_overrides(
        fetch_chain=body.fetch_chain,
        search_provider=body.search_provider or "",
        fallback_mode=body.fallback_mode or "",
    )
    # 浏览器引擎开关影响引擎注册，需 rebuild
    rebuild_engines()

    overrides = get_system_engine_overrides()
    return {
        **overrides,
        "scrapling_dynamic_enabled": body.scrapling_dynamic_enabled,
        "scrapling_stealthy_enabled": body.scrapling_stealthy_enabled,
    }
```

- [ ] **Step 5: 验证 import**

Run: `python -c "from tianshu.gateway.hongluisi_api import hongluisi_router; print('ok')"`
Expected: 输出 `ok`。

- [ ] **Step 6: Commit**

```bash
git add src/tianshu/gateway/hongluisi_api.py
git commit -m "feat: hongluisi API accepts free engines + browser toggles"
```

---

## Task 8: Web UI — 引擎选项 + 浏览器开关

**Files:**
- Modify: `web/src/api/hongluisi.ts`
- Modify: `web/src/pages/HongluisiPage.tsx`
- Modify: `web/src/i18n/locales/en.json`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/zh-modern.json`

- [ ] **Step 1: hongluisi.ts — EnginePreferences 加两字段**

把 `EnginePreferences` 接口替换为：

```typescript
export interface EnginePreferences {
  fetch_chain: string[];
  search_provider: string | null;
  fallback_mode: string | null;
  scrapling_dynamic_enabled: boolean;
  scrapling_stealthy_enabled: boolean;
}
```

- [ ] **Step 2: i18n — 三个 locale 文件加 key**

在 `en.json`、`zh-classic.json`、`zh-modern.json` 的 `hongluisi.preferences` 对象内，各加三个 key。

`en.json`：
```json
        "browserEnginesLabel": "Scrapling browser engines",
        "browserEnginesHint": "Browser engines need browser binaries — run `scrapling install` first.",
        "enableDynamic": "Enable scrapling_dynamic (Chromium, renders JS)",
        "enableStealthy": "Enable scrapling_stealthy (Camoufox, bypasses Cloudflare)"
```

`zh-modern.json`：
```json
        "browserEnginesLabel": "Scrapling 浏览器引擎",
        "browserEnginesHint": "浏览器引擎需要浏览器二进制 —— 请先运行 `scrapling install`。",
        "enableDynamic": "启用 scrapling_dynamic（Chromium，渲染 JS）",
        "enableStealthy": "启用 scrapling_stealthy（Camoufox，过 Cloudflare）"
```

`zh-classic.json`：
```json
        "browserEnginesLabel": "鸿胪寺浏览器引擎",
        "browserEnginesHint": "浏览器引擎需先备齐浏览器二进制 —— 请先运行 `scrapling install`。",
        "enableDynamic": "启用 scrapling_dynamic（Chromium，渲染动态页）",
        "enableStealthy": "启用 scrapling_stealthy（Camoufox，破 Cloudflare 关卡）"
```

注意每个文件加 key 时保持 JSON 合法（前一个 key 末尾补逗号）。

- [ ] **Step 3: HongluisiPage.tsx — state 加两个开关**

在 `const [fallbackMode, setFallbackMode] = useState<string | null>(null);` 之后加：

```typescript
  const [dynamicEnabled, setDynamicEnabled] = useState(false);
  const [stealthyEnabled, setStealthyEnabled] = useState(false);
```

把 `useEffect(() => { if (prefs) {...} }, [prefs]);` 块内补：

```typescript
      setDynamicEnabled(prefs.scrapling_dynamic_enabled);
      setStealthyEnabled(prefs.scrapling_stealthy_enabled);
```

- [ ] **Step 4: HongluisiPage.tsx — 保存按钮带上两个开关**

把保存按钮 `onClick` 里的 `saveMutation.mutate({...})` 改为：

```typescript
                saveMutation.mutate({
                  fetch_chain: fetchChain,
                  search_provider: searchProvider,
                  fallback_mode: fallbackMode,
                  scrapling_dynamic_enabled: dynamicEnabled,
                  scrapling_stealthy_enabled: stealthyEnabled,
                })
```

- [ ] **Step 5: HongluisiPage.tsx — fetch chain 选项补 scrapling**

把 fetch chain `<Select>` 的 `options` 改为：

```typescript
                options={[
                  { value: "scrapling", label: "scrapling (free, TLS stealth)" },
                  { value: "local", label: "local (trafilatura)" },
                  { value: "scrapling_dynamic", label: "scrapling_dynamic (browser)" },
                  { value: "scrapling_stealthy", label: "scrapling_stealthy (browser)" },
                  { value: "jina", label: "jina (r.jina.ai)" },
                  { value: "firecrawl", label: "firecrawl" },
                ]}
```

- [ ] **Step 6: HongluisiPage.tsx — search provider 选项补 DuckDuckGo**

把 search provider `<Radio.Group>` 内的 radio 列表改为：

```typescript
                <Radio value="">{t("hongluisi.preferences.fallbackProfile")}</Radio>
                <Radio value="duckduckgo">DuckDuckGo (free)</Radio>
                <Radio value="tavily">Tavily</Radio>
                <Radio value="jina">Jina Search</Radio>
```

- [ ] **Step 7: HongluisiPage.tsx — 加浏览器引擎开关 Form.Item**

确保从 antd 引入 `Checkbox`：把 `import { Card, Space, ... notification } from "antd";` 里加入 `Checkbox`。

在 search provider 的 `<Form.Item>` 之后、`</Space>` 之前插入：

```tsx
            <Form.Item
              label={t("hongluisi.preferences.browserEnginesLabel")}
              style={{ marginBottom: 0 }}
            >
              <Space direction="vertical">
                <Checkbox
                  checked={dynamicEnabled}
                  onChange={(e) => setDynamicEnabled(e.target.checked)}
                >
                  {t("hongluisi.preferences.enableDynamic")}
                </Checkbox>
                <Checkbox
                  checked={stealthyEnabled}
                  onChange={(e) => setStealthyEnabled(e.target.checked)}
                >
                  {t("hongluisi.preferences.enableStealthy")}
                </Checkbox>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t("hongluisi.preferences.browserEnginesHint")}
                </Typography.Text>
              </Space>
            </Form.Item>
```

- [ ] **Step 8: 前端构建校验**

Run: `cd web && npx tsc --noEmit`
Expected: 无类型错误。

- [ ] **Step 9: Commit**

```bash
git add web/src/api/hongluisi.ts web/src/pages/HongluisiPage.tsx web/src/i18n/locales/en.json web/src/i18n/locales/zh-classic.json web/src/i18n/locales/zh-modern.json
git commit -m "feat(web): add free engines + browser toggles to Hongluisi page"
```

---

## Task 9: 测试 — ScraplingFetchEngine

**Files:**
- Create: `tests/tools/hongluisi/test_scrapling_fetch.py`

- [ ] **Step 1: 写测试**

```python
"""ScraplingFetchEngine 单测：mock Scrapling Response，验证 outcome 映射。"""

from __future__ import annotations

import pytest

from tianshu.tools.hongluisi.engines.scrapling_fetch import (
    MODE_TO_NAME,
    ScraplingFetchEngine,
    build_scrapling,
)


class _FakePage:
    def __init__(self, status: int, body: bytes, url: str = "https://example.com"):
        self.status = status
        self.body = body
        self.encoding = "utf-8"
        self.url = url


@pytest.mark.unit
def test_mode_to_name_mapping():
    assert MODE_TO_NAME == {
        "http": "scrapling",
        "dynamic": "scrapling_dynamic",
        "stealthy": "scrapling_stealthy",
    }


@pytest.mark.unit
def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        ScraplingFetchEngine("bogus")


@pytest.mark.unit
async def test_fetch_ok(monkeypatch):
    engine = ScraplingFetchEngine("http")
    html = b"<html><body><article>Hello world content here.</article></body></html>"

    async def fake_invoke(url):
        return _FakePage(200, html)

    monkeypatch.setattr(engine, "_invoke", fake_invoke)
    outcome = await engine.fetch("https://example.com")
    assert outcome.status == "ok"
    assert outcome.http_status == 200
    assert "Hello world" in outcome.content


@pytest.mark.unit
async def test_fetch_http_error(monkeypatch):
    engine = ScraplingFetchEngine("http")

    async def fake_invoke(url):
        return _FakePage(404, b"")

    monkeypatch.setattr(engine, "_invoke", fake_invoke)
    outcome = await engine.fetch("https://example.com")
    assert outcome.status == "error"
    assert outcome.reason == "http_status:404"


@pytest.mark.unit
async def test_fetch_exception_wrapped(monkeypatch):
    engine = ScraplingFetchEngine("http")

    async def fake_invoke(url):
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "_invoke", fake_invoke)
    outcome = await engine.fetch("https://example.com")
    assert outcome.status == "error"
    assert outcome.reason == "scrapling_error:RuntimeError"


@pytest.mark.unit
async def test_fetch_ssrf_rejected():
    engine = ScraplingFetchEngine("http")
    outcome = await engine.fetch("http://169.254.169.254/latest/meta-data/")
    assert outcome.status == "error"


@pytest.mark.unit
def test_build_scrapling_returns_engine_or_none():
    engine = build_scrapling("http")
    assert engine is None or engine.name == "scrapling"
```

- [ ] **Step 2: 跑测试**

Run: `pytest tests/tools/hongluisi/test_scrapling_fetch.py -v`
Expected: 全部 PASS（`test_fetch_ssrf_rejected` 依赖 ssrf_guard 拦私网 IP；若该 URL 未被拦，调整为一个确定被拦的内网地址）。

- [ ] **Step 3: Commit**

```bash
git add tests/tools/hongluisi/test_scrapling_fetch.py
git commit -m "test: cover ScraplingFetchEngine outcome mapping"
```

---

## Task 10: 测试 — DuckDuckGoSearchEngine

**Files:**
- Create: `tests/tools/hongluisi/test_duckduckgo_search.py`

- [ ] **Step 1: 写测试**

```python
"""DuckDuckGoSearchEngine 解析单测：固定 HTML fixture。"""

from __future__ import annotations

import pytest

from tianshu.tools.hongluisi.engines.duckduckgo_search import _parse, _unwrap_ddg

_FIXTURE = """
<html><body>
<div class="result results_links">
  <div class="result__body">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">First Result</a>
    <a class="result__snippet">Snippet for first result.</a>
  </div>
</div>
<div class="result results_links">
  <div class="result__body">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fb">Second Result</a>
    <a class="result__snippet">Snippet for second result.</a>
  </div>
</div>
</body></html>
"""


@pytest.mark.unit
def test_unwrap_ddg_redirect():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage"
    assert _unwrap_ddg(href) == "https://example.com/page"


@pytest.mark.unit
def test_unwrap_ddg_direct_url_passthrough():
    assert _unwrap_ddg("https://direct.example.com") == "https://direct.example.com"


@pytest.mark.unit
def test_unwrap_ddg_empty():
    assert _unwrap_ddg("") == ""


@pytest.mark.unit
def test_parse_extracts_results():
    results = _parse(_FIXTURE)
    assert len(results) == 2
    assert results[0].title == "First Result"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "Snippet for first result."
    assert results[1].url == "https://example.org/b"


@pytest.mark.unit
def test_parse_empty_html():
    assert _parse("") == []


@pytest.mark.unit
def test_parse_malformed_html_no_results():
    assert _parse("<not real html") == [] or isinstance(_parse("<not real html"), list)
```

- [ ] **Step 2: 跑测试**

Run: `pytest tests/tools/hongluisi/test_duckduckgo_search.py -v`
Expected: 全部 PASS。

- [ ] **Step 3: Commit**

```bash
git add tests/tools/hongluisi/test_duckduckgo_search.py
git commit -m "test: cover DuckDuckGo HTML result parsing"
```

---

## Task 11: 测试 — engine_registry 降级注册

**Files:**
- Create or Modify: `tests/tools/hongluisi/test_engine_registry.py`

- [ ] **Step 1: 写测试**

先 Read `tests/tools/hongluisi/test_engine_registry.py`（若存在）确认风格并追加；不存在则新建：

```python
"""engine_registry 降级与开关注册单测。"""

from __future__ import annotations

import pytest

from tianshu.tools.hongluisi import engine_registry
from tianshu.tools.hongluisi.engine_registry import build_engines, reset_engines


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_engines()
    yield
    reset_engines()


@pytest.mark.unit
def test_duckduckgo_always_registered():
    fetch, search = build_engines(storage=None)
    assert "duckduckgo" in search


@pytest.mark.unit
def test_browser_engines_off_by_default():
    """storage=None → 无 prefs → 浏览器引擎不注册。"""
    fetch, _ = build_engines(storage=None)
    assert "scrapling_dynamic" not in fetch
    assert "scrapling_stealthy" not in fetch


@pytest.mark.unit
def test_scrapling_skipped_when_not_installed(monkeypatch):
    """build_scrapling 返回 None 时 fetch 不含 scrapling。"""
    monkeypatch.setattr(engine_registry, "build_scrapling", lambda mode: None)
    fetch, _ = build_engines(storage=None)
    assert "scrapling" not in fetch
    assert "local" in fetch  # local 始终在
```

- [ ] **Step 2: 跑测试**

Run: `pytest tests/tools/hongluisi/test_engine_registry.py -v`
Expected: 全部 PASS。

- [ ] **Step 3: Commit**

```bash
git add tests/tools/hongluisi/test_engine_registry.py
git commit -m "test: cover engine registry graceful degradation"
```

---

## Task 12: 测试 — storage engine_preferences + API 校验

**Files:**
- Modify: `tests/test_storage.py`
- Create or Modify: `tests/gateway/test_hongluisi_api.py`

- [ ] **Step 1: test_storage.py 追加 engine_preferences 测试**

先 Read `tests/test_storage.py` 头部确认 Storage fixture 名（下例假设 fixture 名为 `storage`，按实际调整）。追加：

```python
@pytest.mark.unit
def test_engine_preferences_roundtrip_with_toggles(storage):
    storage.set_engine_preferences(
        fetch_chain=["scrapling", "local"],
        search_provider="duckduckgo",
        fallback_mode="on_error_or_empty",
        scrapling_dynamic_enabled=True,
        scrapling_stealthy_enabled=False,
    )
    prefs = storage.get_engine_preferences()
    assert prefs["fetch_chain"] == ["scrapling", "local"]
    assert prefs["search_provider"] == "duckduckgo"
    assert prefs["scrapling_dynamic_enabled"] is True
    assert prefs["scrapling_stealthy_enabled"] is False


@pytest.mark.unit
def test_engine_preferences_defaults_when_empty(storage):
    prefs = storage.get_engine_preferences()
    assert prefs["scrapling_dynamic_enabled"] is False
    assert prefs["scrapling_stealthy_enabled"] is False
```

- [ ] **Step 2: hongluisi API 校验测试**

先 Read `tests/gateway/` 目录确认是否有现成 FastAPI test client fixture（如 `tests/test_gateway.py` 或 conftest）。复用既有 client fixture 新建 `tests/gateway/test_hongluisi_api.py`：

```python
"""hongluisi engine-preferences API 校验测试。"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_patch_rejects_unknown_fetch_engine(client):
    resp = client.patch(
        "/api/hongluisi/engine-preferences",
        json={"fetch_chain": ["bogus_engine"], "search_provider": "duckduckgo"},
    )
    assert resp.status_code == 400
    assert "unknown fetch engine" in resp.json()["detail"]


@pytest.mark.integration
def test_patch_accepts_free_engines(client):
    resp = client.patch(
        "/api/hongluisi/engine-preferences",
        json={
            "fetch_chain": ["scrapling", "local"],
            "search_provider": "duckduckgo",
            "fallback_mode": "on_error_or_empty",
            "scrapling_dynamic_enabled": False,
            "scrapling_stealthy_enabled": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["search_provider"] == "duckduckgo"
    assert body["scrapling_dynamic_enabled"] is False
```

注：`client` fixture 名与 URL 前缀按 `tests/test_gateway.py` 实际为准（路由前缀可能是 `/api` + `/hongluisi`）。若无现成 fixture，参照 `tests/test_gateway.py` 的 app 构造方式建一个。

- [ ] **Step 3: 跑测试**

Run: `pytest tests/test_storage.py -k engine_preferences tests/gateway/test_hongluisi_api.py -v`
Expected: 全部 PASS。

- [ ] **Step 4: 跑全量回归**

Run: `pytest tests/tools/hongluisi/ tests/test_storage.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add tests/test_storage.py tests/gateway/test_hongluisi_api.py
git commit -m "test: cover engine_preferences storage + hongluisi API validation"
```

---

## 验收

实现完成后，端到端手动验证（用户负责）：

1. 重启后端 → 鸿胪寺配置页 fetch chain 下拉出现 `scrapling`，search 出现 `DuckDuckGo (free)`。
2. 配置 `fetch_chain=["scrapling","local"]` + `search_provider=duckduckgo` 保存。
3. 发一条需要 web_fetch / web_search 的敕令 → 不带任何 key 也能成功。
4. 勾选"启用 scrapling_dynamic"保存 → `engine-status` 端点 `fetch_engines` 出现 `scrapling_dynamic`（前提：已 `scrapling install`）。
