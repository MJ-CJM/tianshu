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
