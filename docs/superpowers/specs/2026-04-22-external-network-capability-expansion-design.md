# 外部网络通讯能力扩展设计（L1 api_request + L4 web_extract + 藏兵阁凭证）

**日期**: 2026-04-22
**作者**: brainstorming session
**状态**: 设计完成，待 plan
**扩展自**: `docs/superpowers/specs/2026-04-21-web-access-tools-design.md`
**实施分支**: `feat_phase5`（承接现有 15 commit）

---

## 0. 背景与动机

上一轮 spec (`2026-04-21-web-access-tools-design.md`) 已经设计并部分实现了 L2 (`web_fetch`) + L3 (`web_search`)，覆盖"读公开网页 + 关键词搜索"两种能力。但"完整的对外网络通讯能力"按自然分层是：

```
L1  HTTP 调用 (api_request)      —— 通用 HTTP 客户端，打三方 API
L2  内容读取 (web_fetch)  ✅     —— 已设计（Task 1-15 已实现）
L3  关键词搜索 (web_search) ✅   —— 已设计
L4  结构化抽取 (web_extract)     —— schema → JSON
L5  交互式浏览器 (web_browse)    —— 登录/点击/填表，显式不做
L6  入站/订阅 (webhook, rss)     —— 基础设施级，超出 Agent 工具语义，不做
```

本 spec 补齐 **L1** 与 **L4**，同时引入凭证层（藏兵阁 UI 托管的加密凭证池）让 `api_request` 能以"LLM 零可见"的方式携带 Authorization。

---

## 1. 范围决策

### 1.1 纳入

- `api_request(url, method, headers, query, json_body)` —— 通用 HTTP，读写两档
- `web_extract(url, schema, prompt?)` —— Firecrawl `/v1/extract`
- **藏兵阁**新增外部凭证管理（CRUD + 加密存储 + host 匹配注入）
- NetworkPolicy 三档预设扩展（OFFLINE/DEFAULT/RESEARCH 对 L1 的差异化门禁）
- EdictRuntime 新增 `api_request_hosts` 白名单
- 管理后台：藏兵阁凭证 tab、Edict 创建页网络 section、AuditDashboard 网络事件展示

### 1.2 显式不做（Non-Goals）

- **L5 交互式浏览器**：`agent-browser` / `browser-use` / `stagehand` 全部暂不采纳。理由见 1.3。
- **L6 入站 webhook / RSS 订阅**：属基础设施级改动，形态完全不同（服务端接收 vs 客户端发起），留作独立 proposal。
- **跨 Edict 凭证继承**：凭证池全局共享，但必须每个 Edict 在 `api_request_hosts` 白名单显式引用 host 才能命中。不做隐式继承。
- **凭证轮换工作流**：本期只支持 UI 上手动替换 value；自动轮换 / 过期提醒 / 到期告警均为 follow-up。
- **WebSocket / SSE 长连接 / 文件下载（PDF/二进制）**：保持 body cap 1MB 与 HTTP 单次请求模型。

### 1.3 `vercel-labs/agent-browser` 评估

| 维度 | 评估 | 结论 |
|------|------|------|
| 定位 | Rust CLI + Chrome for Testing，解决"浏览器自动化" | 与我们的"读资料/调 API"错位 |
| Python 集成 | 无 SDK，只能 subprocess / WebSocket | 故障面增加，不值得 |
| 成熟度 | 2025 下半年开源，生态远不如 browser-use (35k★) | 同场景有更成熟备选 |
| 启用时机 | 真要做 L5 时通过 **MCP server** 对接更干净 | 保持核心依赖轻量 |

**结论**：本 spec 范围内不采纳任何 L5 方案。未来若需要，走 MCP 外挂路径而非集成到鸿胪寺。

---

## 2. 目标 & 非目标

### 2.1 目标

- 六部官员在 `trusted-automation` profile 下可用 `api_request` 打三方 API（GitHub / Notion / 自建后端 / 天气 / 汇率...），凭证由藏兵阁托管、LLM 零可见
- `web_extract` 给明确 schema 的内容抽取场景省 token（不用先 fetch 再让 LLM 自己 parse）
- 四个网络工具（fetch / search / api_request / extract）共享同一套基础设施（SSRF、HttpClient、RateLimiter、Registry、Policy）
- 凭证、host 白名单、写方法审批三重门禁：**任何一层关掉，整个能力降级而非崩溃**
- 全部能力作为**一个 PR** 合入 `main`

### 2.2 非目标

- 不引入新的加密库（只用标准 `cryptography.fernet`）
- 不做凭证版本化（简单的 update-in-place）
- 不做跨 session 的审计去重（进程内审计即可）
- 不动现有 Task 1-15 已落地的代码（L2/L3 基础设施视为不可修改的契约）

---

## 3. 架构与模块边界

### 3.1 模块布局

```
src/tianshu/
├── secrets/                          # 新增：凭证子系统
│   ├── __init__.py
│   ├── vault.py                      # SecretVault: Fernet 封装
│   ├── store.py                      # CredentialStore: DB CRUD
│   ├── injector.py                   # CredentialInjector: host 匹配 + header 渲染
│   └── models.py                     # pydantic: Credential, CredentialCreate, CredentialUpdate
│
├── tools/hongluisi/
│   ├── engines/
│   │   └── firecrawl_extract.py      # 新增：FirecrawlExtractEngine
│   ├── api_request.py                # 新增：api_request 工具实现
│   ├── web_extract.py                # 新增：web_extract 工具实现
│   ├── tools.py                      # 扩展：register_hongluisi 追加 api_request / web_extract
│   └── policy.py                     # 扩展：NetworkPolicy 新增 3 字段
│
├── tools/policy_rules/
│   └── network_safety.py             # 扩展：原 NetworkSafetyRule 加 api_request 分支
│
├── api/
│   └── credentials.py                # 新增：/api/credentials CRUD
│
├── models/
│   └── credential.py                 # 新增：DB model（如果项目用 sqlalchemy）
│
└── executor/
    └── ambient.py                    # 扩展：向 tool 暴露 current edict 的 ContextVar

web/src/                              # 前端（藏兵阁实际对应 SystemManagementPage.tsx）
├── pages/SystemManagementPage.tsx    # 扩展：内联新增 ExternalCredentialsTab()
├── pages/EdictCreatePage.tsx         # 扩展：追加"网络能力" section
├── pages/AuditDashboardPage.tsx      # 扩展：按 network.* 事件分类渲染
├── api/credentials.ts                # 新增：/api/credentials 客户端封装
└── api/types.ts                      # 扩展：Credential / NetworkAuditEvent 类型
```

### 3.2 调用链（api_request 举例）

```
LLM decides tool call
    ↓
agent.execute_tool("api_request", {"url": "https://api.github.com/...", ...})
    ↓
ToolRegistry.execute()
    ↓
api_request_tool_fn(args)
    ├─ get ambient edict (ContextVar)
    ├─ resolve NetworkPolicy (from policy_profile template)
    ├─ check profile.allow_api_request
    ├─ check method in profile.api_request_methods (T3 写方法还要查 edict.api_request_write_hosts)
    ├─ check url host in edict.runtime.api_request_hosts
    ├─ SSRF validate_url()
    ├─ RateLimiter.check(edict_id, "api_request", rate_per_min)
    ├─ CredentialInjector.inject_headers(host, user_headers) → merged_headers
    ├─ SharedHttpClient.request(method, url, headers=merged, ...)
    └─ return {status, headers, body}
    ↓
AuditRecord {tool: api_request, host, method, credential_name, status, ...}
```

---

## 4. 凭证层（藏兵阁托管）

### 4.1 SecretVault（`src/tianshu/secrets/vault.py`）

```python
from cryptography.fernet import Fernet
from pydantic import SecretStr

class SecretVault:
    """Fernet 对称加密封装。主密钥来自环境变量。"""

    def __init__(self, master_key: SecretStr) -> None:
        # master_key 应为 Fernet.generate_key() 的输出（32 字节 url-safe base64）
        self._fernet = Fernet(master_key.get_secret_value().encode())

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode("utf-8")

_vault: SecretVault | None = None

def get_vault() -> SecretVault | None:
    """主密钥缺失时返回 None；调用方据此决定降级策略。"""
    global _vault
    if _vault is not None:
        return _vault
    key = os.getenv("TIANSHU_SECRET_MASTER_KEY")
    if not key:
        logger.warning("[secrets] TIANSHU_SECRET_MASTER_KEY unset; api_request / web_extract disabled")
        return None
    _vault = SecretVault(SecretStr(key))
    return _vault
```

**启动期契约**：`get_vault() is None` 时，`api_request` 工具与藏兵阁凭证 UI 后端接口均**不注册**。日志明确告警，应用仍能启动。

### 4.2 DB 表（`network_credentials`）

```sql
CREATE TABLE network_credentials (
    id            TEXT PRIMARY KEY,            -- uuid
    name          TEXT NOT NULL UNIQUE,        -- 用户可见名: "github-prod-token"
    host_pattern  TEXT NOT NULL,               -- "api.github.com" 或 "*.notion.com"
    header_template TEXT NOT NULL,             -- "Authorization: Bearer {value}"
    extra_headers TEXT NOT NULL DEFAULT '{}',  -- JSON: {"Notion-Version": "2022-06-28"}
    encrypted_value BLOB NOT NULL,             -- Fernet ciphertext
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_used_at  TEXT,                        -- nullable; 每次注入命中时更新
    deleted_at    TEXT                         -- 软删除
);
CREATE INDEX idx_network_credentials_host ON network_credentials(host_pattern);
CREATE INDEX idx_network_credentials_name ON network_credentials(name);
```

### 4.3 host_pattern 匹配规则

从"最具体"到"最宽松"排序，首次命中即止：

1. **字面精确**：`api.github.com` 只匹配该域
2. **子域通配**：`*.notion.com` 匹配 `api.notion.com` / `www.notion.com`，不匹配 `notion.com`
3. **不支持正则、不支持路径级匹配**：pattern 只管 host，不管 path

```python
def match_host(request_host: str, patterns: list[str]) -> str | None:
    # literal first
    if request_host in patterns:
        return request_host
    # wildcard
    for p in patterns:
        if p.startswith("*.") and request_host.endswith(p[1:]):
            return p
    return None
```

### 4.4 CredentialInjector

```python
class CredentialInjector:
    def __init__(self, store: CredentialStore, vault: SecretVault) -> None:
        self._store = store
        self._vault = vault

    async def inject_headers(
        self, url_host: str, user_headers: dict[str, str]
    ) -> tuple[dict[str, str], str | None]:
        """返回 (merged_headers, credential_name_if_hit)。不命中不是错误。"""
        cred = await self._store.find_by_host(url_host)
        if cred is None:
            return dict(user_headers), None

        # 解密 value
        value = self._vault.decrypt(cred.encrypted_value)

        # 渲染 header_template
        # "Authorization: Bearer {value}" → ("Authorization", "Bearer <value>")
        header_name, template = cred.header_template.split(":", 1)
        header_name = header_name.strip()
        rendered = template.strip().format(value=value)

        injected = {header_name: rendered, **cred.extra_headers}

        # 用户 header 不允许覆盖注入 header
        for k in injected:
            if k.lower() in {h.lower() for h in user_headers}:
                raise CredentialConflict(header=k)

        merged = {**user_headers, **injected}
        await self._store.mark_used(cred.id)
        return merged, cred.name
```

### 4.5 敏感 header 黑名单

`api_request(headers=...)` 的 user-supplied headers 中，以下名字**直接拒绝**（返回 400 级别 ToolResult error）：

```
Authorization, Cookie, Set-Cookie, X-Api-Key, X-Auth-Token, Proxy-Authorization
```

理由：这些 header 必须走藏兵阁托管路径，防止 LLM 被 prompt injection 诱骗把凭证通过 headers 参数泄漏。

---

## 5. api_request 工具

### 5.1 ToolDefinition

```python
ToolDefinition(
    name="api_request",
    description=(
        "Make an HTTP request to a whitelisted external API. "
        "Credentials are managed by the system — do not pass Authorization/Cookie/X-Api-Key "
        "headers (they will be rejected). "
        "Methods GET/HEAD are read-only; POST/PUT/DELETE/PATCH have side effects and require approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL"},
            "method": {
                "type": "string",
                "enum": ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"],
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "Extra request headers (excluding auth headers which are auto-injected)",
                "additionalProperties": {"type": "string"},
            },
            "query": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "json_body": {
                "type": ["object", "array", "null"],
                "description": "JSON request body (for non-GET/HEAD methods)",
            },
        },
        "required": ["url"],
    },
    tier=ToolTier.T2_NETWORK.value,  # 注意：运行时按 method 升级到 T3_WRITE
    max_result_chars=16000,
)
```

### 5.2 运行时 tier 升级

`api_request` 在 `ToolDefinition` 上标 `T2_NETWORK`，但实际执行时：
- GET/HEAD → 保持 T2，直接放行（只过 SSRF + rate limit + 凭证注入）
- POST/PUT/DELETE/PATCH → 由 `NetworkSafetyRule` 识别并**在 PolicyEngine 里动态升级到 T3_WRITE**，触发审批链

这样做的好处是：
- Registry 端只存一个 ToolDefinition，不需要拆成两个工具
- LLM 看到的 schema 是完整的（知道可以 POST）
- 真正的风控决策集中在 PolicyRule 层，一处维护

### 5.3 请求/响应约束

| 项 | 限制 |
|----|------|
| Body size in | 1 MB（JSON 序列化后） |
| Body size out | 1 MB |
| Timeout | 15s 总 / 5s connect |
| Redirects | 最多 3 次，每跳重做 SSRF 校验 |
| Methods | 见 ToolDefinition enum |
| Response truncation | 16000 字符（超出则返回 `{status, truncated:true, body_preview, bytes_total}`） |

### 5.4 失败语义

所有 `api_request` 错误走 `error_result(reason)` 返回，reason 取值：

| reason | 触发 |
|--------|------|
| `ssrf_blocked` | SSRF 校验失败（内部 IP / bad scheme / ...） |
| `host_not_whitelisted` | 目标 host 不在 `edict.runtime.api_request_hosts` 里 |
| `method_not_allowed` | profile 不允许此 method |
| `method_requires_approval` | 写方法需要审批但未获批 |
| `forbidden_header:<name>` | user 传入了敏感 header |
| `credential_conflict:<name>` | user header 与注入 header 冲突 |
| `rate_limited:retry_after_<n>s` | 限流命中 |
| `request_too_large:<bytes>` / `response_too_large:<bytes>` | 大小超限 |
| `timeout` / `http_error:<name>` | 网络层 |
| `http_status:<code>` | 4xx/5xx 响应 |

LLM 永远**不**看到 credential value / credential id / 内部 IP 详情。

---

## 6. web_extract 工具

### 6.1 ToolDefinition

```python
ToolDefinition(
    name="web_extract",
    description=(
        "Extract structured data from a public web page using an AI-powered extractor. "
        "Provide a JSON schema describing fields to extract. "
        "Only registered if the Firecrawl engine is configured."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "schema": {
                "type": "object",
                "description": "JSON Schema describing fields to extract",
            },
            "prompt": {
                "type": "string",
                "description": "Optional extraction instruction to complement the schema",
            },
        },
        "required": ["url", "schema"],
    },
    tier=ToolTier.T2_NETWORK.value,
    max_result_chars=8000,
)
```

### 6.2 引擎

只接 **Firecrawl `/v1/extract`**。原因：
- Jina 无原生 schema-based extract
- 自建 extractor 要跑 LLM，和 tool 侧 LLM 分层，复杂度炸裂
- Firecrawl key 已经在系统里（fetch 链成员），零新增依赖

**注册条件**：`TIANSHU_FIRECRAWL_API_KEY` 必须存在，否则 `web_extract` 不注册。

### 6.3 响应

Firecrawl 原样返回 `{data: {field1: ..., field2: ...}}`，服务端用 pydantic 按 schema 校验一遍，失败返 `error_result("extract_schema_mismatch:<field>")`。

---

## 7. Policy 扩展

### 7.1 NetworkPolicy 新增字段

```python
@dataclass(frozen=True)
class NetworkPolicy:
    # 既有字段
    fetch_engines: tuple[str, ...] = ("local",)
    fallback_mode: Literal["none", "on_error_or_empty"] = "none"
    search_provider: Literal["tavily", "jina"] | None = None
    max_fallback_depth: int = 3
    web_fetch_rate_per_min: int = 20
    web_search_rate_per_min: int = 10
    # 新增 3 字段
    allow_api_request: bool = False
    api_request_methods: tuple[str, ...] = ("GET", "HEAD")
    api_request_rate_per_min: int = 30
    web_extract_rate_per_min: int = 10  # 也放这里
```

### 7.2 三档预设对 L1 的差异

| Profile | allow_api_request | api_request_methods |
|---------|-------------------|---------------------|
| OFFLINE | False | () |
| DEFAULT | False | () |
| RESEARCH | True | ("GET", "HEAD") |

**写方法**（POST/PUT/DELETE/PATCH）在**任何 profile 默认都不开启**，必须在 `EdictRuntime.api_request_write_methods` 额外显式启用，并走审批。

### 7.3 EdictRuntime 新增字段

```python
@dataclass(frozen=True)
class EdictRuntime:
    # 既有
    policy_profile: PolicyProfilePayload
    fetch_engine_override: str | None = None
    search_provider_override: str | None = None
    # 新增
    api_request_hosts: tuple[str, ...] = ()            # host 白名单（read）
    api_request_write_hosts: tuple[str, ...] = ()      # host 白名单（write），必须 ⊆ api_request_hosts
```

**校验**：`api_request_write_hosts` 必须是 `api_request_hosts` 的子集，保存时服务端强校验。

### 7.4 NetworkSafetyRule 扩展

原 `NetworkSafetyRule`（priority=75）只处理 web_fetch / web_search。扩展为：

```python
class NetworkSafetyRule:
    priority = 75

    def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        tool = ctx.tool_call.name
        if tool == "web_fetch": ...       # 既有
        if tool == "web_search": ...      # 既有
        if tool == "api_request":
            return self._eval_api_request(ctx)
        if tool == "web_extract":
            return self._eval_web_extract(ctx)
        return PolicyDecision.PASS

    def _eval_api_request(self, ctx):
        net = self._resolve_network_policy(ctx)
        if not net.allow_api_request:
            return PolicyDecision.deny("api_request_not_allowed_in_profile")

        url = ctx.tool_call.args["url"]
        method = ctx.tool_call.args.get("method", "GET")
        host = urlparse(url).hostname

        # 1. host whitelist
        if host not in ctx.edict.runtime.api_request_hosts:
            return PolicyDecision.deny("host_not_whitelisted")

        # 2. method gate
        if method not in net.api_request_methods and method not in WRITE_METHODS:
            return PolicyDecision.deny("method_not_allowed")

        # 3. 写方法 → 升级审批
        if method in WRITE_METHODS:
            if host not in ctx.edict.runtime.api_request_write_hosts:
                return PolicyDecision.deny("write_method_host_not_whitelisted")
            return PolicyDecision.approval_required(
                tier=ToolTier.T3_WRITE,
                reason=f"api_request {method} {host}",
            )

        return PolicyDecision.PASS
```

---

## 8. UI 设计

### 8.1 藏兵阁 - 外部凭证 tab

**路径**: `web/src/pages/SystemManagementPage.tsx` 内联新增 `ExternalCredentialsTab()`（与现有 `SkillsTab` / `ToolsTab` / `SystemPromptTab` 等保持一致风格）。在 tabs 数组末尾追加 tab 配置项，不做全局重构。

**列表字段**：
| 列 | 说明 |
|----|------|
| 名称 | `name` |
| 匹配域 | `host_pattern` |
| Header 模板 | `header_template`（value 掩码为 `Bearer •••`） |
| 最近使用 | `last_used_at` 相对时间（"3 分钟前"） |
| 操作 | 编辑 / 删除 |

**新增表单**：
```
名称*:         [text]
匹配域*:       [text]  例: api.github.com 或 *.notion.com
Header 名*:    [select: Authorization | X-Api-Key | 自定义]
Header 模板*:  [text]  默认: "Bearer {value}"
Value*:        [password]
附加 Headers:  [JSON editor] 可选，用于 Notion-Version 之类
```

保存时客户端不回显 value，只显示"已保存"。

**编辑**：host_pattern / header_template 不可改（改了就等于新建新的凭证），只能改 value 和 extra_headers。

**删除**：软删除。删除前检查是否有 Edict 在 `api_request_hosts` 引用对应 host，有则阻止并提示。

### 8.2 Edict 创建页 - 网络能力 section

在 Edict 创建 / 编辑页新增一个"网络能力"折叠面板：

```
☐ 启用外部 API 调用 (api_request)
   └─ 允许的 host 列表: [multi-select from credentials] + [自由输入]
      例: api.github.com (有凭证), api.weather.com (无凭证，公开 API)

      ☐ 允许写方法 (POST/PUT/DELETE/PATCH) - 需要审批
         └─ 允许写的 host: [multi-select，必须是上面的子集]
```

**约束校验**（前端即时 + 后端复核）：
- 写 host 必须 ⊆ 读 host
- profile 为 OFFLINE 时整个 section disabled
- profile 为 DEFAULT 时"启用 api_request"不可勾选（灰色 + tooltip "需切换到 trusted-automation profile"）

### 8.3 AuditDashboard - 网络事件

每个 network 工具调用在审计里显示：

```
▸ 14:32:08  api_request  GET  api.github.com/repos/...  200  cred:github-prod
▸ 14:32:12  web_fetch    https://docs.python.org/3/...  ok   (cached)
▸ 14:33:45  api_request  POST api.notion.com/v1/pages  ❌ denied: write_method_host_not_whitelisted
```

点击行展开：完整 URL / headers（auth 类 redacted）/ body_preview / timing。

---

## 9. 安全考量

### 9.1 威胁模型

| 威胁 | 缓解 |
|------|------|
| Prompt injection 诱使 LLM 打内网 API | SSRF (既有) + `api_request_hosts` 白名单（默认空） |
| LLM 通过 `headers` 参数泄漏系统凭证 | 敏感 header 黑名单（`Authorization/Cookie/X-Api-Key/...`），违反即 tool error |
| 凭证在日志 / 审计 / 错误消息里泄漏 | `CredentialInjector.inject_headers` 返回的 merged_headers 打日志前走 `redact_sensitive_headers()` 过一遍 |
| 加密主密钥泄漏 | env-only，不进 git，不进 docker image，ops 手册强调 |
| 缓存污染：不同凭证的 GET 共用缓存 | cache key 扩为 `(url, engine, credential_name_or_none)` |
| Firecrawl extract 返回脏 JSON | pydantic 按 schema 校验，失败返 `extract_schema_mismatch` |
| 写方法误触发 | 三重门禁：profile + write_hosts 白名单 + 审批链 |
| 凭证 DB 备份泄漏 | Fernet 加密至少让备份本身无法直接用；建议 ops 进一步用磁盘加密 |

### 9.2 审计字段

`AuditRecord.network` 子对象字段：
```
{
  "tool": "api_request",
  "method": "GET",
  "host": "api.github.com",
  "path": "/repos/...",
  "credential_name": "github-prod-token",   // 命中时，永不写 value
  "status": 200,
  "bytes_in": 0,
  "bytes_out": 12453,
  "cached": false,
  "duration_ms": 342,
  "rate_limit_remaining": 29
}
```

### 9.3 启动期健康检查

应用启动时记录（WARN 级别）：
```
[hongluisi] web_fetch enabled (engines: local, jina, firecrawl)
[hongluisi] web_search enabled (providers: tavily, jina)
[hongluisi] api_request enabled (vault:ok, 3 credentials in store)
[hongluisi] web_extract enabled (engine: firecrawl)

# 或降级场景：
[hongluisi] api_request DISABLED: TIANSHU_SECRET_MASTER_KEY unset
[hongluisi] web_extract DISABLED: TIANSHU_FIRECRAWL_API_KEY unset
```

---

## 10. PR 组织

### 10.1 总览

- **分支**: `feat_phase5`（已有 15 commit）
- **目标 PR**: 合入 `main`，一次性交付 L2/L3 + L1/L4 + 凭证 + UI
- **新增 commit**: ~26 个（详见 10.2）
- **PR 总 commit**: ~41 个
- **PR 规模**: ~3500 行净增，~40 文件

### 10.2 Commit 切分（26 个新 commit）

```
第 0 层：已在分支（Task 1-15，15 commit）— 不动
  ToolTier / NetworkPolicy / Edict fields / SSRF / Markdown /
  HttpClient / 5 engines / Registry / Router / RateLimiter

第 1 层：凭证基础设施（4 commit）
  C1  feat(secrets): SecretVault Fernet wrapper
  C2  feat(secrets): CredentialStore DB 表 + CRUD
  C3  feat(secrets): alembic 迁移脚本 network_credentials
  C4  feat(secrets): host 匹配 + header 注入器

第 2 层：Policy / Edict 扩字段（3 commit）
  P1  feat(policy): NetworkPolicy 扩 api_request_* 字段
  P2  feat(policy): 3 档 profile 对 L1 差异化
  P3  feat(edict): EdictRuntime 扩 api_request_hosts / write_hosts

第 3 层：新引擎 + 新工具（6 commit）
  T1  feat(hongluisi): FirecrawlExtractEngine
  T2  feat(hongluisi): api_request engine (复用 HttpClient + injector)
  T3  feat(hongluisi): web_fetch / web_search 工具注册              ← 原 Task 16
  T4  feat(hongluisi): api_request 工具注册 + 运行时 tier 升级
  T5  feat(hongluisi): web_extract 工具注册
  T6  feat(hongluisi): register_hongluisi 主入口 + ambient Edict     ← 原 Task 18

第 4 层：安全/策略规则（3 commit）
  S1  feat(rules): NetworkSafetyRule 扩展 api_request / web_extract  ← 原 Task 17
  S2  feat(rules): 写方法审批路径接入 PolicyEngine
  S3  feat(audit): network 审计字段 + 敏感 header redact

第 5 层：后端 API（3 commit）
  A1  feat(api): /api/credentials CRUD endpoints
  A2  feat(api): /api/edicts 校验 api_request_hosts ⊆ credentials
  A3  feat(api): 审计查询按 credential_name / host 过滤

第 6 层：前端（4 commit）
  U1  feat(web): 藏兵阁 - 外部凭证 tab
  U2  feat(web): Edict 创建页 - 网络能力 section
  U3  feat(web): AuditDashboard - 网络事件行
  U4  feat(web): 宫殿首页提示"外部感知"已解锁

第 7 层：收尾（3 commit）
  F1  test: 网络能力端到端 + 单元测试统一补齐                        ← 原 Task 20
  F2  docs: README + skills.md + ops 凭证手册                        ← 原 Task 19
  F3  chore: prompt_builder 自描述 + 示例 edict 模板
```

### 10.3 依赖图

```
     C1 ─► C2 ─► C3                P1 ─► P2
              └─► C4 ─► T2                  └─► P3
                                                │
     T1 ─► T5 ─────────┐                        │
     T2 ─► T4 ─────────┤                        │
     T3 ──────────────┤  (聚合) T6 ◄────────────┘
                                │
                                ▼
                        S1 ─► S2 ─► S3
                                       │
                                       ▼
                               A1 ─► A2 ─► A3
                                              │
                                              ▼
                                      U1 / U2 / U3 / U4  (并行)
                                              │
                                              ▼
                                      F1 ─► F2 ─► F3
```

关键瓶颈：
- **C4 → T2**：凭证注入器必须先于 api_request engine
- **T6 是收敛点**：六个工具（既有 + 新增）在此统一挂到 builtins
- **S1 → 所有写方法**：没有 NetworkSafetyRule，T4 注册时需要兜底（默认拒绝所有 api_request，由 S1 commit 放行）

### 10.4 每个 commit 的落地契约

每个 commit 必须同时满足：

1. **可独立编译**：`ruff check && black --check && isort --check && mypy src` 全绿
2. **可独立 smoke**：commit 引入的组件至少有一个 smoke 调用（pytest 覆盖率统一放 F1）
3. **可独立 revert**：`git revert <sha>` 后剩余代码仍能 `uvicorn main:app` 起动
4. **commit message body 必含三行**：
   ```
   引入: <新增的模块 / 函数 / 表>
   使用者: <下一个依赖此 commit 的 commit>
   关闭: <失败时的降级路径 / 如何禁用>
   ```

### 10.5 回滚策略

| 出事模块 | 回滚 commit | 影响 |
|---------|------------|------|
| 凭证加密/注入异常 | `C1~C4` | api_request 工具自动不注册；fetch/search/extract 不受影响 |
| 写方法审批失灵 | `S2` | api_request 写方法默认拒绝；读方法不受影响 |
| UI 前端 bug | `U1`~`U4` 任一 | 管理功能降级，后端工具仍可用 |
| 迁移脚本破坏 DB | `C3` | 必须手动 `DROP TABLE network_credentials` + 回退 migration head |
| Firecrawl extract 炸 | `T1 + T5` | web_extract 不注册；其他不影响 |

---

## 11. 验证路径（合入前 checklist）

```
[1]  本地启动：配齐 TIANSHU_{JINA,FIRECRAWL,TAVILY}_API_KEY + TIANSHU_SECRET_MASTER_KEY
[2]  启动日志看到 4 个工具全部 "enabled"
[3]  UI 藏兵阁新增凭证 → DB 可见 encrypted_value 是 bytes，不是 plaintext
[4]  创建 Edict (safe-explore) → LLM 调 web_fetch / api_request 都拒绝（OFFLINE）
[5]  切 refactor-in-place → web_fetch / web_search / web_extract 可用，api_request 仍拒
[6]  切 trusted-automation + api_request_hosts=["api.github.com"]
     → api_request GET 通；POST 触发审批
[7]  不在白名单的 host → 返回 host_not_whitelisted
[8]  headers 里传 "Authorization": "Bearer xxx" → 返回 forbidden_header
[9]  审计页看到 network.* 事件，credential_name 有值，value 永远为空
[10] 关掉 TIANSHU_SECRET_MASTER_KEY 重启 → api_request 自动不注册，日志有 WARN
[11] 打满 rate limit → 返回 retry_after_*s
[12] SSRF 恶意 URL (127.0.0.1 / 169.254.169.254 / user@evil.com) 全部拒绝
[13] 删除有 Edict 引用的凭证 → 阻止并提示
[14] pytest tests/ 全绿，覆盖率 >= 80%
[15] ruff + black + isort + mypy 全绿
```

---

## 12. Follow-ups（显式延后，不阻塞本 PR）

- 凭证自动轮换 / 到期告警
- 凭证版本化（保留历史 value）
- WebSocket / SSE 长连接支持
- PDF / 二进制下载（> 1MB 场景）
- L5 交互式浏览器（通过 MCP 外挂 `browser-use`）
- L6 入站 webhook / RSS 订阅
- 跨 Edict 共享的速率限制（全局 QPS）
- 凭证使用频次报表（ops 视角）

---

## 13. 与现有 spec 的关系

| 内容 | 归属文档 |
|------|---------|
| ToolTier 扩展、NetworkPolicy 三档、SSRF Guard、SharedHttpClient、RateLimiter、FetchRouter、5 个既有 engine | `2026-04-21-web-access-tools-design.md`（不变） |
| L1 api_request、L4 web_extract、凭证层、藏兵阁 UI、写方法审批、PR 组织 | 本文档 |
| 最终实施 plan | 待生成 `2026-04-22-external-network-capability-expansion.md`（合并 L2/L3 剩余 Task 16-20 与本文档新增） |
