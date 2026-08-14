# 鸿胪寺 — 对外网络能力与安全边界

> 设计意图：给 Agent 受控的对外网络能力（取、搜、抽、调 API），同时严防 SSRF、凭证泄露与滥用。鸿胪寺（明朝掌外交礼宾）即「对外通讯」的隐喻命名。

## 1. 四个网络工具

| 工具 | tier | side_effect | 用途 |
|---|---|---|---|
| `web_fetch(url)` | T2_NETWORK | — | 取公开网页，返回 Markdown 正文 |
| `web_search(query, max_results)` | T2_NETWORK | — | 搜索，返回 ranked 列表（标题+URL+摘要） |
| `web_extract(url, schema, prompt?)` | T2_NETWORK | — | Firecrawl 按 JSON Schema 抽结构化数据 |
| `api_request(url, method, headers?, query?, json_body?)` | T2_NETWORK | ✓ | 调白名单外部 API |

所有 handler **call-time** 从 `engine_registry` 取最新 engine 实例，支持运行时 `rebuild_engines()` 热更凭证。engine 可用性在 handler 内 check，所以四个工具始终注册。

## 2. NetworkPolicy 与三档预设

`NetworkPolicy`（frozen dataclass）是 per-Edict 网络策略，作为 PolicyProfile 子字段：
- `fetch_engines`：按优先级的 engine 名（length=1 即钉死）
- `fallback_mode`：`none` / `on_error_or_empty`
- `search_provider`：`tavily` / `jina` / `duckduckgo` / None
- `allow_api_request` / `api_request_methods`（默认仅 GET/HEAD）
- 各工具 `*_rate_per_min`

| 预设 | fetch | search | api_request |
|---|---|---|---|
| `NETWORK_OFFLINE` | () | None | 禁 |
| `NETWORK_DEFAULT` | scrapling→local | duckduckgo | 禁 |
| `NETWORK_RESEARCH` | scrapling→local→jina→firecrawl | duckduckgo | GET/HEAD（写需显式启用） |

## 2.1 引擎选型速查

引擎链按顺序尝试，第一个成功即返回（`fallback_mode` 决定失败/空内容是否继续下一个）。**链里排着一个没装的引擎不会报错，只会被 `skipped` 掉**——它仍占着优先级，却什么也不做。

### fetch 引擎

| 引擎 | 机制 | 额外依赖 | key | 何时用 |
|---|---|---|---|---|
| `scrapling` | 伪装 TLS 指纹的 HTTP 抓取 | `uv sync --extra scrapling` | 免 | **默认首选**。纯 pip、轻量，专治 `local` 撞上的反爬 |
| `scrapling_dynamic` | Playwright Chromium，渲染 JS | 上述 + `scrapling install` 下浏览器二进制 | 免 | 目标是前端渲染的 SPA。重（30s 超时），且需在鸿胪寺页显式开启 |
| `scrapling_stealthy` | Camoufox，过 Cloudflare | 同上 | 免 | 被人机验证挡住时。最重（60s 超时），同样需显式开启 |
| `local` | httpx + trafilatura 提正文 | 内置 | 免 | 兜底。最快最省，但不伪装、不渲染，遇反爬即失手 |
| `jina` | r.jina.ai 代理转 markdown | 内置 | 可选 | 无 key 也能走，限流严；配 key 后额度按 jina 账户计 |
| `firecrawl` | api.firecrawl.dev 商业抓取 | 内置 | **必需** | 前面都拿不下时的付费兜底。key 无效会以 `firecrawl_error:401` 收尾 |

### search provider

| provider | key | 说明 |
|---|---|---|
| `duckduckgo` | 免 | 硬依赖 lxml（`web` extra）。无 key、易被限流，返回空结果是常态化的间歇故障 |
| `tavily` | **必需** | 面向 agent 的检索 API，结果结构化、稳定 |
| `jina` | 可选 | 与 fetch 侧共用同一把 key；配额用尽表现为 HTTP 402 |

### 选型建议

- **只想要能用**：`scrapling → local` + `tavily`。前者免费管抓取，后者花小钱换稳定检索。
- **要抓 SPA**：把 `scrapling_dynamic` 插在 `scrapling` 之后，并在鸿胪寺开启浏览器引擎开关。
- **别把没装/没 key 的引擎留在链里**：它们只会拖长失败路径，并让最终错误停在一个误导性的状态码上。

### 抓不到时按这个顺序查

1. `GET /hongluisi/engine-status` → `fetch_engines` 里有没有你选的那个（没有 = 未安装/未配 key，链里是空转的）
2. 事件流里 `tool.failed` 的 `details.network.attempts` → 逐个引擎的 `status` 与 `reason`，能直接看出断在哪一环
3. `providers` 字段 → 该 provider 的 key 来源（`db` / `env` / `none`）；有 key 仍 401/402 说明 key 无效或配额耗尽

## 3. profile / host 白名单解析

`resolve_network_for_edict(edict)` 是 NetworkSafetyRule 与 hongluisi/tools.py **共用**的解析器（防两处 fallback 不一致）：

```text
优先级：Edict runtime override > 系统级 engine override > PolicyProfile template 预设
fallback：template_name 缺失/无效 → refactor-in-place(DEFAULT)；想离线须显式 safe-explore
```

`api_request` 的 host 白名单（来自 `EdictRuntime`）：
- `api_request_hosts`：读方法 host 白名单
- `api_request_write_hosts`：写方法（POST/PUT/DELETE/PATCH）额外白名单
- 写方法命中白名单后**仍触发审批**（NetworkSafetyRule require_approval）

## 4. SSRF 防护

`ssrf_guard.validate_url(url)` 对每个 URL 三步校验，失败抛 `SSRFViolation(code, internal_reason)`（code 脱敏可透传 LLM，internal_reason 仅服务端审计）：

1. **字面黑名单**：scheme 限 http/https；port 限 {None,80,443,8080,8443}；hostname 拒 `localhost` / `.local`/`.internal`/`.corp`
2. **DNS 解析后逐 IP 校验**：拒 private/loopback/link_local/multicast/reserved/unspecified；额外拒 `169.254.169.254/32`（云 metadata）、`100.64.0.0/10`（CGNAT）、`fd00::/8`（IPv6 ULA）
3. **userinfo 剥离**：去掉 `user:pass@` 重组干净 URL

## 5. 防凭证泄露

凭证由系统按 host 注入，**LLM 不可见**：
- `api_request` 工具描述明确禁止 LLM 传 `Authorization`/`Cookie`/`X-Api-Key`
- 凭证经 `tianshu.secrets` 的 `CredentialStore` + `CredentialInjector` + `get_vault()`（藏兵阁/vault 加密托管）按 host 自动注入
- LLM 若强传认证 header，engine 返回 `credential_conflict:{header}` 拒绝
- 审计元数据只记 `credential_name`（凭证标识，非凭证值）
- provider 凭证（搜索/抽取服务）与 edict auth 凭证经 `network_credentials` 表区分

## 6. Rate Limit

`RateLimiter`（进程单例）按 `(edict_id, tool_name)` 滑动窗口令牌桶（deque 记 60s 内时间戳），超配额返回 `rate_limited:retry_after_Xs`。每工具配额来自 NetworkPolicy（如 web_fetch 20/min、web_search 10/min、api_request 30/min）。

## 7. 调用链

```text
web_fetch
  → resolve_network_for_edict → profile 校验 fetch_engines
  → RateLimiter.check
  → FetchRouter.dispatch(url)  (每 engine 内 SSRF validate_url)
  → ok: Markdown + network 审计 detail | fail: is_error

api_request
  → profile allow_api_request? → host in api_request_hosts? → 写方法 host in write_hosts?
  → (写方法审批由 NetworkSafetyRule 在 PolicyEngine 拦截)
  → RateLimiter.check → CredentialInjector 注入凭证 → engine.request
```

**相关实现**：[../../impl/tools/](../../impl/tools/)
