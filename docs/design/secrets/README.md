# 藏兵阁 — 凭证加密托管与按 host 注入

> 设计意图：让 Agent 在调外部 API / 第三方搜索抽取服务时拿到认证，但凭证值对 LLM 全程不可见——由系统按 URL host 静默注入。藏兵阁（古代藏兵器之所）即「敏感凭证集中保管」的隐喻命名。

**相关实现**：[../../impl/secrets/README.md](../../impl/secrets/README.md)

凭证消费方（`api_request` 工具、第三方 engine 工厂）的网络安全边界见 [../tools/network.md](../tools/network.md)。

## 1. Fernet master key 与加密落盘

凭证值用 [Fernet](https://cryptography.io/en/latest/fernet/)（AES-128-CBC + HMAC-SHA256 认证加密）对称加密后落盘，明文不进数据库、不进日志。

- master key 从环境变量 `TIANSHU_SECRET_MASTER_KEY` 读取，必须是 `Fernet.generate_key()` 的输出（32 字节 url-safe base64）。
- `SecretVault`（`vault.py`）封装 `encrypt(plaintext) -> bytes` / `decrypt(ciphertext) -> str`；解密失败（密文被篡改或换了 key）统一抛 `ValueError("credential decryption failed")`，不泄露 `InvalidToken` 细节。
- `get_vault()` 是进程级单例（双检锁），master key 缺失返回 `None` 并打 WARNING——调用方据此**降级**：`CredentialStore` 不构造、`api_request` engine 不注册、`/api/credentials` 直接 503。

key 不落盘是刻意的：数据库泄露（仅含密文）不等于凭证泄露，攻击者还需另外拿到进程环境里的 master key 才能解密。

## 2. 两类凭证模型

凭证统一存 `network_credentials` 表，靠 `kind` 字段区分两种语义完全不同的用途：

| kind | 用途 | 必填字段 | host_pattern | 匹配方式 |
|---|---|---|---|---|
| `edict_auth` | Agent `api_request` 调用某 host 时注入的认证 header | `host_pattern` + `header_template` | 有 | 按 URL host 匹配 |
| `engine_provider` | 第三方 engine 服务（jina/tavily/firecrawl）的 API key | `provider_name` | 空 | 按 provider 名查 |

`CredentialCreate` 在 `CredentialStore.create` 里按 kind 校验：

- `engine_provider`：`provider_name` 必填且限 `{jina, tavily, firecrawl}`；同一 provider **唯一**（已配置则报错让走 update），`host_pattern`/`header_template` 强制置空。
- `edict_auth`：`host_pattern` 与 `header_template` 都必填（如 `"Authorization: Bearer {value}"`）。

`CredentialView`（返回前端）与 domain 对象 `Credential` 的关键区别：**View 不含 `encrypted_value`/`value`**——凭证值只在「写入时加密」与「注入时解密」两个瞬间出现，永不回吐给前端或 LLM。

## 3. host-pattern 匹配注入 + 禁用 header 过滤

`api_request` 发请求前，`CredentialInjector.inject(url_host, user_headers)`（`injector.py`）完成两件事：

**(a) 禁用 header 过滤（防 LLM 自带凭证）**
LLM 自己传的 `headers` 先过 `validate_user_headers`，命中黑名单 `FORBIDDEN_USER_HEADERS`（`authorization` / `cookie` / `set-cookie` / `x-api-key` / `x-auth-token` / `proxy-authorization`，大小写不敏感）即抛 `ForbiddenHeader`，工具返回 `forbidden_header:{name}`。LLM 无法绕过系统注入自带认证。

**(b) 按 host 匹配并注入**

```text
find_for_host(host)  最具体优先：
  1. 字面相等 host_pattern == host        （最高优先）
  2. 通配 "*.example.com" 且 host endswith ".example.com"
  3. 都不中 → None（不注入，原样放行）
```

命中后：解密凭证值 → 按 `header_template` 渲染（`"Authorization: Bearer {value}"` → `("Authorization", "Bearer <real>")`）→ 叠加 `extra_headers` → 与用户 header 合并。若注入 header 与用户 header **同名**（大小写不敏感）抛 `CredentialConflict`，返回 `credential_conflict:{header}`——避免用户用空值覆盖掉注入的真凭证。

`engine_provider` 凭证不参与 host 匹配（`find_credentials_by_host` 只查 `kind='edict_auth'`），而是由 `resolve_provider_key(store, provider_name, env_var)` 在 engine 工厂里取：**DB 配置优先 → env 回落 → None**，解密失败不抛、记日志回落 env。

`redact_sensitive_headers` 在打日志/审计前把黑名单 header 值替换为 `<redacted>`，保证注入的真凭证不进日志。

## 4. last_used_at 追踪与审计

- 每次成功注入命中凭证，`inject` 调 `store.mark_used(cred.id)` 更新 `last_used_at`（`UPDATE ... SET last_used_at=?`）。可用于发现「长期未用」的僵尸凭证、或核对某凭证是否仍在被调用。
- 审计元数据只记 `credential_name`（凭证标识，**非凭证值**）：`InjectionResult.credential_name` 透传到 `ApiResponse.credential_name`，再进网络审计 detail。
- 删除是**软删**（`soft_delete_credential`：置 `deleted_at` 并把 `name` 改名为 `name__deleted_{id}`），保留历史可审计，同时腾出 `name` 的 UNIQUE 约束让同名可重建。所有查询都带 `WHERE deleted_at IS NULL`。

## 5. 威胁模型

| 威胁 | 防护手段 |
|---|---|
| **数据库泄露 → 凭证泄露** | 凭证值 Fernet 加密落盘；master key 仅在进程环境，不入库。仅拿到 DB 拿不到明文。 |
| **凭证值回吐前端 / LLM** | `CredentialView` 不含 `encrypted_value`/`value`；注入只在服务端发请求瞬间解密，不返回 header 给 LLM。 |
| **LLM 自带认证绕过注入** | `FORBIDDEN_USER_HEADERS` 黑名单直接拒（`ForbiddenHeader`）；同名注入冲突拒（`CredentialConflict`）。 |
| **越权注入到非目标 host** | 凭证按 `host_pattern` 精确/通配匹配，最具体优先；不匹配则不注入。配合 `api_request` 的 host 白名单（见 [../tools/network.md](../tools/network.md) §3）二层收窄。 |
| **SSRF 借凭证打内网** | host 匹配发生在 SSRF `validate_url` 之后（`api_request.py` 先校验 URL 再注入），内网/metadata 地址在注入前已被拒。 |
| **日志泄露** | `redact_sensitive_headers` 脱敏；解密异常只抛模糊 `ValueError`，不暴露密钥/密文。 |
| **master key 缺失静默放行** | `get_vault()` 返回 `None` 时整条凭证链路降级关闭（store 不建、engine 不注册、API 503），fail-closed 而非 fail-open。 |

**越权注入**的核心约束：凭证作用域由 `host_pattern` 钉死，单条 `edict_auth` 凭证只能注入到匹配 host 的请求；通配也只放宽到子域（`*.example.com`），不会泛化到任意 host。
