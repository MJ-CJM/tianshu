# 凭证管理（secrets）实现现状

**相关设计**：[../../design/secrets/README.md](../../design/secrets/README.md)

> 代码位于 `src/tianshu/secrets/`。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，加密落盘、注入机制、威胁模型见 design 篇。消费侧（`api_request` / engine 工厂）见 [../tools/](../tools/)。

## 1. 模块清单（`src/tianshu/secrets/`）

| 文件 | 关键类 / 函数 | 职责 |
|---|---|---|
| `vault.py` | `SecretVault`、`get_vault`、`reset_vault` | Fernet 对称加解密；进程级单例（双检锁），master key 缺失返回 `None` |
| `models.py` | `Credential` / `CredentialCreate` / `CredentialUpdate` / `CredentialView`（Pydantic v2） | 凭证 DTO；`Credential` 含 `encrypted_value`，`CredentialView` 不含密文 |
| `store.py` | `CredentialStore`、`resolve_provider_key` | DB 行 ↔ domain 对象；CRUD + 加解密 + host/provider 查询 + `mark_used` |
| `injector.py` | `CredentialInjector`、`FORBIDDEN_USER_HEADERS`、`redact_sensitive_headers`、`ForbiddenHeader` / `CredentialConflict` | 禁用 header 过滤 + host 匹配注入 |
| `__init__.py` | 统一 re-export | 对外只暴露上述类/函数 |

## 2. 落盘位置

凭证全部存 SQLite 的 `network_credentials` 表（建表见 `storage/schema.py` 的 `SCHEMA_SQL_CORE` 段；迁移见 `storage/migrations.py` 的 `run_migrations()` 内 `ALTER TABLE network_credentials`）：

| 列 | 说明 |
|---|---|
| `id` / `name` | ULID 主键；`name` UNIQUE（软删后改名腾出约束） |
| `host_pattern` / `header_template` / `extra_headers` | `edict_auth` 用：注入目标 + header 模板（JSON） |
| `encrypted_value` BLOB | Fernet 密文（**唯一持久化形态**，明文不落盘） |
| `kind` / `provider_name` | 区分 `edict_auth` vs `engine_provider`；`provider_name` 部分唯一索引 |
| `enabled` | 启停开关（disabled 视为未配置） |
| `last_used_at` / `deleted_at` | 注入追踪 / 软删标记 |
| `created_at` / `updated_at` | 时间戳 |

索引：`idx_netcreds_host`、`idx_netcreds_name`、`idx_netcreds_provider`（部分索引，`provider_name IS NOT NULL`）。

master key 不落盘，仅来自环境变量 `TIANSHU_SECRET_MASTER_KEY`（`Fernet.generate_key()` 的输出）。

## 3. Storage 层方法（`storage/credential_repo.py`）

`CredentialStore` 不直接写 SQL，全走 `Storage` 方法：`insert_credential` / `get_credential_by_id` / `list_credentials(kind=…)` / `find_credentials_by_host`（仅 `edict_auth`）/ `find_credentials_by_provider`（仅 `engine_provider`）/ `update_credential` / `mark_credential_used` / `soft_delete_credential`。所有读查询带 `WHERE deleted_at IS NULL`。

## 4. 装配

凭证子系统**无独立 lifespan 装配**，按需在两处懒构造：

**(a) engine 注册（`tools/hongluisi/engine_registry.py` `_do_build`）**
```text
get_vault()  → None? 整条降级（cred_store/api_engine 都不建）
vault + storage 齐 → CredentialStore(storage, vault)
                   → ApiRequestEngine(CredentialInjector(cred_store))
provider key 来源 = {name: resolve_provider_key(cred_store, name, env)[1]}  # db|env|none
jina/tavily/firecrawl engine 工厂吃 cred_store 取 key
```
`rebuild_engines()` 在凭证 CRUD 后被 `/api/credentials` 触发，live 热更 engine（无需重启即生效）。

**(b) HTTP API（`gateway/credentials_api.py`）**
`/api/credentials` 路由每次请求 `get_vault()` → 无 key 直接 503 → `CredentialStore(storage, vault)`。CRUD：
- `GET ""`（按 `kind` 过滤）/ `POST ""`（201，UNIQUE 冲突 409）/ `PATCH /{id}` / `DELETE /{id}`（软删，删前查 `find_edicts_referencing_host` 引用拦截 409）。
- 返回一律 `CredentialView`（无密文）；`engine_provider` 增删改后触发 `_trigger_engine_rebuild()`。

## 5. 调用链（注入路径）

```text
api_request(url, method, headers, …)        tools/hongluisi/api_request.py
  → validate_url(url)                        先 SSRF 校验（注入前）
  → host = urlparse(clean_url).hostname
  → CredentialInjector.inject(host, headers)
       validate_user_headers → ForbiddenHeader?  → forbidden_header:{h}
       store.find_for_host(host)  字面 > 通配 > None
       解密 + 渲染 header_template + extra_headers
       同名冲突 → CredentialConflict → credential_conflict:{h}
       命中 → store.mark_used(id)
  → client.request(headers=merged)
  → ApiResponse.credential_name（仅名字进审计）
```

## 6. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 新增第三方 provider | `CredentialStore.create` 的 `{jina,tavily,firecrawl}` 白名单加项；engine 工厂用 `resolve_provider_key` 取 key |
| 新增禁用 header | 往 `injector.FORBIDDEN_USER_HEADERS` 加；`redact_sensitive_headers` 自动随之脱敏 |
| 换匹配策略 | 改 `CredentialStore.find_for_host`（当前：字面 > `*.` 通配 > None）|
| 轮换 master key | 现实现单 key，无内建轮换；需逐条解密旧 key → 用新 key 重 `encrypt` 回写（`encrypted_value`），并切换 `TIANSHU_SECRET_MASTER_KEY` |
| key 缺失时的降级行为 | `get_vault()` 返回 `None` 的所有调用点（engine_registry / credentials_api）统一 fail-closed |
