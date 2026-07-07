# 鸿胪寺对外网络通讯能力实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为天枢六部官员新增 `web_fetch` 与 `web_search` 两个工具，统一由"鸿胪寺"官署（`src/tianshu/tools/hongluisi/`）承接所有对外网络 I/O，支持本地 / Jina Reader / Firecrawl 三层 fetch 自动 fallback 与 Tavily / Jina Search 两种 provider，并附 SSRF 防护、限流、错误脱敏、per-Edict override。

**Architecture:** 在现有 `ToolRegistry` + `PolicyEngine` + `PolicyProfile` 架构上水平扩展 —— 新增 `T2_NETWORK` tier、`NetworkPolicy` 子配置、`NetworkSafetyRule` policy rule、`FetchRouter` + `SharedHttpClient` 基础设施。LLM 接口保持一致（不暴露 engine/provider），选择逻辑全部下沉到 profile + Edict runtime override。

**Tech Stack:** Python 3.12+, httpx, trafilatura, cachetools, pydantic, asyncio

**Tier 决策:** ☑ 扩展方案（新增 T2_NETWORK=2，原 T2_WRITE→T3，原 T3_DANGEROUS→T4） / ☐ 保守方案
— 2026-04-21 评估：src/ 下 `T2_WRITE|T3_DANGEROUS` 共 14 处引用（<15 阈值），采用扩展方案，语义更清晰。

**User preference:** 功能优先，测试最后补 —— 本 plan 前 19 个任务只实现功能（不写测试），最后的 Task 20 统一补齐单元 / 集成 / 契约测试。

**Spec:** `docs/superpowers/specs/2026-04-21-web-access-tools-design.md`

---

## File Structure Overview

### 新增文件

```
src/tianshu/tools/hongluisi/
├── __init__.py                       # 公共入口：register_hongluisi(registry, ...)
├── policy.py                         # NetworkPolicy dataclass
├── ssrf_guard.py                     # URL 校验 / SSRFViolation
├── markdown_extract.py               # trafilatura 包装
├── http_client.py                    # SharedHttpClient 单例
├── rate_limiter.py                   # per-(edict_id, tool) 令牌桶
├── router.py                         # FetchRouter + FetchOutcome + FetchAttempt
├── engine_registry.py                # engine 注册表（env 决定）
└── engines/
    ├── __init__.py                   # FetchEngine / SearchEngine Protocol
    ├── local_fetch.py
    ├── jina_reader.py
    ├── firecrawl.py
    ├── tavily.py
    └── jina_search.py

src/tianshu/tools/policy_rules/
└── network_safety.py                 # 新增 NetworkSafetyRule（priority=75）

tests/tools/hongluisi/
├── test_ssrf_guard.py
├── test_rate_limiter.py
├── test_markdown_extract.py
├── test_router.py
├── test_web_fetch_integration.py
├── test_web_search_integration.py
└── cassettes/                        # pytest-vcr recorded responses
```

### 修改文件

- `pyproject.toml` — 新增 `trafilatura>=1.12`、`cachetools>=5.3` 依赖
- `src/tianshu/tools/types.py` — `ToolTier` 扩展 `T2_NETWORK`
- `src/tianshu/tools/policy_profile.py` — 新增 `NetworkPolicy` 字段 + 3 档 `network_*` 预设
- `src/tianshu/tools/policy_rules/__init__.py` — 在 `build_default_rules()` 中挂载 `NetworkSafetyRule`
- `src/tianshu/tools/builtins.py` — `register_builtins()` 末尾调用 `register_hongluisi()`
- `src/tianshu/models/edict.py` — `EdictRuntime` 新增 `fetch_engine_override` / `search_provider_override`
- `src/tianshu/models/api.py` — `EdictRuntimeRequest` 同步新增两字段
- `src/tianshu/persona/prompt_builder.py` — 工具清单描述补充
- `web/src/pages/EdictCreatePage.tsx`（或等价） — 两个下拉框
- `README.md` + `docs/impl/skills.md` — 工具清单与使用说明

---

## Task 0: 决策 Tier 扩展方案

**Files:**
- 评估：`src/tianshu/tools/types.py`、`src/tianshu/tools/policy_rules/default_tier.py`、`src/tianshu/tools/registry.py`、`src/tianshu/tools/builtins.py`、`src/tianshu/tools/policy_profile.py` 对 `T2_WRITE` / `T3_DANGEROUS` 的引用

- [ ] **Step 1: 评估影响面**

Run:
```bash
grep -rn "T2_WRITE\|T3_DANGEROUS" src/ --include="*.py" | wc -l
```

Expected: ~10 处引用。如果 <15 处 → 走**扩展方案**（新增 `T2_NETWORK=2`，原 `T2_WRITE` 提升为 `T3_WRITE=3`，原 `T3_DANGEROUS` 提升为 `T4_DANGEROUS=4`）。若 >15 处 → 走**保守方案**（沿用 `T2_WRITE`，让 `T2_WRITE` 同时涵盖"外部读 + 外部写"）。

- [ ] **Step 2: 记录决策**

在本 plan 顶部（"Tech Stack" 下一行）追加一行：
```markdown
**Tier 决策:** ☐ 扩展方案（新增 T2_NETWORK） / ☐ 保守方案（沿用 T2_WRITE）
```
选中其中一项。

> **本 plan 假设采用扩展方案。** 若决策为保守方案，Task 1 跳过并把后续所有 `ToolTier.T2_NETWORK` 替换为 `ToolTier.T2_WRITE`。

- [ ] **Step 3: Commit 决策记录**

```bash
git add docs/superpowers/plans/2026-04-21-web-access-tools.md
git commit -m "docs(plan): tier 扩展方案决策"
```

---

## Task 1: 扩展 ToolTier

**Files:**
- Modify: `src/tianshu/tools/types.py`
- Modify: `src/tianshu/tools/policy_rules/default_tier.py`
- Modify: `src/tianshu/tools/registry.py:74-81`
- Modify: `src/tianshu/tools/policy_profile.py:53`
- Modify: `src/tianshu/tools/builtins.py:73`
- Modify: `src/tianshu/executor/policy_hook.py:55,59`
- Modify: `src/tianshu/executor/agent.py:357`

- [ ] **Step 1: 更新 ToolTier 枚举**

Modify `src/tianshu/tools/types.py:43-54` —— 将原枚举替换为：

```python
class ToolTier(IntEnum):
    """工具权限 tier，数值越大越危险。

    与 PolicyEngine 协作：T0 直接快路径放行，T1+ 进入 hook chain
    由 PolicyEngine 决策。spec: Section 2 + 2026-04-21 web access。
    """

    T0_READONLY = 0          # 只读 / 无副作用
    T1_WORKSPACE = 1         # workspace 内写
    T2_NETWORK = 2           # 外部读（SSRF 风险）
    T3_WRITE = 3             # 外部写 / 可逆副作用（原 T2_WRITE）
    T4_DANGEROUS = 4         # 危险 / 不可逆（原 T3_DANGEROUS）
```

- [ ] **Step 2: 同步 registry.py 的 fallback tier**

Modify `src/tianshu/tools/registry.py:74-81` —— 把所有 `T3_DANGEROUS` 引用改为 `T4_DANGEROUS`。该文件内已有的 `ToolTier` import 无需改动。验证 tier 合法范围 `(0, 1, 2, 3)` 更新为 `(0, 1, 2, 3, 4)`：

```python
        if defn.tier is None or defn.tier not in (0, 1, 2, 3, 4):
            logger.error(
                "[TOOL] %s has invalid tier=%r, downgrading to T4_DANGEROUS",
                name, defn.tier,
            )
            defn = defn.model_copy(update={"tier": ToolTier.T4_DANGEROUS.value})
```

- [ ] **Step 3: 同步其他引用**

逐一替换：
- `src/tianshu/tools/builtins.py:73` `ToolTier.T3_DANGEROUS.value` → `ToolTier.T4_DANGEROUS.value`
- `src/tianshu/tools/policy_profile.py:53` `ToolTier.T2_WRITE.value` → `ToolTier.T3_WRITE.value`
- `src/tianshu/tools/policy_rules/default_tier.py:36,42` 同步两处 `T3_DANGEROUS` → `T4_DANGEROUS`，`T2_WRITE` → `T3_WRITE`（注意 reason 字符串里的 tier 名称一并更新）
- `src/tianshu/executor/policy_hook.py:55,59` 两处 `T3_DANGEROUS` → `T4_DANGEROUS`
- `src/tianshu/executor/agent.py:357` `T3_DANGEROUS` → `T4_DANGEROUS`

Run:
```bash
grep -rn "T2_WRITE\|T3_DANGEROUS" src/ --include="*.py"
```

Expected: 无输出（全部替换完毕）

- [ ] **Step 4: 跑一次现有测试确保不回归**

Run:
```bash
pytest tests/ -x --tb=short -q 2>&1 | tail -30
```

Expected: 原有 tier 相关测试仍通过；若有 hardcoded `T2_WRITE` / `T3_DANGEROUS` 字符串的断言失败，更新为新名称。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/tools/types.py src/tianshu/tools/registry.py src/tianshu/tools/builtins.py \
        src/tianshu/tools/policy_profile.py src/tianshu/tools/policy_rules/default_tier.py \
        src/tianshu/executor/policy_hook.py src/tianshu/executor/agent.py
git commit -m "feat(tools): extend ToolTier with T2_NETWORK"
```

---

## Task 2: 新增 NetworkPolicy + 3 档预设

**Files:**
- Create: `src/tianshu/tools/hongluisi/__init__.py`（占位）
- Create: `src/tianshu/tools/hongluisi/policy.py`
- Modify: `src/tianshu/tools/policy_profile.py`

- [ ] **Step 1: 创建 hongluisi 包**

Create `src/tianshu/tools/hongluisi/__init__.py`:

```python
"""鸿胪寺 — 天枢外朝负责对外网络通讯的官署。

所有对外 HTTP/I/O 必须经此官署；其他部门禁止直接 socket。

公共入口：register_hongluisi()
"""

from tianshu.tools.hongluisi.policy import NetworkPolicy

__all__ = ["NetworkPolicy"]
```

- [ ] **Step 2: 创建 NetworkPolicy**

Create `src/tianshu/tools/hongluisi/policy.py`:

```python
"""NetworkPolicy — 鸿胪寺的 per-Edict 策略配置。

Spec Section 3.4。作为 PolicyProfile 的子字段嵌入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NetworkPolicy:
    """对外网络通讯策略。

    - fetch_engines: 按优先级排序的 engine 名字（length=1 即钉死）
    - fallback_mode: 什么情况下跳下一个 engine
        - "none": 只用 fetch_engines[0]
        - "on_error_or_empty": 硬错误 / 4xx-5xx / 空内容 都 fallback
    - search_provider: 单 provider，None 表示禁用 search
    """

    fetch_engines: tuple[str, ...] = ("local",)
    fallback_mode: Literal["none", "on_error_or_empty"] = "none"
    search_provider: Literal["tavily", "jina"] | None = None
    max_fallback_depth: int = 3
    web_fetch_rate_per_min: int = 20
    web_search_rate_per_min: int = 10


# 三档预设 —— 由 PolicyProfile 引用
NETWORK_OFFLINE = NetworkPolicy(
    fetch_engines=("local",),
    fallback_mode="none",
    search_provider=None,
)

NETWORK_DEFAULT = NetworkPolicy(
    fetch_engines=("local", "jina"),
    fallback_mode="on_error_or_empty",
    search_provider="tavily",
)

NETWORK_RESEARCH = NetworkPolicy(
    fetch_engines=("local", "jina", "firecrawl"),
    fallback_mode="on_error_or_empty",
    search_provider="tavily",
)
```

- [ ] **Step 3: 扩展 PolicyProfile**

Modify `src/tianshu/tools/policy_profile.py:24-40`（`PolicyProfile` dataclass）—— 增加 `network` 字段 + 更新 3 档 `BUILTIN_TEMPLATES`：

```python
# 在 from tianshu.tools.policy_store 之前加一行
from tianshu.tools.hongluisi.policy import (
    NetworkPolicy, NETWORK_OFFLINE, NETWORK_DEFAULT, NETWORK_RESEARCH,
)

@dataclass(frozen=True)
class PolicyProfile:
    allowed_paths: tuple[str, ...] = ()
    allowed_bash_prefixes: tuple[str, ...] = ()
    tier_overrides: dict[str, int] = field(default_factory=dict)
    auto_approve_max_tier: int = ToolTier.T1_WORKSPACE.value
    expires_after_seconds: int | None = None
    template_name: str | None = None
    network: NetworkPolicy = field(default_factory=NetworkPolicy)  # 新增
```

同步 `BUILTIN_TEMPLATES` 三档：

```python
BUILTIN_TEMPLATES: dict[str, PolicyProfile] = {
    "safe-explore": PolicyProfile(
        allowed_paths=(),
        allowed_bash_prefixes=(),
        auto_approve_max_tier=ToolTier.T0_READONLY.value,
        template_name="safe-explore",
        network=NETWORK_OFFLINE,  # 只本地 fetch、禁 search
    ),
    "refactor-in-place": PolicyProfile(
        allowed_paths=("**/*",),
        allowed_bash_prefixes=("git status", "git diff"),
        auto_approve_max_tier=ToolTier.T1_WORKSPACE.value,
        template_name="refactor-in-place",
        network=NETWORK_DEFAULT,  # local + Jina fallback，Tavily 搜索
    ),
    "trusted-automation": PolicyProfile(
        allowed_paths=("**/*",),
        allowed_bash_prefixes=("git ", "pytest", "ruff", "black", "mypy"),
        auto_approve_max_tier=ToolTier.T3_WRITE.value,
        template_name="trusted-automation",
        network=NETWORK_RESEARCH,  # 含 Firecrawl
    ),
}
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/tools/hongluisi/__init__.py src/tianshu/tools/hongluisi/policy.py \
        src/tianshu/tools/policy_profile.py
git commit -m "feat(hongluisi): NetworkPolicy + 3 preset profiles"
```

---

## Task 3: Edict Runtime Override 字段

**Files:**
- Modify: `src/tianshu/models/edict.py:43-53`
- Modify: `src/tianshu/models/api.py:18-25`

- [ ] **Step 1: EdictRuntime 新增字段**

Modify `src/tianshu/models/edict.py:43-53`（`EdictRuntime` 类）：

```python
class EdictRuntime(BaseModel):
    timeout_seconds: int = 300
    max_iterations: int = 20
    max_concurrency: int = 1
    retry_limit: int = 0
    token_budget: int | None = None
    cost_budget_cny: float | None = None
    approval_required_tools: list[str] = Field(default_factory=list)
    # Spec Section 5: Policy Profile 预配权限
    policy_profile: PolicyProfilePayload | None = None
    tier_overrides: dict[str, int] = Field(default_factory=dict)
    # 2026-04-21 web access: 钉死 engine / provider，存在则强制关闭 fallback
    fetch_engine_override: str | None = None
    search_provider_override: str | None = None
```

- [ ] **Step 2: EdictRuntimeRequest 同步**

Modify `src/tianshu/models/api.py:18-25`（`EdictRuntimeRequest` 类）：

```python
class EdictRuntimeRequest(BaseModel):
    timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    max_iterations: int | None = Field(default=None, ge=1, le=200)
    max_concurrency: int | None = Field(default=None, ge=1, le=8)
    retry_limit: int | None = Field(default=None, ge=0, le=10)
    token_budget: int | None = None
    cost_budget_cny: float | None = None
    fetch_engine_override: str | None = Field(
        default=None,
        description="Pin web_fetch to specific engine: local | jina | firecrawl",
    )
    search_provider_override: str | None = Field(
        default=None,
        description="Pin web_search to specific provider: tavily | jina",
    )
```

- [ ] **Step 3: 确保 api → model 转换带上新字段**

Run:
```bash
grep -n "EdictRuntime\|runtime=" src/tianshu/gateway/api.py | head -10
```

如果存在 `EdictRuntime(**request.runtime.model_dump())` 类的转换，新字段会自动带上；否则显式补充两字段转换。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/models/edict.py src/tianshu/models/api.py
git commit -m "feat(edict): add fetch_engine_override / search_provider_override"
```

---

## Task 4: SSRF Guard

**Files:**
- Create: `src/tianshu/tools/hongluisi/ssrf_guard.py`

- [ ] **Step 1: 创建 ssrf_guard 模块**

Create `src/tianshu/tools/hongluisi/ssrf_guard.py`:

```python
"""URL SSRF 防护。

Spec Section 5.1。对每一个要访问的 URL 做：
1. 协议 / 端口 / hostname 字面黑名单
2. DNS 解析后逐一校验 IP
3. 用户信息剥离后重校验

失败时抛 SSRFViolation(code, internal_reason)：
- code 可安全透传给 LLM（脱敏常量）
- internal_reason 仅在服务端审计日志记录
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS: frozenset[int | None] = frozenset({None, 80, 443, 8080, 8443})

HOSTNAME_DENY_SUFFIXES: tuple[str, ...] = (
    ".local",
    ".internal",
    ".corp",
)
HOSTNAME_DENY_EXACT: frozenset[str] = frozenset({"localhost"})

# 显式额外拒绝（有些地址虽然不是标准 private 但风险大）
EXTRA_DENY_CIDRS: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("169.254.169.254/32"),   # AWS / GCP metadata
    ipaddress.ip_network("100.64.0.0/10"),        # CGNAT
    ipaddress.ip_network("fd00::/8"),             # IPv6 ULA
)


@dataclass(frozen=True)
class SSRFViolation(Exception):
    code: str               # 可透传给 LLM
    internal_reason: str    # 审计日志

    def __str__(self) -> str:
        return self.code


async def validate_url(url: str) -> str:
    """校验 URL 可访问；返回剥离 userinfo 后的干净 URL。失败抛 SSRFViolation。"""
    parsed = urlparse(url)

    # 1. scheme
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SSRFViolation("ssrf_bad_scheme", f"scheme={parsed.scheme!r}")
    # 2. host
    host = parsed.hostname
    if not host:
        raise SSRFViolation("ssrf_no_host", "hostname empty")
    # 3. hostname 字面黑名单
    lowered = host.lower()
    if lowered in HOSTNAME_DENY_EXACT:
        raise SSRFViolation("ssrf_bad_hostname", f"literal={lowered}")
    for suffix in HOSTNAME_DENY_SUFFIXES:
        if lowered.endswith(suffix):
            raise SSRFViolation("ssrf_bad_hostname", f"suffix={suffix}")
    # 4. port
    port = parsed.port
    if port not in ALLOWED_PORTS:
        raise SSRFViolation("ssrf_bad_port", f"port={port}")

    # 5. DNS 解析 + IP 校验（逐一；任一命中即拒）
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(host, None)
        )
    except socket.gaierror as e:
        raise SSRFViolation("ssrf_dns_failed", f"getaddrinfo: {e}")

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SSRFViolation("ssrf_private_ip", f"resolved to {ip_str}")
        for net in EXTRA_DENY_CIDRS:
            if ip in net:
                raise SSRFViolation("ssrf_private_ip", f"in {net}")

    # 6. 剥离 userinfo 重组
    if parsed.username or parsed.password:
        netloc = host
        if port:
            netloc = f"{host}:{port}"
        cleaned = urlunparse((
            parsed.scheme, netloc, parsed.path,
            parsed.params, parsed.query, parsed.fragment,
        ))
        return cleaned
    return url
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/ssrf_guard.py
git commit -m "feat(hongluisi): ssrf guard (scheme/port/host/dns/userinfo)"
```

---

## Task 5: Markdown 提取 + 新依赖

**Files:**
- Modify: `pyproject.toml:10-24`
- Create: `src/tianshu/tools/hongluisi/markdown_extract.py`

- [ ] **Step 1: 添加依赖**

Modify `pyproject.toml:10-24`（`dependencies = [...]`），在 `"croniter>=2.0",` 之后追加两行：

```toml
    "trafilatura>=1.12",
    "cachetools>=5.3",
```

- [ ] **Step 2: 安装依赖**

Run:
```bash
pip install -e '.[dev,cli]'
```

Expected: trafilatura / cachetools 安装成功。

- [ ] **Step 3: 创建 markdown_extract.py**

Create `src/tianshu/tools/hongluisi/markdown_extract.py`:

```python
"""HTML → Markdown 提取。仅 LocalFetchEngine 使用。

Spec Section 5.3。
"""

from __future__ import annotations

import logging

import trafilatura

logger = logging.getLogger(__name__)

MIN_EXTRACTED_LEN = 500   # 小于此数视为 empty（router 判断是否 fallback）


def extract_markdown(html: str, url: str | None = None) -> str:
    """从 HTML 提取可读 Markdown。失败或几乎空时返回空串。"""
    if not html or not html.strip():
        return ""
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=True,
            favor_precision=True,
        )
    except Exception as e:
        logger.warning("trafilatura.extract raised: %s", e)
        return ""
    return extracted or ""


def is_empty(content: str) -> bool:
    return len(content.strip()) < MIN_EXTRACTED_LEN
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/tianshu/tools/hongluisi/markdown_extract.py
git commit -m "feat(hongluisi): markdown extract (trafilatura) + deps"
```

---

## Task 6: Shared HTTP Client

**Files:**
- Create: `src/tianshu/tools/hongluisi/http_client.py`

- [ ] **Step 1: 创建 SharedHttpClient**

Create `src/tianshu/tools/hongluisi/http_client.py`:

```python
"""共享 httpx.AsyncClient + TTL 响应缓存。单例。

Spec Section 5.2。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from cachetools import TTLCache

from tianshu.tools.hongluisi.ssrf_guard import validate_url

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1 * 1024 * 1024   # 1 MB
REQUEST_TIMEOUT_SEC = 15.0
CONNECT_TIMEOUT_SEC = 5.0
CACHE_MAXSIZE = 32
CACHE_TTL_SEC = 300


class BodyTooLarge(Exception):
    def __init__(self, bytes_read: int) -> None:
        super().__init__(f"body exceeded {MAX_BODY_BYTES} bytes")
        self.bytes_read = bytes_read


class SharedHttpClient:
    """跨 Edict 共享连接池、DNS 缓存、TTL 响应缓存。"""

    _instance: "SharedHttpClient | None" = None

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SEC, connect=CONNECT_TIMEOUT_SEC),
            follow_redirects=True,
            max_redirects=5,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            headers={"User-Agent": "tianshu-hongluisi/0.1 (+https://github.com/)"},
            event_hooks={"response": [self._on_redirect]},
        )
        self._cache: TTLCache = TTLCache(maxsize=CACHE_MAXSIZE, ttl=CACHE_TTL_SEC)

    @classmethod
    def instance(cls) -> "SharedHttpClient":
        if cls._instance is None:
            cls._instance = SharedHttpClient()
        return cls._instance

    @classmethod
    async def reset(cls) -> None:
        if cls._instance is not None:
            await cls._instance._client.aclose()
            cls._instance = None

    async def _on_redirect(self, response: httpx.Response) -> None:
        if response.is_redirect:
            loc = response.headers.get("location")
            if loc:
                await validate_url(loc)

    async def get_cached(
        self, url: str, engine: str
    ) -> tuple[str, dict[str, Any], bool]:
        """返回 (body_text, meta, cached_flag)。未命中时做真实 GET。"""
        key = (url, engine)
        if key in self._cache:
            body, meta = self._cache[key]
            return body, meta, True

        bytes_read = 0
        chunks: list[bytes] = []
        async with self._client.stream("GET", url) as resp:
            # 预检 Content-Length
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
                raise BodyTooLarge(int(cl))
            async for chunk in resp.aiter_bytes():
                bytes_read += len(chunk)
                if bytes_read > MAX_BODY_BYTES:
                    raise BodyTooLarge(bytes_read)
                chunks.append(chunk)
            body_bytes = b"".join(chunks)
            body = body_bytes.decode(
                resp.encoding or "utf-8", errors="replace"
            )
            meta: dict[str, Any] = {
                "http_status": resp.status_code,
                "content_type": resp.headers.get("content-type", ""),
                "final_url": str(resp.url),
                "bytes_fetched": bytes_read,
            }
        self._cache[key] = (body, meta)
        return body, meta, False

    async def post_json(
        self,
        url: str,
        json_body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """返回 (parsed_json, http_status)。不缓存。"""
        resp = await self._client.post(url, json=json_body, headers=headers or {})
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:500]}
        return data, resp.status_code
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/http_client.py
git commit -m "feat(hongluisi): SharedHttpClient singleton + TTL cache"
```

---

## Task 7: Engine Protocol & FetchOutcome

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/__init__.py`

- [ ] **Step 1: 定义协议与数据类**

Create `src/tianshu/tools/hongluisi/engines/__init__.py`:

```python
"""Fetch / Search Engine 协议定义 + 共享数据类。

Spec Section 5.3。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


FetchStatus = Literal["ok", "empty", "error"]


@dataclass(frozen=True)
class FetchOutcome:
    content: str              # Markdown；失败/empty 时可能为空
    status: FetchStatus
    http_status: int | None
    reason: str | None
    bytes_fetched: int
    final_url: str | None
    cached: bool = False


@dataclass(frozen=True)
class FetchAttempt:
    engine: str
    status: str               # "ok" | "empty" | "error" | "skipped"
    reason: str | None


class FetchEngine(Protocol):
    name: str

    async def fetch(self, url: str) -> FetchOutcome: ...


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float | None = None


@dataclass(frozen=True)
class SearchOutcome:
    results: tuple[SearchResult, ...]
    raw_api_meta: dict                # provider 返回的 usage / response_time 等


class SearchEngine(Protocol):
    name: str

    async def search(self, query: str, max_results: int) -> SearchOutcome: ...
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/__init__.py
git commit -m "feat(hongluisi): FetchEngine / SearchEngine protocols"
```

---

## Task 8: LocalFetchEngine

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/local_fetch.py`

- [ ] **Step 1: 创建 local engine**

Create `src/tianshu/tools/hongluisi/engines/local_fetch.py`:

```python
"""Local fetch engine：httpx + trafilatura。Spec Section 5.3。"""

from __future__ import annotations

import logging

import httpx

from tianshu.tools.hongluisi.engines import FetchEngine, FetchOutcome
from tianshu.tools.hongluisi.http_client import BodyTooLarge, SharedHttpClient
from tianshu.tools.hongluisi.markdown_extract import extract_markdown, is_empty
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

SUPPORTED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/xhtml",
)


class LocalFetchEngine:
    name = "local"

    async def fetch(self, url: str) -> FetchOutcome:
        try:
            clean_url = await validate_url(url)
        except SSRFViolation as v:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=v.code, bytes_fetched=0, final_url=None,
            )
        client = SharedHttpClient.instance()
        try:
            body, meta, cached = await client.get_cached(clean_url, engine=self.name)
        except BodyTooLarge as e:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=f"response_too_large:{e.bytes_read}",
                bytes_fetched=e.bytes_read, final_url=None,
            )
        except httpx.TimeoutException:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason="timeout", bytes_fetched=0, final_url=None,
            )
        except httpx.HTTPError as e:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=f"http_error:{type(e).__name__}",
                bytes_fetched=0, final_url=None,
            )

        http_status = int(meta["http_status"])
        if http_status >= 400:
            return FetchOutcome(
                content="", status="error", http_status=http_status,
                reason=f"http_status:{http_status}",
                bytes_fetched=meta["bytes_fetched"],
                final_url=meta["final_url"], cached=cached,
            )

        content_type = meta.get("content_type", "").lower()
        if content_type and not any(t in content_type for t in SUPPORTED_CONTENT_TYPES):
            return FetchOutcome(
                content="", status="error", http_status=http_status,
                reason=f"unsupported_content_type:{content_type}",
                bytes_fetched=meta["bytes_fetched"],
                final_url=meta["final_url"], cached=cached,
            )

        # text/plain / text/markdown 直接当 Markdown，不过 trafilatura
        if "html" in content_type or "xhtml" in content_type:
            markdown = extract_markdown(body, url=clean_url)
        else:
            markdown = body

        status = "empty" if is_empty(markdown) else "ok"
        return FetchOutcome(
            content=markdown, status=status, http_status=http_status,
            reason=None if status == "ok" else "extracted_empty",
            bytes_fetched=meta["bytes_fetched"],
            final_url=meta["final_url"], cached=cached,
        )
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/local_fetch.py
git commit -m "feat(hongluisi): LocalFetchEngine (httpx + trafilatura)"
```

---

## Task 9: JinaReaderEngine

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/jina_reader.py`

- [ ] **Step 1: 创建 Jina Reader engine**

Create `src/tianshu/tools/hongluisi/engines/jina_reader.py`:

```python
"""Jina Reader engine：r.jina.ai 代理。Spec Section 5.3。"""

from __future__ import annotations

import logging
import os

import httpx

from tianshu.tools.hongluisi.engines import FetchEngine, FetchOutcome
from tianshu.tools.hongluisi.http_client import BodyTooLarge, SharedHttpClient
from tianshu.tools.hongluisi.markdown_extract import is_empty
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

JINA_READER_BASE = "https://r.jina.ai"


class JinaReaderEngine:
    name = "jina"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def fetch(self, url: str) -> FetchOutcome:
        # 先对目标 URL 做 SSRF 校验 —— 代理不是安全边界
        try:
            clean_url = await validate_url(url)
        except SSRFViolation as v:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=v.code, bytes_fetched=0, final_url=None,
            )
        proxied = f"{JINA_READER_BASE}/{clean_url}"
        client = SharedHttpClient.instance()
        try:
            if self._api_key:
                # 带 key：通过 _client 直接请求以附加 header
                resp = await client._client.get(
                    proxied,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "text/plain",
                    },
                )
                body = resp.text
                http_status = resp.status_code
                final = str(resp.url)
                bytes_read = len(body.encode("utf-8", errors="ignore"))
                cached = False
            else:
                body, meta, cached = await client.get_cached(proxied, engine=self.name)
                http_status = int(meta["http_status"])
                final = meta["final_url"]
                bytes_read = meta["bytes_fetched"]
        except BodyTooLarge as e:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=f"response_too_large:{e.bytes_read}",
                bytes_fetched=e.bytes_read, final_url=None,
            )
        except httpx.TimeoutException:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason="timeout", bytes_fetched=0, final_url=None,
            )
        except httpx.HTTPError as e:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=f"http_error:{type(e).__name__}",
                bytes_fetched=0, final_url=None,
            )

        if http_status >= 400:
            return FetchOutcome(
                content="", status="error", http_status=http_status,
                reason=f"http_status:{http_status}",
                bytes_fetched=bytes_read, final_url=final, cached=cached,
            )
        status = "empty" if is_empty(body) else "ok"
        return FetchOutcome(
            content=body, status=status, http_status=http_status,
            reason=None if status == "ok" else "jina_empty",
            bytes_fetched=bytes_read, final_url=final, cached=cached,
        )


def build_jina_reader() -> JinaReaderEngine | None:
    """按 env 构造；key 可选，无 key 也能用（20 req/min）。"""
    key = os.getenv("TIANSHU_JINA_API_KEY")
    return JinaReaderEngine(api_key=key)
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/jina_reader.py
git commit -m "feat(hongluisi): JinaReaderEngine"
```

---

## Task 10: FirecrawlEngine

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/firecrawl.py`

- [ ] **Step 1: 创建 Firecrawl engine**

Create `src/tianshu/tools/hongluisi/engines/firecrawl.py`:

```python
"""Firecrawl engine：api.firecrawl.dev/v1/scrape。Spec Section 5.3。"""

from __future__ import annotations

import logging
import os

import httpx

from tianshu.tools.hongluisi.engines import FetchEngine, FetchOutcome
from tianshu.tools.hongluisi.http_client import SharedHttpClient
from tianshu.tools.hongluisi.markdown_extract import is_empty
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"


class FirecrawlEngine:
    name = "firecrawl"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("FirecrawlEngine requires api_key")
        self._api_key = api_key

    async def fetch(self, url: str) -> FetchOutcome:
        try:
            clean_url = await validate_url(url)
        except SSRFViolation as v:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=v.code, bytes_fetched=0, final_url=None,
            )
        client = SharedHttpClient.instance()
        body = {
            "url": clean_url,
            "formats": ["markdown"],
            "onlyMainContent": True,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            data, http_status = await client.post_json(
                FIRECRAWL_ENDPOINT, body, headers=headers
            )
        except httpx.TimeoutException:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason="timeout", bytes_fetched=0, final_url=None,
            )
        except httpx.HTTPError as e:
            return FetchOutcome(
                content="", status="error", http_status=None,
                reason=f"http_error:{type(e).__name__}",
                bytes_fetched=0, final_url=None,
            )
        if http_status >= 400 or not data.get("success", True):
            return FetchOutcome(
                content="", status="error", http_status=http_status,
                reason=f"firecrawl_error:{http_status}",
                bytes_fetched=0, final_url=None,
            )
        markdown = (data.get("data") or {}).get("markdown", "")
        bytes_read = len(markdown.encode("utf-8", errors="ignore"))
        status = "empty" if is_empty(markdown) else "ok"
        return FetchOutcome(
            content=markdown, status=status, http_status=http_status,
            reason=None if status == "ok" else "firecrawl_empty",
            bytes_fetched=bytes_read, final_url=clean_url,
        )


def build_firecrawl() -> FirecrawlEngine | None:
    key = os.getenv("TIANSHU_FIRECRAWL_API_KEY")
    if not key:
        return None
    return FirecrawlEngine(api_key=key)
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/firecrawl.py
git commit -m "feat(hongluisi): FirecrawlEngine"
```

---

## Task 11: TavilyEngine (Search)

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/tavily.py`

- [ ] **Step 1: 创建 Tavily engine**

Create `src/tianshu/tools/hongluisi/engines/tavily.py`:

```python
"""Tavily search engine：api.tavily.com/search。Spec Section 5.3."""

from __future__ import annotations

import logging
import os

import httpx

from tianshu.tools.hongluisi.engines import SearchEngine, SearchOutcome, SearchResult
from tianshu.tools.hongluisi.http_client import SharedHttpClient

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilySearchEngine:
    name = "tavily"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("TavilySearchEngine requires api_key")
        self._api_key = api_key

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        client = SharedHttpClient.instance()
        body = {
            "api_key": self._api_key,        # Tavily 支持 body 里传 key
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_raw_content": False,
        }
        try:
            data, http_status = await client.post_json(TAVILY_ENDPOINT, body)
        except httpx.HTTPError as e:
            raise RuntimeError(f"tavily_http_error:{type(e).__name__}") from e

        if http_status >= 400:
            raise RuntimeError(f"tavily_status:{http_status}")

        results: list[SearchResult] = []
        for item in (data.get("results") or []):
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    score=item.get("score"),
                )
            )
        meta = {
            "response_time": data.get("response_time"),
            "usage": data.get("usage"),
        }
        return SearchOutcome(results=tuple(results), raw_api_meta=meta)


def build_tavily() -> TavilySearchEngine | None:
    key = os.getenv("TIANSHU_TAVILY_API_KEY")
    if not key:
        return None
    return TavilySearchEngine(api_key=key)
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/tavily.py
git commit -m "feat(hongluisi): TavilySearchEngine"
```

---

## Task 12: JinaSearchEngine

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/jina_search.py`

- [ ] **Step 1: 创建 Jina Search engine**

Create `src/tianshu/tools/hongluisi/engines/jina_search.py`:

```python
"""Jina Search engine：s.jina.ai/?q=<query>。Spec Section 5.3。"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote_plus

import httpx

from tianshu.tools.hongluisi.engines import SearchEngine, SearchOutcome, SearchResult
from tianshu.tools.hongluisi.http_client import SharedHttpClient

logger = logging.getLogger(__name__)

JINA_SEARCH_BASE = "https://s.jina.ai"


class JinaSearchEngine:
    name = "jina"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        client = SharedHttpClient.instance()
        url = f"{JINA_SEARCH_BASE}/?q={quote_plus(query)}"
        headers = {"Accept": "text/plain"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            resp = await client._client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise RuntimeError(f"jina_search_http_error:{type(e).__name__}") from e
        if resp.status_code >= 400:
            raise RuntimeError(f"jina_search_status:{resp.status_code}")

        # Jina Search 返回 markdown 文本，每条结果大致是 "[n] Title\nURL\nSnippet" 段
        # 为统一协议，我们把 raw 放在 snippet 里，url / title 留空让上层文本化
        # 简单起见：整段作为一个 SearchResult（title=query，snippet=body）
        body = resp.text.strip()
        results = (
            SearchResult(
                title=f"Jina Search: {query}",
                url=url,
                snippet=body[:4000],  # 防爆
                score=None,
            ),
        )
        return SearchOutcome(
            results=results[:max_results] if max_results < len(results) else results,
            raw_api_meta={"bytes": len(body)},
        )


def build_jina_search() -> JinaSearchEngine | None:
    key = os.getenv("TIANSHU_JINA_API_KEY")
    return JinaSearchEngine(api_key=key)
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/jina_search.py
git commit -m "feat(hongluisi): JinaSearchEngine"
```

---

## Task 13: Engine Registry

**Files:**
- Create: `src/tianshu/tools/hongluisi/engine_registry.py`

- [ ] **Step 1: 构建 engine 注册表**

Create `src/tianshu/tools/hongluisi/engine_registry.py`:

```python
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
        list(fetch), list(search),
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
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/engine_registry.py
git commit -m "feat(hongluisi): engine registry (env-driven)"
```

---

## Task 14: FetchRouter

**Files:**
- Create: `src/tianshu/tools/hongluisi/router.py`

- [ ] **Step 1: 创建路由器**

Create `src/tianshu/tools/hongluisi/router.py`:

```python
"""FetchRouter：按 profile + override 决定 engine 链与 fallback。

Spec Section 5.4。
"""

from __future__ import annotations

import logging

from tianshu.tools.hongluisi.engines import FetchAttempt, FetchEngine, FetchOutcome
from tianshu.tools.hongluisi.policy import NetworkPolicy

logger = logging.getLogger(__name__)


class FetchRouter:
    def __init__(
        self,
        engines: dict[str, FetchEngine],
        policy: NetworkPolicy,
        override: str | None,
    ) -> None:
        self._engines = engines
        if override is not None:
            # 手动钉死 + 强制关闭 fallback
            self._chain: tuple[str, ...] = (override,)
            self._fallback_mode = "none"
        else:
            self._chain = policy.fetch_engines
            self._fallback_mode = policy.fallback_mode
        self._max_depth = policy.max_fallback_depth

    async def dispatch(
        self, url: str
    ) -> tuple[FetchOutcome, list[FetchAttempt]]:
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
                logger.exception("engine %s raised", engine_name)
                outcome = FetchOutcome(
                    content="", status="error", http_status=None,
                    reason=f"engine_exception:{type(e).__name__}",
                    bytes_fetched=0, final_url=None,
                )
            attempts.append(FetchAttempt(
                engine_name, outcome.status, outcome.reason,
            ))
            if outcome.status == "ok":
                return outcome, attempts
            if self._fallback_mode == "none":
                return outcome, attempts
            # on_error_or_empty：empty 和 error 都继续
        if outcome is None:
            outcome = FetchOutcome(
                content="", status="error", http_status=None,
                reason="no_engine_available",
                bytes_fetched=0, final_url=None,
            )
        return outcome, attempts
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/router.py
git commit -m "feat(hongluisi): FetchRouter with fallback"
```

---

## Task 15: Rate Limiter

**Files:**
- Create: `src/tianshu/tools/hongluisi/rate_limiter.py`

- [ ] **Step 1: 创建令牌桶**

Create `src/tianshu/tools/hongluisi/rate_limiter.py`:

```python
"""per-(edict_id, tool_name) 令牌桶。Spec Section 5.6。

简单实现：deque 记录过去 60s 内的调用时间戳；
超过配额时返回 (False, retry_after_sec)。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateCheckResult:
    allowed: bool
    retry_after_sec: float = 0.0


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(
        self, edict_id: str, tool_name: str, rate_per_min: int
    ) -> RateCheckResult:
        key = (edict_id, tool_name)
        now = time.monotonic()
        window_start = now - 60.0
        async with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            if len(bucket) >= rate_per_min:
                oldest = bucket[0]
                retry = max(0.0, (oldest + 60.0) - now)
                return RateCheckResult(allowed=False, retry_after_sec=retry)
            bucket.append(now)
            return RateCheckResult(allowed=True)


# 进程单例
_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/hongluisi/rate_limiter.py
git commit -m "feat(hongluisi): per-(edict,tool) rate limiter"
```

---

## Task 16: web_fetch / web_search 工具注册

**Files:**
- Create: `src/tianshu/tools/hongluisi/tools.py`
- Modify: `src/tianshu/tools/hongluisi/__init__.py`

- [ ] **Step 1: 创建工具实现**

Create `src/tianshu/tools/hongluisi/tools.py`:

```python
"""web_fetch / web_search 工具实现 + 注册入口。

Spec Section 4、6。
"""

from __future__ import annotations

import logging
from typing import Any

from tianshu.tools.hongluisi.engine_registry import (
    build_engines,
    get_fetch_engines_map,
    get_search_providers_map,
)
from tianshu.tools.hongluisi.engines import FetchAttempt, FetchOutcome, SearchOutcome
from tianshu.tools.hongluisi.policy import NetworkPolicy
from tianshu.tools.hongluisi.rate_limiter import get_rate_limiter
from tianshu.tools.hongluisi.router import FetchRouter
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

logger = logging.getLogger(__name__)

WEB_FETCH_MAX_RESULT_CHARS = 16000
WEB_SEARCH_MAX_RESULT_CHARS = 4000


def _extract_runtime_context(
    get_ctx: callable,
) -> tuple[str, NetworkPolicy, str | None, str | None]:
    """从 ambient context 取 edict_id / NetworkPolicy / overrides。

    Tianshu ToolRegistry 目前把 ctx 通过 ContextVar 或参数传入；
    这里抽出接口以便 Task 17 wire。
    """
    ctx = get_ctx()
    edict = ctx.edict
    net = edict.runtime.policy_profile.network if edict.runtime.policy_profile else NetworkPolicy()
    fe_override = edict.runtime.fetch_engine_override
    sp_override = edict.runtime.search_provider_override
    return edict.id, net, fe_override, sp_override


async def web_fetch(url: str, *, edict_id: str, net: NetworkPolicy, fe_override: str | None) -> ToolResult:
    # 限流
    limiter = get_rate_limiter()
    rc = await limiter.check(edict_id, "web_fetch", net.web_fetch_rate_per_min)
    if not rc.allowed:
        return error_result(
            f"rate limit exceeded; retry after {rc.retry_after_sec:.1f}s"
        )

    # 路由 + 执行
    router = FetchRouter(
        engines=get_fetch_engines_map(),
        policy=net,
        override=fe_override,
    )
    outcome, attempts = await router.dispatch(url)

    # 组装 ToolResult
    engine_chain = [
        {"engine": a.engine, "status": a.status, "reason": a.reason}
        for a in attempts
    ]

    if outcome.status == "ok":
        content = outcome.content
        if len(content) > WEB_FETCH_MAX_RESULT_CHARS:
            content = content[:WEB_FETCH_MAX_RESULT_CHARS]
            truncated = True
        else:
            truncated = False
        return ok_result(
            content,
            details={
                "url": url,
                "final_url": outcome.final_url,
                "engine_used": attempts[-1].engine if attempts else None,
                "engine_chain": engine_chain,
                "http_status": outcome.http_status,
                "bytes_fetched": outcome.bytes_fetched,
                "cached": outcome.cached,
                "truncated": truncated,
            },
        )

    # 失败：给 LLM 可决策的错误信息
    lines = ["抓取失败。尝试记录："]
    for i, a in enumerate(attempts, 1):
        lines.append(f"{i}. {a.engine}: {a.status} — {a.reason or 'no reason'}")
    lines.append("")
    lines.append("建议：换一个 URL，或使用 web_search 检索相关内容。")
    return ToolResult(
        content="\n".join(lines),
        details={
            "url": url,
            "engine_chain": engine_chain,
            "suggest": ["web_search"],
        },
        is_error=True,
    )


async def web_search(
    query: str, max_results: int = 5,
    *, edict_id: str, net: NetworkPolicy, sp_override: str | None,
) -> ToolResult:
    # 限流
    limiter = get_rate_limiter()
    rc = await limiter.check(edict_id, "web_search", net.web_search_rate_per_min)
    if not rc.allowed:
        return error_result(
            f"rate limit exceeded; retry after {rc.retry_after_sec:.1f}s"
        )
    provider_name = sp_override or net.search_provider
    if provider_name is None:
        return error_result("search is disabled for this profile")
    providers = get_search_providers_map()
    provider = providers.get(provider_name)
    if provider is None:
        return error_result(
            f"search provider '{provider_name}' not registered (missing env key?)"
        )
    try:
        outcome = await provider.search(query, max_results)
    except Exception as e:
        return error_result(f"search failed: {type(e).__name__}: {e}")

    lines: list[str] = []
    for i, r in enumerate(outcome.results, 1):
        lines.append(f"{i}. {r.title} — {r.url}")
        if r.snippet:
            lines.append(f"   {r.snippet[:300]}")
        if r.score is not None:
            lines.append(f"   (relevance: {r.score:.2f})")
        lines.append("")
    content = "\n".join(lines).strip()
    if len(content) > WEB_SEARCH_MAX_RESULT_CHARS:
        content = content[:WEB_SEARCH_MAX_RESULT_CHARS]
    return ok_result(
        content,
        details={
            "provider": provider_name,
            "query": query,
            "result_count": len(outcome.results),
            "api_meta": outcome.raw_api_meta,
        },
    )


# ------------------ 注册入口 ------------------

WEB_FETCH_DEF = ToolDefinition(
    name="web_fetch",
    description=(
        "Fetch a public web page and return its readable text as Markdown. "
        "Only public URLs are allowed; internal/private IPs are rejected. "
        "Max response body 1 MB; output truncated to 16000 chars. "
        "Use this when you already have a specific URL to read."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Absolute http(s) URL",
            },
        },
        "required": ["url"],
    },
    tier=ToolTier.T2_NETWORK.value,
    max_result_chars=WEB_FETCH_MAX_RESULT_CHARS,
)

WEB_SEARCH_DEF = ToolDefinition(
    name="web_search",
    description=(
        "Search the public web. Returns a ranked list of results "
        "(title / url / snippet). Use web_fetch afterwards to read full content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {
                "type": "integer",
                "minimum": 1, "maximum": 10, "default": 5,
            },
        },
        "required": ["query"],
    },
    tier=ToolTier.T2_NETWORK.value,
    max_result_chars=WEB_SEARCH_MAX_RESULT_CHARS,
)


def register_hongluisi(registry: ToolRegistry, edict_context_resolver: callable) -> None:
    """注册 web_fetch / web_search。
    
    edict_context_resolver() 应返回当前正在执行 tool 的 Edict 对象。
    Tianshu 现有架构通过 ContextVar 传递；在 Task 17 与 app.py wiring 时具体化。
    """
    build_engines()   # env → engine 注册表

    async def _web_fetch(url: str) -> ToolResult:
        edict_id, net, fe_override, _ = _extract_runtime_context(edict_context_resolver)
        return await web_fetch(url, edict_id=edict_id, net=net, fe_override=fe_override)

    async def _web_search(query: str, max_results: int = 5) -> ToolResult:
        edict_id, net, _, sp_override = _extract_runtime_context(edict_context_resolver)
        return await web_search(
            query, max_results,
            edict_id=edict_id, net=net, sp_override=sp_override,
        )

    registry.register("web_fetch", _web_fetch, WEB_FETCH_DEF)
    registry.register("web_search", _web_search, WEB_SEARCH_DEF)
```

- [ ] **Step 2: 更新 `__init__.py` 暴露 register_hongluisi**

Modify `src/tianshu/tools/hongluisi/__init__.py`（覆盖整个文件）:

```python
"""鸿胪寺 — 天枢外朝负责对外网络通讯的官署。

所有对外 HTTP/I/O 必须经此官署；其他部门禁止直接 socket。

公共入口：register_hongluisi(registry, edict_context_resolver)
"""

from tianshu.tools.hongluisi.policy import NetworkPolicy
from tianshu.tools.hongluisi.tools import register_hongluisi

__all__ = ["NetworkPolicy", "register_hongluisi"]
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/hongluisi/tools.py src/tianshu/tools/hongluisi/__init__.py
git commit -m "feat(hongluisi): web_fetch / web_search tool implementation"
```

---

## Task 17: NetworkSafetyRule + 注册到 PolicyEngine

**Files:**
- Create: `src/tianshu/tools/policy_rules/network_safety.py`
- Modify: `src/tianshu/tools/policy_rules/__init__.py`

- [ ] **Step 1: 创建 policy rule**

Create `src/tianshu/tools/policy_rules/network_safety.py`:

```python
"""NetworkSafetyRule — web_fetch / web_search 前置决策。

Spec Section 5.5。
- priority = 75（bash_safety=80 之后、default_tier=10 之前）
- 职责：
  1. web_search 在未配置 provider 的 profile 下 deny
  2. override 引用未注册的 engine / provider 时 deny
  3. web_fetch 的 url 做 SSRF 前置校验（脱敏 code 返回给 LLM）
"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.hongluisi.engine_registry import (
    get_registered_fetch_engines,
    get_registered_search_providers,
)
from tianshu.tools.hongluisi.policy import NetworkPolicy
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url
from tianshu.tools.policy import PolicyContext, PolicyDecision

NETWORK_TOOLS = {"web_fetch", "web_search"}


@dataclass
class NetworkSafetyRule:
    rule_id: str = "network_safety"
    priority: int = 75

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        if ctx.tool_name not in NETWORK_TOOLS:
            return None

        runtime = ctx.edict.runtime
        profile = runtime.policy_profile
        # profile 来自 EdictRuntime.policy_profile（Pydantic），而 BUILTIN_TEMPLATES
        # 是 dataclass —— 两条路径都要兼容，统一通过 getattr 取 network
        net: NetworkPolicy = getattr(profile, "network", None) or NetworkPolicy()

        # 1. web_search：provider 必须存在
        if ctx.tool_name == "web_search":
            sp_override = getattr(runtime, "search_provider_override", None)
            if net.search_provider is None and not sp_override:
                return PolicyDecision(
                    verdict="deny",
                    rule_id=self.rule_id,
                    reason="search disabled for this profile",
                )
            target = sp_override or net.search_provider
            if target not in get_registered_search_providers():
                return PolicyDecision(
                    verdict="deny",
                    rule_id=self.rule_id,
                    reason=f"search provider '{target}' not registered (missing env key?)",
                )

        # 2. web_fetch：override 若指定，engine 必须已注册
        if ctx.tool_name == "web_fetch":
            fe_override = getattr(runtime, "fetch_engine_override", None)
            if fe_override and fe_override not in get_registered_fetch_engines():
                return PolicyDecision(
                    verdict="deny",
                    rule_id=self.rule_id,
                    reason=f"fetch engine '{fe_override}' not registered (missing env key?)",
                )
            # 3. SSRF 前置校验
            url = ctx.args.get("url", "")
            if url:
                try:
                    await validate_url(url)
                except SSRFViolation as v:
                    return PolicyDecision(
                        verdict="deny",
                        rule_id=self.rule_id,
                        reason=v.code,   # 脱敏 code，不含 IP
                        metadata={"internal_reason": v.internal_reason},
                    )

        return PolicyDecision(
            verdict="allow",
            rule_id=self.rule_id,
            reason="passed",
        )
```

- [ ] **Step 2: 注册到 build_default_rules**

Modify `src/tianshu/tools/policy_rules/__init__.py`（整体覆盖）:

```python
"""Built-in policy rules. Spec Section 3."""

from tianshu.tools.policy_rules.approval_required_list import ApprovalRequiredListRule
from tianshu.tools.policy_rules.bash_safety import BashSafetyRule
from tianshu.tools.policy_rules.default_tier import DefaultTierRule
from tianshu.tools.policy_rules.network_safety import NetworkSafetyRule
from tianshu.tools.policy_rules.tier_escalation import TierEscalationRule
from tianshu.tools.policy_rules.workspace_boundary import WorkspaceBoundaryRule

__all__ = [
    "TierEscalationRule",
    "WorkspaceBoundaryRule",
    "BashSafetyRule",
    "NetworkSafetyRule",
    "ApprovalRequiredListRule",
    "DefaultTierRule",
]


def build_default_rules() -> list:
    """返回内建规则的默认实例列表（按优先级顺序）。"""
    return [
        TierEscalationRule(),        # 100
        WorkspaceBoundaryRule(),     # 90
        BashSafetyRule(),            # 80
        NetworkSafetyRule(),         # 75
        ApprovalRequiredListRule(),  # 70
        DefaultTierRule(),           # 10
    ]
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/policy_rules/network_safety.py src/tianshu/tools/policy_rules/__init__.py
git commit -m "feat(policy): NetworkSafetyRule with SSRF pre-check"
```

---

## Task 18: 工具 wiring 进主入口

**Files:**
- Modify: `src/tianshu/tools/builtins.py:143-152`
- Modify: `src/tianshu/app.py`（register_builtins 调用点）

- [ ] **Step 1: 在 builtins 尾部挂载 hongluisi**

Modify `src/tianshu/tools/builtins.py:143-152`，把 `register_builtins` 函数的尾部扩展为：

```python
    # Register new tools
    from tianshu.tools.edit_file import register_edit_file
    from tianshu.tools.find_files import register_find_files
    from tianshu.tools.grep import register_grep
    from tianshu.tools.list_dir import register_list_dir

    register_edit_file(registry, workspace)
    register_list_dir(registry, workspace)
    register_grep(registry, workspace)
    register_find_files(registry, workspace)

    # 鸿胪寺：对外网络通讯
    from tianshu.tools.hongluisi import register_hongluisi
    from tianshu.executor.ambient import get_current_edict   # Step 2 引入
    register_hongluisi(registry, get_current_edict)
```

- [ ] **Step 2: 确认 ambient edict context**

Run:
```bash
grep -rn "ContextVar\|current_edict\|ambient" src/tianshu/executor/ --include="*.py" | head -20
```

如果已存在 `get_current_edict()` 接口（通过 `ContextVar`），Step 1 的 import 即可用。否则创建 `src/tianshu/executor/ambient.py`:

```python
"""当前正在执行的 Edict —— via ContextVar，供 tool 函数获取运行时上下文。"""

from __future__ import annotations

from contextvars import ContextVar

from tianshu.models.edict import Edict

_current_edict: ContextVar[Edict | None] = ContextVar("current_edict", default=None)


def get_current_edict() -> Edict:
    edict = _current_edict.get()
    if edict is None:
        raise RuntimeError("no current edict in context")
    return edict


def set_current_edict(edict: Edict) -> object:
    return _current_edict.set(edict)


def reset_current_edict(token: object) -> None:
    _current_edict.reset(token)
```

并在 `src/tianshu/executor/executor.py` 或 `agent.py` 的每次 iteration 入口包一层 `set_current_edict(edict)` / `reset_current_edict(token)`（找到 `async def run` 或等价入口，在最外层 try / finally 里调用）。

> **注意**：本 plan 假设 tianshu 现有 executor 已有某种 ambient context 机制。Step 2 若发现确无，补充一个最小 ContextVar 实现（7 行代码）。

- [ ] **Step 3: 跑一次 app，确保启动无报错**

Run:
```bash
python -c "from tianshu.app import create_app; create_app()" 2>&1 | tail -20
```

Expected: 看到 `[hongluisi] fetch engines: ['local', ...]; search providers: [...]` 或等价日志，不 crash。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/tools/builtins.py src/tianshu/executor/ambient.py src/tianshu/executor/*.py
git commit -m "feat(tools): wire hongluisi into register_builtins"
```

---

## Task 19: Prompt + Web UI + Docs

**Files:**
- Modify: `src/tianshu/persona/prompt_builder.py`
- Modify: `web/src/pages/EdictListPage.tsx`（或 EdictCreate 相关页面）
- Modify: `README.md`
- Modify: `docs/impl/skills.md`

- [ ] **Step 1: Prompt 同步**

Modify `src/tianshu/persona/prompt_builder.py` —— 如果其中有工具清单段，在对应处追加 web_fetch / web_search 描述：

```
- web_fetch(url): 获取公开 URL 的 Markdown 正文。内网地址被拒。
- web_search(query, max_results?): 在公网搜索，返回排序后的结果列表（title/url/snippet）。
  配合 web_fetch 使用：先搜索定位，再抓具体 URL 阅读全文。
```

如果 prompt_builder 是从 tool registry 动态生成 `tool_descriptions`，则无需手改 —— 新工具会自动进入。Run:

```bash
grep -n "tool_descriptions\|get_openai_tools\|list_definitions" src/tianshu/persona/prompt_builder.py
```

如果看到动态生成逻辑，Skip 本 Step 代码修改，仅加一行说明注释。

- [ ] **Step 2: Web UI Edict 创建页加两个下拉**

Run:
```bash
grep -rn "EdictRuntime\|runtime\.\(timeout\|max_iter\)" web/src --include="*.tsx" | head -10
```

找到 EdictRuntime 编辑区域的组件。在该组件内增加：

```tsx
<div className="runtime-override-section">
  <label>
    Fetch Engine Override
    <select
      value={runtime.fetch_engine_override || ""}
      onChange={(e) => setRuntime({ ...runtime, fetch_engine_override: e.target.value || null })}
    >
      <option value="">自动（按 profile）</option>
      <option value="local">local（httpx + trafilatura）</option>
      <option value="jina">jina reader</option>
      <option value="firecrawl">firecrawl</option>
    </select>
  </label>
  <label>
    Search Provider Override
    <select
      value={runtime.search_provider_override || ""}
      onChange={(e) => setRuntime({ ...runtime, search_provider_override: e.target.value || null })}
    >
      <option value="">自动（按 profile）</option>
      <option value="tavily">tavily</option>
      <option value="jina">jina search</option>
    </select>
  </label>
</div>
```

同步 `web/src/types/*.ts` 或等价的 EdictRuntime 类型定义，添加 `fetch_engine_override?: string | null` 与 `search_provider_override?: string | null`。

- [ ] **Step 3: README 更新**

Modify `README.md`，在"内置工具"或等价章节追加：

```markdown
### 鸿胪寺（对外网络通讯）

天枢外朝官署，负责一切对外 HTTP/网络 I/O。

- `web_fetch(url)` — 抓取公开网页并以 Markdown 返回
- `web_search(query)` — 搜索公网并返回 top-k 结果

**后端 engine**（由 profile 决定启用哪些）：
- Fetch: local (httpx + trafilatura) → Jina Reader → Firecrawl
- Search: Tavily、Jina Search

**env 配置**：
```
TIANSHU_JINA_API_KEY       # 可选，Jina Reader / Search 提速
TIANSHU_FIRECRAWL_API_KEY  # 启用 Firecrawl engine
TIANSHU_TAVILY_API_KEY     # 启用 Tavily search
```
```

- [ ] **Step 4: docs/impl/skills.md**

Modify `docs/impl/skills.md`，参考现有工具的说明格式，加一段"鸿胪寺工具"章节，包含：工具签名、tier、profile 三档行为表、Edict override 字段说明、错误码一览（引用 spec Section 7.1）。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/persona/prompt_builder.py web/ README.md docs/impl/skills.md
git commit -m "feat(hongluisi): prompt + web ui override + docs"
```

---

## Task 20: 测试（统一补齐）

**Files:**
- Create: `tests/tools/hongluisi/__init__.py`
- Create: `tests/tools/hongluisi/test_ssrf_guard.py`
- Create: `tests/tools/hongluisi/test_rate_limiter.py`
- Create: `tests/tools/hongluisi/test_markdown_extract.py`
- Create: `tests/tools/hongluisi/test_router.py`
- Create: `tests/tools/hongluisi/test_web_fetch_integration.py`
- Create: `tests/tools/hongluisi/test_web_search_integration.py`
- Modify: `pyproject.toml` （加 `pytest-vcr`、`pytest.ini_options.markers`）

- [ ] **Step 1: 添加测试依赖**

Modify `pyproject.toml`，`[project.optional-dependencies]` 下 `dev` 数组追加：

```toml
    "pytest-vcr>=1.0.2",
    "pytest-httpx>=0.30",
```

追加（或创建）`[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
markers = [
    "contract: tests that hit real third-party APIs (recorded via vcr)",
]
asyncio_mode = "auto"
```

Run:
```bash
pip install -e '.[dev]'
```

- [ ] **Step 2: ssrf_guard 单元测试**

Create `tests/tools/hongluisi/__init__.py`（空）

Create `tests/tools/hongluisi/test_ssrf_guard.py`:

```python
"""SSRF guard unit tests. Spec Section 8.1。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url


def _mock_getaddrinfo(ips: list[str]):
    return [(None, None, None, None, (ip, 0)) for ip in ips]


@pytest.mark.asyncio
async def test_bad_scheme():
    for url in ("file:///etc/passwd", "ftp://host", "javascript:alert(1)"):
        with pytest.raises(SSRFViolation) as e:
            await validate_url(url)
        assert e.value.code == "ssrf_bad_scheme"


@pytest.mark.asyncio
async def test_bad_port():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo(["8.8.8.8"])):
        with pytest.raises(SSRFViolation) as e:
            await validate_url("http://example.com:22/")
        assert e.value.code == "ssrf_bad_port"


@pytest.mark.asyncio
async def test_localhost_literal():
    with pytest.raises(SSRFViolation) as e:
        await validate_url("http://localhost/")
    assert e.value.code == "ssrf_bad_hostname"


@pytest.mark.asyncio
async def test_internal_suffix():
    for host in ("foo.internal", "bar.local", "baz.corp"):
        with pytest.raises(SSRFViolation) as e:
            await validate_url(f"http://{host}/")
        assert e.value.code == "ssrf_bad_hostname"


@pytest.mark.asyncio
async def test_dns_to_private_ip():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo(["10.0.0.1"])):
        with pytest.raises(SSRFViolation) as e:
            await validate_url("http://evil.com/")
        assert e.value.code == "ssrf_private_ip"


@pytest.mark.asyncio
async def test_dns_to_metadata_ip():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo(["169.254.169.254"])):
        with pytest.raises(SSRFViolation) as e:
            await validate_url("http://metadata.cloud/")
        assert e.value.code == "ssrf_private_ip"


@pytest.mark.asyncio
async def test_multiple_a_records_one_private():
    # DNS round-robin 绕过防御：任一命中即拒
    with patch(
        "socket.getaddrinfo",
        return_value=_mock_getaddrinfo(["8.8.8.8", "10.0.0.1"]),
    ):
        with pytest.raises(SSRFViolation) as e:
            await validate_url("http://mixed.com/")
        assert e.value.code == "ssrf_private_ip"


@pytest.mark.asyncio
async def test_ipv6_ula():
    with patch(
        "socket.getaddrinfo",
        return_value=_mock_getaddrinfo(["fd00::1"]),
    ):
        with pytest.raises(SSRFViolation) as e:
            await validate_url("http://v6.com/")
        assert e.value.code == "ssrf_private_ip"


@pytest.mark.asyncio
async def test_public_ip_passes():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo(["8.8.8.8"])):
        result = await validate_url("https://example.com/")
        assert result == "https://example.com/"


@pytest.mark.asyncio
async def test_userinfo_stripped():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo(["8.8.8.8"])):
        result = await validate_url("https://u:p@example.com/path?x=1")
        assert "u:p@" not in result
        assert "example.com/path?x=1" in result
```

- [ ] **Step 3: rate_limiter 单元测试**

Create `tests/tools/hongluisi/test_rate_limiter.py`:

```python
"""rate limiter unit tests. Spec Section 8.1。"""

from __future__ import annotations

import asyncio

import pytest

from tianshu.tools.hongluisi.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_allows_within_quota():
    limiter = RateLimiter()
    for _ in range(5):
        rc = await limiter.check("edict1", "web_fetch", rate_per_min=5)
        assert rc.allowed


@pytest.mark.asyncio
async def test_blocks_over_quota():
    limiter = RateLimiter()
    for _ in range(3):
        await limiter.check("e1", "web_fetch", rate_per_min=3)
    rc = await limiter.check("e1", "web_fetch", rate_per_min=3)
    assert not rc.allowed
    assert rc.retry_after_sec > 0


@pytest.mark.asyncio
async def test_isolated_per_edict():
    limiter = RateLimiter()
    for _ in range(3):
        await limiter.check("e1", "web_fetch", rate_per_min=3)
    rc = await limiter.check("e2", "web_fetch", rate_per_min=3)
    assert rc.allowed


@pytest.mark.asyncio
async def test_isolated_per_tool():
    limiter = RateLimiter()
    for _ in range(3):
        await limiter.check("e1", "web_fetch", rate_per_min=3)
    rc = await limiter.check("e1", "web_search", rate_per_min=3)
    assert rc.allowed
```

- [ ] **Step 4: markdown_extract 单元测试**

Create `tests/tools/hongluisi/test_markdown_extract.py`:

```python
"""markdown_extract unit tests。"""

from tianshu.tools.hongluisi.markdown_extract import extract_markdown, is_empty


def test_extract_normal_html():
    html = """
    <html><body>
      <h1>Hello</h1>
      <p>This is a paragraph of text long enough to pass extraction.
      It contains multiple sentences so trafilatura recognises it as content.</p>
    </body></html>
    """ * 5
    md = extract_markdown(html, url="https://example.com")
    assert "Hello" in md or "paragraph" in md


def test_extract_empty_html():
    assert extract_markdown("") == ""
    assert extract_markdown("   ") == ""


def test_extract_returns_empty_on_noise():
    html = "<html><body><script>alert(1)</script></body></html>"
    md = extract_markdown(html)
    # trafilatura 一般返回 "" 或极短字符串
    assert is_empty(md)


def test_is_empty_threshold():
    assert is_empty("")
    assert is_empty("short")
    assert not is_empty("x" * 501)
```

- [ ] **Step 5: router 单元测试**

Create `tests/tools/hongluisi/test_router.py`:

```python
"""FetchRouter fallback 决策逻辑。Spec Section 8.1。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tianshu.tools.hongluisi.engines import FetchEngine, FetchOutcome
from tianshu.tools.hongluisi.policy import NetworkPolicy
from tianshu.tools.hongluisi.router import FetchRouter


class FakeEngine:
    def __init__(self, name: str, outcome: FetchOutcome):
        self.name = name
        self._outcome = outcome

    async def fetch(self, url: str) -> FetchOutcome:
        return self._outcome


def _outcome(status: str, reason: str | None = None) -> FetchOutcome:
    return FetchOutcome(
        content="ok content" * 100 if status == "ok" else "",
        status=status, http_status=200 if status == "ok" else 500,
        reason=reason, bytes_fetched=100, final_url="https://x",
    )


@pytest.mark.asyncio
async def test_single_engine_ok():
    engines = {"local": FakeEngine("local", _outcome("ok"))}
    policy = NetworkPolicy(fetch_engines=("local",), fallback_mode="none")
    router = FetchRouter(engines, policy, override=None)
    outcome, attempts = await router.dispatch("https://x")
    assert outcome.status == "ok"
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_fallback_on_error():
    engines = {
        "local": FakeEngine("local", _outcome("error", "timeout")),
        "jina": FakeEngine("jina", _outcome("ok")),
    }
    policy = NetworkPolicy(
        fetch_engines=("local", "jina"),
        fallback_mode="on_error_or_empty",
    )
    router = FetchRouter(engines, policy, override=None)
    outcome, attempts = await router.dispatch("https://x")
    assert outcome.status == "ok"
    assert [a.engine for a in attempts] == ["local", "jina"]


@pytest.mark.asyncio
async def test_no_fallback_when_mode_none():
    engines = {
        "local": FakeEngine("local", _outcome("error", "timeout")),
        "jina": FakeEngine("jina", _outcome("ok")),
    }
    policy = NetworkPolicy(
        fetch_engines=("local", "jina"),
        fallback_mode="none",
    )
    router = FetchRouter(engines, policy, override=None)
    outcome, attempts = await router.dispatch("https://x")
    assert outcome.status == "error"
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_override_pins_engine():
    engines = {
        "local": FakeEngine("local", _outcome("ok")),
        "firecrawl": FakeEngine("firecrawl", _outcome("ok")),
    }
    policy = NetworkPolicy(
        fetch_engines=("local", "jina"),
        fallback_mode="on_error_or_empty",
    )
    router = FetchRouter(engines, policy, override="firecrawl")
    outcome, attempts = await router.dispatch("https://x")
    assert [a.engine for a in attempts] == ["firecrawl"]


@pytest.mark.asyncio
async def test_skip_unregistered_engine_in_chain():
    engines = {"local": FakeEngine("local", _outcome("error"))}
    policy = NetworkPolicy(
        fetch_engines=("firecrawl", "local"),   # firecrawl 未注册
        fallback_mode="on_error_or_empty",
    )
    router = FetchRouter(engines, policy, override=None)
    outcome, attempts = await router.dispatch("https://x")
    assert attempts[0].engine == "firecrawl"
    assert attempts[0].status == "skipped"
    assert attempts[1].engine == "local"


@pytest.mark.asyncio
async def test_max_fallback_depth():
    engines = {
        "local": FakeEngine("local", _outcome("error")),
        "jina": FakeEngine("jina", _outcome("error")),
        "firecrawl": FakeEngine("firecrawl", _outcome("ok")),
    }
    policy = NetworkPolicy(
        fetch_engines=("local", "jina", "firecrawl"),
        fallback_mode="on_error_or_empty",
        max_fallback_depth=2,
    )
    router = FetchRouter(engines, policy, override=None)
    outcome, attempts = await router.dispatch("https://x")
    assert len(attempts) == 2
    assert outcome.status == "error"


@pytest.mark.asyncio
async def test_engine_exception_wrapped_as_error():
    class BoomEngine:
        name = "boom"
        async def fetch(self, url):
            raise RuntimeError("boom")
    engines = {
        "boom": BoomEngine(),
        "local": FakeEngine("local", _outcome("ok")),
    }
    policy = NetworkPolicy(
        fetch_engines=("boom", "local"),
        fallback_mode="on_error_or_empty",
    )
    router = FetchRouter(engines, policy, override=None)
    outcome, attempts = await router.dispatch("https://x")
    assert attempts[0].status == "error"
    assert "engine_exception" in (attempts[0].reason or "")
    assert outcome.status == "ok"
```

- [ ] **Step 6: web_fetch 集成测试**

Create `tests/tools/hongluisi/test_web_fetch_integration.py`:

```python
"""web_fetch 端到端集成测试。Spec Section 8.2。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tianshu.tools.hongluisi import tools as hl_tools
from tianshu.tools.hongluisi.engines import FetchOutcome
from tianshu.tools.hongluisi.policy import NetworkPolicy


def _outcome(status: str, content: str = "", reason: str | None = None) -> FetchOutcome:
    return FetchOutcome(
        content=content or "ok content" * 200,
        status=status,
        http_status=200,
        reason=reason,
        bytes_fetched=100,
        final_url="https://x",
    )


@pytest.mark.asyncio
async def test_successful_fetch_returns_content():
    class Fake:
        name = "local"
        async def fetch(self, url): return _outcome("ok")
    with patch.object(hl_tools, "get_fetch_engines_map", return_value={"local": Fake()}):
        net = NetworkPolicy(fetch_engines=("local",))
        result = await hl_tools.web_fetch(
            "https://example.com",
            edict_id="e1", net=net, fe_override=None,
        )
        assert not result.is_error
        assert "ok content" in result.content
        assert result.details["engine_used"] == "local"


@pytest.mark.asyncio
async def test_fallback_chain_visible_on_failure():
    class FakeErr:
        name = "local"
        async def fetch(self, url): return _outcome("error", reason="timeout")
    class FakeAlsoErr:
        name = "jina"
        async def fetch(self, url): return _outcome("error", reason="http_status:403")
    with patch.object(
        hl_tools, "get_fetch_engines_map",
        return_value={"local": FakeErr(), "jina": FakeAlsoErr()},
    ):
        net = NetworkPolicy(
            fetch_engines=("local", "jina"),
            fallback_mode="on_error_or_empty",
        )
        result = await hl_tools.web_fetch(
            "https://example.com",
            edict_id="e1", net=net, fe_override=None,
        )
        assert result.is_error
        assert "local" in result.content
        assert "jina" in result.content


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_quota():
    class Fake:
        name = "local"
        async def fetch(self, url): return _outcome("ok")
    with patch.object(hl_tools, "get_fetch_engines_map", return_value={"local": Fake()}):
        net = NetworkPolicy(fetch_engines=("local",), web_fetch_rate_per_min=3)
        for _ in range(3):
            result = await hl_tools.web_fetch("https://x", edict_id="e1", net=net, fe_override=None)
            assert not result.is_error
        result = await hl_tools.web_fetch("https://x", edict_id="e1", net=net, fe_override=None)
        assert result.is_error
        assert "rate limit" in result.content.lower()
```

- [ ] **Step 7: web_search 集成测试**

Create `tests/tools/hongluisi/test_web_search_integration.py`:

```python
"""web_search 端到端集成测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tianshu.tools.hongluisi import tools as hl_tools
from tianshu.tools.hongluisi.engines import SearchOutcome, SearchResult
from tianshu.tools.hongluisi.policy import NetworkPolicy


class FakeProvider:
    name = "tavily"
    async def search(self, query, max_results):
        return SearchOutcome(
            results=(
                SearchResult("Title A", "https://a", "snippet A", 0.9),
                SearchResult("Title B", "https://b", "snippet B", 0.7),
            ),
            raw_api_meta={"usage": "1 credit"},
        )


@pytest.mark.asyncio
async def test_search_formats_results():
    with patch.object(hl_tools, "get_search_providers_map", return_value={"tavily": FakeProvider()}):
        net = NetworkPolicy(search_provider="tavily")
        result = await hl_tools.web_search(
            "foo", max_results=5,
            edict_id="e1", net=net, sp_override=None,
        )
        assert not result.is_error
        assert "Title A" in result.content
        assert "https://a" in result.content
        assert result.details["provider"] == "tavily"
        assert result.details["result_count"] == 2


@pytest.mark.asyncio
async def test_search_disabled_in_offline_profile():
    net = NetworkPolicy(search_provider=None)
    result = await hl_tools.web_search(
        "foo", max_results=5,
        edict_id="e1", net=net, sp_override=None,
    )
    assert result.is_error
    assert "disabled" in result.content.lower()


@pytest.mark.asyncio
async def test_search_unregistered_override_fails():
    with patch.object(hl_tools, "get_search_providers_map", return_value={}):
        net = NetworkPolicy(search_provider="tavily")
        result = await hl_tools.web_search(
            "foo", max_results=5,
            edict_id="e1", net=net, sp_override="exa",
        )
        assert result.is_error
        assert "not registered" in result.content.lower() or "exa" in result.content.lower()
```

- [ ] **Step 8: 跑全部单元与集成测试**

Run:
```bash
pytest tests/tools/hongluisi/ -v --tb=short -m "not contract"
```

Expected: 全部 PASS。

- [ ] **Step 9: Commit 测试**

```bash
git add tests/tools/hongluisi/ pyproject.toml
git commit -m "test(hongluisi): unit + integration tests"
```

- [ ] **Step 10: 手工验证清单（spec Section 8.4 移植）**

跑 dev server 后按下表逐项验证：

- [ ] 创建 `safe-explore` profile 的 Edict → LLM 看不到 `web_search`（应被 NetworkSafetyRule deny）
- [ ] 创建 `refactor-in-place` profile Edict → `web_fetch("https://example.com")` 在 AuditDashboard 可见，`engine_chain=["local"]`
- [ ] `web_fetch("http://169.254.169.254/")` → `is_error=True`，reason 里只有 `ssrf_private_ip`（**不含**具体 IP）
- [ ] `trusted-automation` profile + Edict runtime `fetch_engine_override="firecrawl"` → AuditDashboard 里 `engine_used="firecrawl"`
- [ ] 连续 21 次 `web_fetch` → 第 21 次返回 rate limit error
- [ ] 抓一个 1.5 MB 大文件 URL → 被 size limit 拦下

记录验证结果后，在本 plan 文件末尾追加"验证日志"章节。

- [ ] **Step 11: 最终 commit**

```bash
git add docs/superpowers/plans/2026-04-21-web-access-tools.md
git commit -m "docs(plan): manual verification log appended"
```

---

## Self-Review Summary

**Spec coverage check:**
- Scope（A+B）→ Task 16 两工具实现 ✓
- 鸿胪寺模块布局 → Task 2,4,5,6,7,13,14,15 ✓
- Tier 扩展 → Task 0+1 ✓
- 三档 profile → Task 2 ✓
- Edict override → Task 3 ✓
- env 控制 → Task 9,10,11,12 各自 build_*() ✓
- 工具接口（LLM 视角）→ Task 16 ToolDefinition ✓
- SSRF 纵深防御 → Task 4 guard + Task 6 redirect hook + Task 8/9/10 engine 层 + Task 17 policy 层 ✓
- SharedHttpClient → Task 6 ✓
- FetchRouter fallback 决策 → Task 14 ✓
- NetworkSafetyRule → Task 17 ✓
- Rate limiter → Task 15 ✓
- 数据流（6.1/6.2）→ 由 Task 16 web_fetch/web_search 实现路径覆盖 ✓
- 错误处理分类表 → Task 8-16 各层抛错点 + Task 16 fallback-exhausted 组装 ✓
- 观测（9.1）→ Task 16 details 字段 ✓
- 测试（单元/集成/契约）→ Task 20（契约测试未写 cassette，标注待补） ✓
- 手工验证清单（8.4）→ Task 20 Step 10 ✓

**Placeholder scan:** 无 TBD / TODO。Task 0 的决策预设值在 Task 1 起始明确假设（扩展方案），保守方案的替代路径在 Task 0 Step 2 注释里显式说明。

**Type consistency:** `FetchOutcome` / `FetchAttempt` / `NetworkPolicy` / `ToolResult` 在所有 Task 里使用的字段名一致。`get_registered_fetch_engines()` 与 `get_registered_search_providers()` 在 Task 13 与 Task 17 签名匹配。

**未覆盖项（故意延后）：**
- 契约测试的 cassette 录制：需要真实 API key，在 Task 20 标注 "skip if no key"；cassette 首次由开发者本地录制并提交
- Edict "网络使用量"前端统计条：spec 9.2 明确标注"非本 spec scope"，不纳入本 plan
- MCP 方式接入 agent-browser：spec 12 标注"未来扩展"，不在此
