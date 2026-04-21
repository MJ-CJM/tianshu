# 天枢对外网络通讯能力设计（鸿胪寺）

**日期**: 2026-04-21
**作者**: brainstorming session
**状态**: 设计完成，待 plan
**相关**:
- 上一轮初稿 `docs/superpowers/plans/2026-04-21-web-access-tools.md`（作废，由本 spec 取代）
- 工具策略管道 `docs/superpowers/specs/2026-04-14-tool-policy-pipeline-design.md`

---

## 0. 背景与动机

目前天枢的内置工具只覆盖 workspace 内的文件/命令操作（`src/tianshu/tools/builtins.py`：`shell_exec / read_file / write_file / edit_file / grep / find_files / list_dir`）。Edict 执行过程中，六部官员与用户分身遇到"需要读一份 RFC / MDN 文档 / GitHub README"或"需要调研某主题"时，只能凭模型记忆猜，无法补充新信息。

本 spec 引入**对外网络通讯能力**，覆盖两个场景（scope）：

- **A. 读公开文档**：已知 URL，抓回来提取可读正文（Markdown）
- **B. 研究型任务**：给定 query，在公网搜索相关材料

**不纳入本 spec 的 scope**：
- **C. 交互式浏览器操作**（登录、点击、填表、截图）—— 见下节"关于 vercel-labs/agent-browser"
- 数据导出（POST/PUT 到外部服务）、登录态抓取、订阅/监听

---

## 1. 关于 `vercel-labs/agent-browser` 及同类选型

brainstorming 阶段调研了主流方案，结论如下。

### 1.1 候选对比

| 方案 | 形态 | 输出 | 我们的用途 | 采纳？ |
|------|------|------|-----------|-------|
| [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) | Rust CLI + Chromium（~684 MB）| accessibility tree with refs | 真正的"操作网页"（点击/填表）才值这个重量 | ❌ 不采纳 |
| [Jina Reader](https://jina.ai/reader/) | HTTP API，`r.jina.ai/<url>` 前缀即用 | Markdown | 零设置 fetch，JS 渲染页面 fallback | ✅ 采纳（fallback 档） |
| [Firecrawl](https://www.firecrawl.dev/) | REST API | Markdown / 结构化 | 高质量深度抓取，77% 成功率 vs Tavily 67% | ✅ 采纳（opt-in） |
| [Tavily](https://tavily.com/) | REST API | Search results + optional raw content | 为 agent 优化的搜索，免费 1k credits/月 | ✅ 采纳（search 主） |
| [Jina Search](https://jina.ai/reader/#apiform) | `s.jina.ai/?q=` | 已提取的 Markdown 列表 | 搜索 + 抓取一步到位 | ✅ 采纳（search 备） |
| [Exa](https://exa.ai/) | REST API | 语义搜索结果 | 强在"发现"阶段，场景特殊 | △ 本期不采纳 |

### 1.2 为何不用 agent-browser

- **定位错配**：它解决"浏览器自动化"（让 LLM 像用户一样点击），而我们要的是"读公开资料"。两者差一个数量级的工程重量。
- **二进制负担**：Chromium ~684 MB 与"轻量宫殿"的项目定位冲突。
- **Python 集成不干净**：无 Python 库，只能 subprocess + JSON 解析，多一层故障面。
- **未来扩展路径**：如果后续真要做场景 C，通过 **MCP server** 方式对接 agent-browser 是更干净的路径 —— 保持它在天枢主包外、按需加载、不污染核心依赖。

---

## 2. 目标 & 非目标

### 2.1 目标

- 给六部官员（executor、planner 等）两个工具：`web_fetch(url)` 与 `web_search(query, max_results=5)`。
- 支持三档可配置策略（offline / default / research）+ Edict 级 override。
- LLM **看不到**后端 engine/provider 选择 —— 接口在所有 profile 下完全一致。
- 硬护栏：SSRF 防护、响应大小上限、单 Edict 限流、敏感错误脱敏。
- 三层 fetch 策略（local → Jina Reader → Firecrawl），自动 fallback 可开关。

### 2.2 非目标

- 不引入 Chromium / Playwright / headless 浏览器。
- 不做持久化缓存（只做进程内 TTL LRU）。
- 不支持登录态抓取、POST/PUT、文件上传、长期订阅。
- 不做跨 Edict 共享的速率限制（进程内 per-Edict 令牌桶即可）。

---

## 3. 架构与模块边界

### 3.1 叙事归属：鸿胪寺

新增外朝官署 **鸿胪寺**（hongluisi），专司对外通讯、传信、读远方典籍。所有网络 I/O 必须经此官署；其他部门禁止直接 socket。这给 code 一个清晰的 import 边界：任何 `from tianshu.tools.hongluisi import ...` 之外的网络调用都视为违规。

### 3.2 模块布局

```
src/tianshu/tools/hongluisi/
├── __init__.py                 # 注册 web_fetch / web_search（按 profile）
├── engines/
│   ├── __init__.py             # FetchEngine / SearchEngine Protocol
│   ├── local_fetch.py          # httpx + trafilatura
│   ├── jina_reader.py          # r.jina.ai 代理
│   ├── firecrawl.py            # api.firecrawl.dev/v1/scrape
│   ├── tavily.py               # api.tavily.com/search
│   └── jina_search.py          # s.jina.ai
├── router.py                   # FetchRouter：profile + override → engine chain
├── http_client.py              # 共享 httpx.AsyncClient + TTL 缓存（单例）
├── ssrf_guard.py               # URL 白/黑 IP + scheme + port 校验
├── markdown_extract.py         # trafilatura 包装（仅 local engine 用）
└── rate_limiter.py             # per-(edict_id, tool_name) 令牌桶

src/tianshu/tools/policy_rules/
└── network_safety.py           # 新增：profile/override/SSRF 前置决策
```

### 3.3 Tier 扩展

在 `src/tianshu/tools/types.py` 的 `ToolTier` IntEnum 新增 `T2_NETWORK = 2`：

```python
class ToolTier(IntEnum):
    T0_READONLY = 0
    T1_WORKSPACE = 1
    T2_NETWORK = 2       # 新增：外部读（SSRF 风险，介于 workspace 与 dangerous 间）
    T3_WRITE = 3         # 原 T2_WRITE 改名 / 提升数值
    T4_DANGEROUS = 4     # 原 T3_DANGEROUS 改名 / 提升数值
```

**影响面评估**（`grep -rn 'T2_WRITE\|T3_DANGEROUS' src/` 得出）：`builtins.py` / `edit_file.py` / `registry.py` / `policy_rules/*` / 测试若干。保守替代方案：若扩展影响面过大，可让 `T2_WRITE` 同时涵盖"外部读 + 外部写"，由 `NetworkSafetyRule` 按 `tool_name` 分流。本 spec 推荐扩展方案（语义更清晰），最终在 plan 阶段由 T-0 任务拍板。

### 3.4 配置模型

`NetworkPolicy` 作为现有 `PolicyProfile` 的子字段嵌入：

```python
# src/tianshu/tools/hongluisi/policy.py（新文件）
@dataclass(frozen=True)
class NetworkPolicy:
    fetch_engines: tuple[str, ...] = ("local",)
    fallback_mode: Literal["none", "on_error_or_empty"] = "none"
    search_provider: Literal["tavily", "jina"] | None = None
    max_fallback_depth: int = 3
    web_fetch_rate_per_min: int = 20
    web_search_rate_per_min: int = 10

# src/tianshu/tools/policy_profile.py（扩展现有）
@dataclass(frozen=True)
class PolicyProfile:
    ...
    network: NetworkPolicy = field(default_factory=NetworkPolicy)
```

这样 `ctx.edict.runtime.policy_profile.network` 是唯一访问路径，`NetworkSafetyRule` 不依赖全局 state。

三档预设（写死在 `policy_profile.py`）：

| Profile | fetch_engines | fallback_mode | search_provider |
|---------|--------------|---------------|-----------------|
| `offline` | `("local",)` | `"none"` | `None`（工具不注册） |
| `default` | `("local", "jina")` | `"on_error_or_empty"` | `"tavily"` |
| `research` | `("local", "jina", "firecrawl")` | `"on_error_or_empty"` | `"tavily"` |

**Edict 级 override**（任一 profile 上叠加）：

```python
@dataclass(frozen=True)
class RuntimeConfig:
    policy_profile: PolicyProfile
    fetch_engine_override: str | None = None       # 钉死用某 engine
    search_provider_override: str | None = None    # 钉死用某 provider
```

**override 语义**：存在时强制 `fallback_mode="none"`（手动钉死意味着用户不希望偷偷 fallback）。

### 3.5 env（启动期存在性）

```
TIANSHU_JINA_API_KEY        # 可选：无 key 仍可调 r.jina.ai（20 req/min）；有 key 500/min
TIANSHU_FIRECRAWL_API_KEY   # 无 key → firecrawl engine 不注册
TIANSHU_TAVILY_API_KEY      # 无 key → web_search 若依赖 tavily 则不注册
```

三层控制的层级关系：**env 存在性 → profile 可见性 → override 精确选择**。

---

## 4. 工具接口（LLM 视角）

### 4.1 `web_fetch`

```python
ToolDefinition(
    name="web_fetch",
    description=(
        "Fetch a public web page and return its readable text as Markdown. "
        "Only public URLs are allowed; internal/private IPs are rejected. "
        "Response bodies larger than 1 MB are refused. Output is truncated to 16000 chars."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL"},
        },
        "required": ["url"],
    },
    tier=ToolTier.T2_NETWORK.value,
    max_result_chars=16000,
)
```

**不暴露** `engine` 参数；engine 由 profile + override 在后端路由。

### 4.2 `web_search`

```python
ToolDefinition(
    name="web_search",
    description=(
        "Search the public web. Returns a ranked list of results "
        "(title / url / snippet). Use web_fetch afterwards to read full content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["query"],
    },
    tier=ToolTier.T2_NETWORK.value,
    max_result_chars=4000,
)
```

**不暴露** `provider` 参数；provider 由 profile + override 在后端路由。

---

## 5. 关键组件

### 5.1 `ssrf_guard.py`

```python
async def validate_url(url: str) -> None:
    """raises SSRFViolation(code, internal_reason) on any failure."""
```

**顺序检查**（失败即抛，`code` 用于 LLM 可见的脱敏常量，`internal_reason` 记审计日志）：

1. `urllib.parse.urlparse`；`scheme ∈ {http, https}`；`host` 非空
2. 端口白名单 `{None, 80, 443, 8080, 8443}`
3. hostname 字面黑名单：`localhost`、以 `.local` / `.internal` / `.corp` 结尾
4. `socket.getaddrinfo(host, None)` 拿所有 A/AAAA；**逐一**校验：
   - `ipaddress.ip_address(ip).is_private/is_loopback/is_link_local/is_multicast/is_reserved/is_unspecified` → 拒
   - 显式拒 `169.254.169.254`（AWS metadata）、`100.64.0.0/10`（CGNAT）、`fd00::/8`（IPv6 ULA）
5. 用户信息（`https://user:pass@host`）→ 剥离后重校验

**调用点**（纵深防御，三层都要）：
- `NetworkSafetyRule` 作为 policy 前置
- 每个 engine 的 `fetch()` 入口（即使 policy 被绕过）
- `http_client` 的 redirect event hook（每跳重新校验 Location）

### 5.2 `http_client.py`

```python
class SharedHttpClient:
    """进程单例。跨 Edict 共享连接池、DNS 缓存、TTL 响应缓存。"""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=True,
            max_redirects=5,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            event_hooks={"response": [self._on_redirect]},
        )
        self._cache: TTLCache = TTLCache(maxsize=32, ttl=300)

    async def _on_redirect(self, r: httpx.Response) -> None:
        if r.is_redirect and "location" in r.headers:
            await validate_url(r.headers["location"])
```

- **缓存 key** = `(url, engine_name)`，跨 engine 不共享
- **响应体上限** 1 MB：先看 `Content-Length`，无则 streaming 累积校验
- **异常隔离**：每 engine 调用包在 `async with client.stream()` 里，异常主动 close；池异常时重建 client，保留缓存

### 5.3 Engine Protocol 与实现

```python
class FetchEngine(Protocol):
    name: str
    async def fetch(self, url: str) -> FetchOutcome: ...

@dataclass(frozen=True)
class FetchOutcome:
    content: str                  # Markdown；失败时可空
    status: Literal["ok", "empty", "error"]
    http_status: int | None
    reason: str | None            # router 判断用
    bytes_fetched: int
    final_url: str | None
```

**注意**：Router 用 `FetchOutcome` 而非直接 `ToolResult`，因为中间 fallback 过程需要保留更多信息（reason / http_status / final_url），到最终才组装 ToolResult。

**engine 实现要点**

| Engine | 要点 |
|--------|------|
| `local_fetch` | `Accept: text/html,text/plain,application/json,text/markdown`；非预期 content-type → error；`markdown_extract.extract(html)` 后 <500 字符 → empty |
| `jina_reader` | 先 `validate_url(target)`；构造 `https://r.jina.ai/<url>`；env 有 key 加 `Authorization: Bearer` |
| `firecrawl` | 先 `validate_url(target)`；`POST /v1/scrape`，body `{url, formats:["markdown"], onlyMainContent:true}`；无 key 时启动期不注册 |
| `tavily` | `POST /search`，body `{query, max_results, search_depth:"basic"}`；格式化 `results[*]` 为编号 Markdown 列表；截 4000 字符 |
| `jina_search` | `GET s.jina.ai/?q=<query>`；Jina 已返回格式化 Markdown；成本高故默认不选；截 4000 字符 |

### 5.4 `router.py` — Fetch 路由决策

```python
class FetchRouter:
    def __init__(
        self,
        engines: dict[str, FetchEngine],
        policy: NetworkPolicy,
        override: str | None,
    ) -> None:
        self._engines = engines
        if override is not None:
            self._chain, self._fallback_mode = (override,), "none"
        else:
            self._chain, self._fallback_mode = policy.fetch_engines, policy.fallback_mode
        self._max_depth = policy.max_fallback_depth

    async def dispatch(self, url: str) -> tuple[FetchOutcome, list[FetchAttempt]]:
        attempts: list[FetchAttempt] = []
        outcome: FetchOutcome | None = None
        for engine_name in self._chain[: self._max_depth]:
            engine = self._engines.get(engine_name)
            if engine is None:
                attempts.append(FetchAttempt(engine_name, "skipped", "not registered"))
                continue
            try:
                outcome = await engine.fetch(url)
            except Exception as e:
                outcome = FetchOutcome(
                    content="", status="error", http_status=None,
                    reason=f"{type(e).__name__}: {e}",
                    bytes_fetched=0, final_url=None,
                )
            attempts.append(FetchAttempt(engine_name, outcome.status, outcome.reason))
            if outcome.status == "ok":
                return outcome, attempts
            if self._fallback_mode == "none":
                return outcome, attempts
            # on_error_or_empty：empty 和 error 都继续 fallback
        assert outcome is not None, "empty fetch_engines chain"
        return outcome, attempts
```

### 5.5 `NetworkSafetyRule` — policy 层

```python
@dataclass
class NetworkSafetyRule:
    rule_id: str = "network_safety"
    priority: int = 75

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        if ctx.tool_name not in {"web_fetch", "web_search"}:
            return None

        net = ctx.edict.runtime.policy_profile.network
        runtime = ctx.edict.runtime

        # 1. offline / 未配置 provider → deny
        if ctx.tool_name == "web_search" and net.search_provider is None:
            return PolicyDecision("deny", self.rule_id, "search disabled for this profile")

        # 2. override 引用未注册的 engine / provider → deny
        if ctx.tool_name == "web_fetch":
            override = runtime.fetch_engine_override
            if override and override not in get_registered_fetch_engines():
                return PolicyDecision("deny", self.rule_id, f"fetch engine '{override}' not available (missing env key?)")
        else:  # web_search
            override = runtime.search_provider_override
            if override and override not in get_registered_search_providers():
                return PolicyDecision("deny", self.rule_id, f"search provider '{override}' not available (missing env key?)")

        # 3. web_fetch：前置 SSRF 校验
        if ctx.tool_name == "web_fetch":
            try:
                await validate_url(ctx.args["url"])
            except SSRFViolation as v:
                return PolicyDecision("deny", self.rule_id, v.code)  # 脱敏 code

        return PolicyDecision("allow", self.rule_id, "passed")
```

### 5.6 `rate_limiter.py`

- 令牌桶 per-`(edict_id, tool_name)`
- `web_fetch` 20 / min、`web_search` 10 / min
- 桶空返回 `error_result("rate limit, retry after {s}s")`
- **挂在 tool function 入口**（policy 之后、router 之前）：
  - 放 policy 之后：被 deny 的调用不计入限流
  - 放 router 之前：一次 fetch 穿 3 层 engine 只消耗 1 token

---

## 6. 数据流

### 6.1 `web_fetch` 完整链路

```
LLM tool_call web_fetch(url)
  │
  ▼ ToolRegistry.execute
  │  ├─ jsonschema 校验
  │  └─ tier=T2_NETWORK → hook chain
  ▼ PolicyEngine
  │  └─ NetworkSafetyRule → ssrf_guard.validate_url(url)   # 第一次 SSRF
  ▼ rate_limiter.check(edict_id, "web_fetch")
  ▼ FetchRouter.dispatch(url)
  │  └─ for engine in chain[:max_depth]:
  │       ├─ engine.fetch(url)
  │       │   ├─ ssrf_guard.validate_url(url)               # 第二次（纵深）
  │       │   ├─ SharedHttpClient.get
  │       │   │   └─ redirect hook: validate_url(Location)  # 每跳一次
  │       │   └─ trafilatura.extract(html) (仅 local)
  │       └─ ok → return；empty/error + fallback_mode → 下一个
  ▼ ToolResult(content, details={engine_used, engine_chain, cached, ...})
```

### 6.2 `web_search` 完整链路

```
LLM tool_call web_search(query, max_results)
  │
  ▼ PolicyEngine → NetworkSafetyRule（搜索无 SSRF，但查 provider 可用性）
  ▼ rate_limiter.check
  ▼ SearchEngine.search(query, max_results)   # Tavily 或 Jina
  ▼ 格式化结果为 Markdown 列表，截 4000 字符
  ▼ ToolResult(content, details={provider, result_count, ...})
```

**Search 不缓存**：query 多变，且 provider 自身有搜索缓存。

---

## 7. 错误处理

### 7.1 错误分类

| 类别 | 触发 | LLM 看到 | 审计日志 | LLM 可恢复？ |
|------|------|---------|---------|-------------|
| SSRF | scheme/port/IP 命中黑名单 | `is_error=True, content="URL 被拒"` + 脱敏 code | `verdict=deny, rule=network_safety, internal_reason` | ❌ 换 URL |
| 超限 | rate bucket 空 | `is_error=True, retry_after` | `tool_skipped_rate_limit` | ⏱ 等或换思路 |
| 超大 | body > 1MB | `is_error=True, bytes` | `fetch_rejected_size` | ❌ |
| 超时 | httpx timeout | `is_error=True` | `fetch_timeout` | 🔄 fallback |
| 协议错 | 4xx/5xx | `is_error=True, http_status` | `fetch_http_error` | 🔄 fallback |
| 空内容 | <500 字符 | `status=empty`（router 处理后可能变 ok） | `fetch_empty` | 🔄 fallback |
| 不支持 | content-type 非白名单 | `is_error=True` | `fetch_unsupported_type` | ❌ |
| 没 key | profile 要 firecrawl 但 env 无 key | `is_error=True` + 提示改用 X | `engine_not_registered` | ❌ |
| search 全败 | 唯一 provider 报错 | `is_error=True` | `search_api_error` | 🔄 换 query |
| 全 engine 用尽 | fallback 穿透完仍 error | `is_error=True` + `engine_chain` 历史 | `fetch_exhausted` | ❌ |

### 7.2 敏感信息脱敏

- **SSRF 错误**绝不告诉 LLM 具体 IP / 解析结果；只给脱敏 code（`"ssrf_private_ip"` / `"ssrf_bad_scheme"` / `"ssrf_bad_port"`）
- **API key 错误**永不在 ToolResult 里 dump provider 响应 body（可能 echo header）
- 审计日志内记完整 internal_reason（便于调试，仅限后端可见）

### 7.3 Fallback 用尽时的 ToolResult

```
content = (
  "抓取失败。尝试记录：\n"
  "1. local: error — timeout after 15s\n"
  "2. jina:  error — HTTP 403\n"
  "3. firecrawl: error — HTTP 500\n\n"
  "建议：换一个 URL，或使用 web_search 检索相关内容。"
)
is_error = True
details = {"engine_chain": [...], "original_url": url, "suggest": ["web_search"]}
```

核心原则：给 LLM 足够信息**决策下一步**，而不是只说"失败了"。

### 7.4 启动期错误

- `httpx` / `trafilatura` 导入失败 → 对应 engine 不注册，其他照常
- env 有 key 但真调失败 → 仅启动期 WARNING log，运行时再报错（校验 key 本身消耗 quota，不宜启动期主动验证）

---

## 8. 测试策略

**执行次序**：按项目偏好"功能优先、测试最后补"，测试作为 plan 中最后一个任务 T-10 统一完成。

### 8.1 单元测试

**`test_ssrf_guard.py`**（安全最重要）
- `file://` / `ftp://` / 无 scheme → 拒
- 端口 22 / 25 / 5432 → 拒；80 / 443 → 过
- `localhost` / `foo.internal` → 拒
- DNS 解析到 `127.0.0.1` / `10.0.0.1` / `169.254.169.254` / `::1` → 拒
- 多个 A 记录里任一内网 → 拒（防 DNS round-robin 绕过）
- IPv6 私有地址（`fd00::`）→ 拒
- 公网地址（`8.8.8.8`）→ 过
- 带 userinfo 的 URL → 剥离后重校验

**`test_rate_limiter.py`**
- 令牌桶按 `(edict_id, tool_name)` 隔离
- 每分钟重置
- 桶空 `check()` 返回 False + 剩余秒数
- 两 Edict 互不影响

**`test_markdown_extract.py`**
- 正常 HTML → Markdown
- 纯 JS SPA / 空 body → 返回 ""
- 已 Markdown / plain text → passthrough

**`test_router.py`**
- chain=["local"] + fallback="none" + local ok → 返回
- chain=["local","jina"] + fallback="on_error_or_empty" + local error → 调 jina
- chain=["local","jina"] + override="firecrawl" → 只调 firecrawl
- chain 含未注册 engine → 跳过并记 attempts
- max_fallback_depth=2 + chain 长度 3 → 只尝试前 2
- engine 抛异常 → 包装成 FetchOutcome(error) 继续

### 8.2 集成测试（`test_web_access_integration.py`）

- Policy 层：SSRF URL 被拦，engine 不被调用
- Policy 层：offline profile 下 web_search deny
- Policy 层：override 未注册 engine → deny + 提示
- 限流：连续 21 次 web_fetch 第 21 次返回 rate_limit
- Fallback：mock local 永远 empty、jina 永远 ok → engine_chain=["local","jina"]
- Fallback 全败：3 engine 都 error → `is_error=True` + content 含尝试历史
- 缓存：同 URL 两次调用第二次 `details.cached=true`
- 缓存 key 带 engine：同 URL 不同 engine 各走一次

### 8.3 契约测试（`pytest-vcr`，cassette 提交 repo）

- `local_fetch` vs `https://example.com`
- `jina_reader` vs `https://r.jina.ai/https://example.com`
- `firecrawl` vs `api.firecrawl.dev/v1/scrape`（需 key，无则 skip）
- `tavily` + `jina_search`（需 key，无则 skip）

契约测试打 marker `@pytest.mark.contract`；本地 `pytest -m "not contract"` 默认不跑；CI 单独 stage。

### 8.4 手工验证清单（给你 MVP 完工后自测）

- [ ] `offline` profile Edict → LLM 看不到 `web_search`
- [ ] `default` profile → `web_fetch("https://example.com")` 在 AuditDashboard 可见，`engine_chain=["local"]`
- [ ] `web_fetch("http://169.254.169.254/")` → `is_error=True`，reason 脱敏
- [ ] `research` profile + Edict `fetch_engine_override="firecrawl"` → `engine_used="firecrawl"`
- [ ] 连续 21 次 web_fetch → 第 21 次 rate limit
- [ ] 1.5 MB 文件 URL → 被 size limit 拦

### 8.5 不做的测试

- trafilatura 提取质量回归（那是 trafilatura 的职责）
- Jina/Firecrawl/Tavily API 功能性测试（供应商职责，只测契约）
- 性能 benchmark（MVP 不优化 latency）

---

## 9. 观测与审计

### 9.1 ToolResult.details 必含字段

**web_fetch**:
- `url` / `final_url`
- `engine_used` / `engine_chain: [{engine, status, reason}, ...]`
- `http_status` / `bytes_fetched` / `content_type`
- `cached: bool` / `truncated: bool`

**web_search**:
- `provider` / `query` / `max_results_requested`
- `result_count` / `api_usage`（provider 返回的 usage 元数据）

### 9.2 前端（AuditDashboard）

`ToolResult.content` 展示已在上一轮迭代中支持折叠，本 spec 无需新增前端工作。建议未来在 edict 详情页加一个"网络使用量"统计条，展示本 Edict 已消耗的 web_fetch/web_search 次数 / 字节数（非本 spec scope）。

---

## 10. 风险 & 缓解

| 风险 | 缓解 |
|------|------|
| SSRF 漏抓（DNS rebinding） | redirect hook 每跳重校验；engine 层独立再校验 |
| API key 泄漏 | 只从 env 读；绝不写入 log / ToolResult.details |
| trafilatura 对某些站点提取失败 | fallback 到 Jina Reader；极端情况下 empty 触发链条升级 |
| Firecrawl / Tavily 成本失控 | 限流 + profile 默认不含 Firecrawl；审计日志每次 dump 用量 |
| 重构 ToolTier 命名波及太多测试 | T-0 任务先评估，必要时退回"复用 T2_WRITE"保守方案 |
| 反爬 / Cloudflare challenge | fallback_mode=on_error_or_empty 会自动走 Jina / Firecrawl；仍失败则 LLM 可见完整链条 |

---

## 11. 落地次序（供 plan 阶段参考）

| # | 任务 | 产物 |
|---|------|------|
| T-0 | Tier 扩展方案决策（扩展 vs 复用 T2_WRITE） | 本 spec 注释更新 |
| T-1 | ToolTier 扩展 + 全项目引用点替换 | types.py + grep 替换 |
| T-2 | ssrf_guard.py 实现 | 含黑白名单常量 |
| T-3 | markdown_extract.py + 引入 trafilatura 依赖 | pyproject.toml 更新 |
| T-4 | http_client.py + TTL 缓存 + redirect hook | |
| T-5 | LocalFetchEngine | |
| T-6 | JinaReaderEngine + FirecrawlEngine + engine registry | |
| T-7 | TavilyEngine + JinaSearchEngine | |
| T-8 | FetchRouter + RateLimiter | |
| T-9 | NetworkSafetyRule 接入 PolicyEngine | |
| T-10 | web_fetch / web_search 工具注册（按 profile 动态注册） | builtins.py 挂载 |
| T-11 | RuntimeConfig 加 override 字段 + web UI 下拉 | Edict 创建页 |
| T-12 | Prompt 同步（六部官员工具清单） | prompt_builder.py |
| T-13 | docs/impl + README 工具清单更新 | |
| T-14 | 测试（单元 + 集成 + 契约） | tests/* |

预估总工作量 **8-10 小时**（不含 web UI 打磨）。

---

## 12. 未来扩展（超出本 spec scope）

- **场景 C（浏览器自动化）**：通过 MCP server 对接 `vercel-labs/agent-browser`，保持在主包外
- **语义搜索**：Exa 作为 Tavily 的补充
- **持久化缓存**：若统计显示同一 URL 高频访问，可引入 sqlite-based 缓存（但要先明确隐私边界）
- **Edict 预算**：每个 Edict 声明最大 `web_*_calls` / `max_bytes_fetched`，超限熔断
