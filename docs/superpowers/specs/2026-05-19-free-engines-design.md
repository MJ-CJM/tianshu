# 免费引擎接入设计：Scrapling 抓取 + DuckDuckGo 搜索

> 日期：2026-05-19
> 状态：设计已批准，待写实现计划

## 背景与动机

天枢的鸿胪寺（网络工具层）目前的 `web_fetch` / `web_search` 全部依赖付费第三方
服务，且无可用的免费档：

- **fetch 引擎**：`local`（httpx + trafilatura，免费但易被反爬拦截、不渲染 JS）、
  `jina`（r.jina.ai，有免费档但代码强制带 key）、`firecrawl`（付费）。
- **search 引擎**：`tavily`（付费）、`jina`（s.jina.ai，无 key 直接 401，必须付费 key）。

线上"每日天气推送"定时任务连续多日失败，根因排查结论：

1. 系统级引擎 override（`engine_preferences` 表）把 fetch 和 search **都钉死到 `jina`**。
2. 唯一的网络凭证 `jina-test` 是一个**余额耗尽**的 Jina key。
3. `JinaReaderEngine` 在有 key 时强制带 `Authorization` 头 → r.jina.ai 对欠费账户返回
   **HTTP 402 Payment Required**；s.jina.ai 同理 → `RuntimeError`。
4. agent 在迭代里反复重试失败工具 → 撑满 300s 超时墙。

**目标**：引入零成本、无 key 的免费引擎选项，让系统开箱即可用；付费引擎
（jina / tavily / firecrawl）原样保留，由用户在鸿胪寺配置页自行选择。

## 范围

**纳入**：

- 新增免费 fetch 引擎，参考 [Scrapling](https://github.com/D4Vinci/Scrapling)。
- 新增免费 search 引擎（爬 DuckDuckGo HTML 结果页）。
- 浏览器类引擎的启停开关 + 持久化。
- 鸿胪寺配置页（`HongluisiPage`）扩展为可选新引擎。
- 默认配置改为全免费组合。

**不纳入**：

- 不新增"免费/付费 tier"抽象（YAGNI，现有配置 UI 已满足"用户自选"）。
- 不删除现有 jina / tavily / firecrawl 付费引擎。
- 不新增独立的 `weather_query` 专用工具（用户明确排除）。

## 架构总览

现有引擎架构已分层清晰，本设计为**纯增量接入**，不改动核心：

- `FetchEngine` / `SearchEngine` 两个 Protocol（`engines/__init__.py`）。
- `engine_registry._do_build()` 按可用性构造所有引擎。
- `FetchRouter` 按 fetch 链 + fallback 分发。
- `engine_preferences` 表 + `policy_profile` 系统级 override 缓存。
- 鸿胪寺 API（`hongluisi_api.py`）+ 前端 `HongluisiPage`。

新引擎只需实现既有 Protocol 并在 registry 注册，沿用全部既有分发、限流、SSRF、
审计逻辑。

## 组件设计

### 1. Scrapling fetch 引擎

新文件 `src/tianshu/tools/hongluisi/engines/scrapling_fetch.py`。

单个 `ScraplingFetchEngine` 类，构造参数 `mode ∈ {"http", "dynamic", "stealthy"}`，
对应 3 个注册名：

| 注册名 | mode | Scrapling 类 | 依赖 | 默认 |
|--------|------|-------------|------|------|
| `scrapling` | http | `Fetcher`（TLS 指纹伪装 HTTP） | 纯 pip | 开启 |
| `scrapling_dynamic` | dynamic | `DynamicFetcher`（Playwright Chromium，渲染 JS） | 浏览器二进制 | 关闭 |
| `scrapling_stealthy` | stealthy | `StealthyFetcher`（Camoufox，过 Cloudflare） | 浏览器二进制 | 关闭 |

行为约定：

- 统一走 Scrapling 的 async 变体（`AsyncFetcherSession` / `AsyncDynamicSession` /
  `AsyncStealthySession`），避免阻塞事件循环。
- 抓取前对目标 URL 做 SSRF 校验（复用 `ssrf_guard.validate_url`）—— Scrapling 不是
  安全边界。
- HTML 内容复用 `markdown_extract.extract_markdown` 转 Markdown；
  `is_empty` 判定空内容。
- 输出统一为既有 `FetchOutcome`（`content` / `status` / `http_status` /
  `reason` / `bytes_fetched` / `final_url` / `cached`）。
- 异常包成 `FetchOutcome(status="error", reason="scrapling_error:<ExcType>")`，
  让 `FetchRouter` 照常 fallback。

### 2. DuckDuckGo search 引擎

新文件 `src/tianshu/tools/hongluisi/engines/duckduckgo_search.py`。

`DuckDuckGoSearchEngine` 类，注册名 `duckduckgo`：

- 请求 `https://html.duckduckgo.com/html/?q=<urlencoded query>`（服务端渲染，
  无需 key），用现有 `SharedHttpClient`。
- 用 lxml 解析结果块：标题/链接 `.result__a`、摘要 `.result__snippet`；
  DuckDuckGo 的跳转链接需还原真实 URL（解析 `uddg` query 参数）。
- 转既有 `SearchResult`（`title` / `url` / `snippet` / `score=None`），
  截断到 `max_results`。
- 解析为空 → `SearchOutcome(results=())` → 上层 `web_search` 返回 `search_empty`。
- 无 key、无新依赖。

### 3. 引擎注册与可用性

`engine_registry._do_build()` 扩展：

- 新增 `build_scrapling(mode, store)` —— 捕获 `ImportError`（Scrapling 未安装）
  返回 `None` → 引擎不注册。沿用现有"无 key 即不注册"的优雅降级模式。
- 浏览器引擎（`scrapling_dynamic` / `scrapling_stealthy`）额外受开关控制：
  开关关闭 → 不注册。
- 新增 `build_duckduckgo()` —— 无条件可注册（无依赖、无 key）。

### 4. 浏览器引擎开关与持久化

`engine_preferences` 表新增两列：

```
scrapling_dynamic_enabled  INTEGER NOT NULL DEFAULT 0
scrapling_stealthy_enabled INTEGER NOT NULL DEFAULT 0
```

- `storage.get_engine_preferences()` / `set_engine_preferences()` 读写新列。
- 走 storage 既有的 schema migration 机制（`ALTER TABLE ... ADD COLUMN`）。
- 开关变更后调用 `rebuild_engines()` 热更，无需重启。

### 5. Scrapling 依赖

- 在 `pyproject.toml` 加可选依赖组
  `[project.optional-dependencies] scrapling = ["scrapling"]`，版本在实现阶段
  按当时最新稳定版固定下限。
- 浏览器二进制（Playwright Chromium / Camoufox）不随 pip 安装，需用户手动
  `scrapling install`；UI 上对浏览器引擎给出该提示。

### 6. 配置入口

**后端** `hongluisi_api.py`：

- `ALLOWED_FETCH` 增加 `scrapling` / `scrapling_dynamic` / `scrapling_stealthy`。
- `ALLOWED_SEARCH` 增加 `duckduckgo`。
- `EnginePreferencesPayload` 与 PATCH 增加 `scrapling_dynamic_enabled` /
  `scrapling_stealthy_enabled` 两个布尔字段。
- PATCH 校验：若 fetch_chain 选了未注册引擎（如浏览器引擎未开启或二进制缺失），
  返回明确 400。

**前端** `web/src/pages/HongluisiPage.tsx` + `web/src/api/hongluisi.ts`：

- `EnginePreferences` 接口加两个布尔字段。
- fetch_chain 多选、search_provider 下拉补全新选项。
- 浏览器引擎加启停开关，并显示"需运行 `scrapling install`"提示。
- `engine-status` 端点已返回已注册引擎列表，UI 自动反映可用性。

### 7. 默认值调整

新装/默认配置（无任何 key 即可用）：

- `fetch_chain = ["scrapling", "local"]`
- `search_provider = "duckduckgo"`
- `fallback_mode = "on_error_or_empty"`

迁移：把现有 DB 里 `["jina"]` / `jina` 的 override 纠正为上述默认。在 storage
的 schema migration 步骤里一并处理（与新增两列同一次 migration）。

## 数据流

**web_fetch**：`web_fetch` handler → `_resolve_edict_context` 取 NetworkPolicy →
`FetchRouter`（链含 `scrapling`）→ `ScraplingFetchEngine.fetch(url)` →
SSRF 校验 → Scrapling async 抓取 → markdown 提取 → `FetchOutcome` →
ok 即返回，error/empty 按 `fallback_mode` 走链上下一个（如 `local`）。

**web_search**：`web_search` handler → `get_search_providers_map()` →
`DuckDuckGoSearchEngine.search(query, max_results)` → 请求 DuckDuckGo HTML →
lxml 解析 → `SearchOutcome` → 格式化为 Markdown 结果列表。

## 错误处理

- Scrapling 抓取异常 → `FetchOutcome(status="error", reason="scrapling_error:<Type>")`，
  `FetchRouter` fallback 到链上下一引擎。
- DuckDuckGo 解析空 → `SearchOutcome(results=())` → `web_search` 返回 `search_empty`。
- Scrapling 未安装 → `build_scrapling` 返回 None → 引擎不注册；UI engine-status 显示
  不可用。
- 浏览器引擎被选用但开关关闭或二进制缺失 → 注册时跳过 + 日志；PATCH 校验时
  给明确 400。

## 测试策略

遵循项目"功能优先、测试最后补"约定，测试在实现收尾统一补齐：

- **单测**：
  - `ScraplingFetchEngine` 三个 mode（mock Scrapling 类），含异常包装、SSRF 拒绝。
  - `DuckDuckGoSearchEngine` 解析（固定 HTML fixture，含 uddg 链接还原、空结果）。
  - `engine_registry` 在 Scrapling 缺失 / 浏览器开关关闭时的降级注册。
  - `hongluisi_api` PATCH 白名单校验（含未注册引擎 400）。
  - `storage` engine_preferences 新列读写 + migration。
- **集成**：`FetchRouter` 把 `scrapling` 串进链并验证 fallback 到 `local`。
- 目标覆盖率沿用项目标准（80%+）。

## 风险与权衡

- **DuckDuckGo HTML 结构变更**：解析依赖 HTML class 选择器，DuckDuckGo 改版会
  导致解析失败。缓解：解析失败时 `SearchOutcome` 空而非抛异常；选择器集中在
  单文件便于维护。
- **浏览器引擎运维成本**：Playwright/Camoufox 二进制体积大、需手动安装。缓解：
  默认关闭，纯 pip 的 `scrapling`（http mode）作为默认免费引擎已能覆盖多数场景。
- **DuckDuckGo 速率限制 / IP 封禁**：高频爬取可能被限。缓解：复用现有
  `rate_limiter`（`web_search_rate_per_min`）。
