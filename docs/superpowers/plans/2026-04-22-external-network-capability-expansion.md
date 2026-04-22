# 外部网络通讯能力扩展 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 L2/L3 基础设施上补齐 L1 `api_request` + L4 `web_extract`，引入藏兵阁加密凭证池，全部作为单个 PR 合入 main。

**Architecture:** 凭证层独立于工具层（Fernet 加密 + sqlite 表 + host 匹配注入器），api_request 在 PolicyRule 层按 method 动态升级到 T3_WRITE 走审批，web_extract 复用 Firecrawl API key。所有前后端改动统一在 `feat_phase5` 分支上 ~26 个 commit 切分完成。

**Tech Stack:** Python 3.12 / FastAPI / sqlite3（直操，非 ORM）/ cryptography.fernet / httpx / React + TypeScript / Vite / Mantine UI

**Spec:** [2026-04-22-external-network-capability-expansion-design.md](../specs/2026-04-22-external-network-capability-expansion-design.md)

**User preference:** 功能优先，测试最后补。每个 task 只做功能 + smoke check，pytest 单元/集成测试统一放 Task 23。

---

## File Structure

### 新建
```
src/tianshu/secrets/
├── __init__.py                   # 导出 SecretVault / CredentialStore / CredentialInjector
├── vault.py                      # Fernet 封装 + get_vault() 单例
├── store.py                      # CredentialStore：network_credentials 表 CRUD
├── injector.py                   # CredentialInjector：host 匹配 + header 渲染
└── models.py                     # pydantic: Credential / CredentialCreate / CredentialUpdate

src/tianshu/executor/ambient.py   # ContextVar[current_edict] + set/get 包装

src/tianshu/tools/hongluisi/
├── engines/firecrawl_extract.py  # FirecrawlExtractEngine
├── api_request.py                # api_request 工具实现（ToolDefinition + handler）
├── web_extract.py                # web_extract 工具实现
└── tools.py                      # register_hongluisi(registry) 主入口

src/tianshu/tools/policy_rules/network_safety.py
                                  # NetworkSafetyRule（扩展 4 工具分支）

src/tianshu/gateway/credentials_api.py
                                  # /api/credentials CRUD FastAPI router

web/src/components/system/ExternalCredentialsTab.tsx
                                  # 藏兵阁外部凭证 tab 内容
web/src/components/edict/NetworkCapabilitySection.tsx
                                  # Edict 创建/编辑页网络能力 section
web/src/api/credentials.ts        # fetch 客户端封装
```

### 修改
```
src/tianshu/storage.py                      # +network_credentials 表 + CRUD 方法
src/tianshu/tools/hongluisi/policy.py       # NetworkPolicy 扩 3 字段
src/tianshu/tools/hongluisi/http_client.py  # cache key 加 credential_name
src/tianshu/tools/policy_profile.py         # 3 档预设对 L1 差异化
src/tianshu/models/edict.py                 # EdictRuntime + PolicyProfilePayload 扩字段
src/tianshu/models/api.py                   # EdictRuntimeRequest 同步扩字段
src/tianshu/tools/builtins.py               # import register_hongluisi 挂到入口
src/tianshu/executor/agent.py               # 执行前 set ambient edict
src/tianshu/models/common.py                # AuditResult 加 network 字段
src/tianshu/app.py                          # 注册 credentials_api router
src/tianshu/gateway/api.py                  # Edict 校验 write_hosts ⊆ hosts
pyproject.toml                              # +cryptography
web/src/pages/SystemManagementPage.tsx      # 挂 ExternalCredentialsTab
web/src/pages/EdictCreatePage.tsx           # 挂 NetworkCapabilitySection
web/src/pages/AuditDashboardPage.tsx        # 渲染 network.* 审计事件
web/src/api/types.ts                        # +Credential / NetworkAuditEvent
```

---

## Task Index

| # | Phase | 标题 | 依赖 |
|---|-------|------|------|
| 1 | 凭证 | SecretVault Fernet 封装 | — |
| 2 | 凭证 | network_credentials 表 + CRUD | 1 |
| 3 | 凭证 | CredentialInjector host 匹配 | 2 |
| 4 | Policy | NetworkPolicy 扩 api_request 字段 | — |
| 5 | Policy | 3 档 profile 对 L1 差异化 | 4 |
| 6 | Edict | EdictRuntime 扩 host 白名单 | — |
| 7 | 引擎 | FirecrawlExtractEngine | — |
| 8 | 引擎 | api_request engine wrapper | 3 |
| 9 | 工具 | web_fetch / web_search 工具注册 | 6 |
| 10 | 工具 | api_request 工具注册 | 8 |
| 11 | 工具 | web_extract 工具注册 | 7 |
| 12 | 入口 | register_hongluisi + ambient Edict | 9,10,11 |
| 13 | 规则 | NetworkSafetyRule 扩 4 工具分支 | 12 |
| 14 | 规则 | 写方法审批路径接入 | 13 |
| 15 | 审计 | network 审计字段 + redact | 13 |
| 16 | API | /api/credentials CRUD | 3 |
| 17 | API | /api/edicts 校验 hosts 子集关系 | 6,16 |
| 18 | API | 审计查询按 host / credential 过滤 | 15 |
| 19 | UI | 藏兵阁 - 外部凭证 tab | 16 |
| 20 | UI | Edict 页 - 网络能力 section | 17 |
| 21 | UI | AuditDashboard - 网络事件行 | 18 |
| 22 | UI | 宫殿首页"外部感知"解锁提示 | 20 |
| 23 | 测试 | 单元 + 集成测试统一补齐 | 1-22 |
| 24 | 文档 | README + skills.md + ops 手册 | 23 |
| 25 | Prompt | prompt_builder 自描述 + 示例模板 | 23 |

---

## Task 1: SecretVault Fernet 封装

**Files:**
- Create: `src/tianshu/secrets/__init__.py`
- Create: `src/tianshu/secrets/vault.py`
- Modify: `pyproject.toml`（添加 `cryptography>=42`）

- [ ] **Step 1: 添加依赖**

修改 `pyproject.toml` `[project.dependencies]` 追加：
```toml
"cryptography>=42",
```

运行 `uv sync` 或 `pip install -e .` 确认安装。

- [ ] **Step 2: 创建 secrets package**

创建 `src/tianshu/secrets/__init__.py`：
```python
"""加密凭证子系统（藏兵阁后端）。Spec Section 4。"""

from tianshu.secrets.vault import SecretVault, get_vault

__all__ = ["SecretVault", "get_vault"]
```

- [ ] **Step 3: 实现 SecretVault**

创建 `src/tianshu/secrets/vault.py`：
```python
"""Fernet 对称加密封装。主密钥从 TIANSHU_SECRET_MASTER_KEY 读取。"""

from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class SecretVault:
    def __init__(self, master_key: str) -> None:
        # master_key 必须是 Fernet.generate_key() 的输出（32 字节 url-safe base64）
        self._fernet = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("credential decryption failed") from e


_vault: SecretVault | None = None


def get_vault() -> SecretVault | None:
    """主密钥缺失返回 None。调用方据此决定降级策略。"""
    global _vault
    if _vault is not None:
        return _vault
    key = os.getenv("TIANSHU_SECRET_MASTER_KEY")
    if not key:
        logger.warning(
            "[secrets] TIANSHU_SECRET_MASTER_KEY unset; "
            "api_request / credentials store disabled"
        )
        return None
    _vault = SecretVault(key)
    return _vault


def reset_vault() -> None:
    """测试用。"""
    global _vault
    _vault = None
```

- [ ] **Step 4: Smoke check**

```bash
python -c "
from cryptography.fernet import Fernet
import os
os.environ['TIANSHU_SECRET_MASTER_KEY'] = Fernet.generate_key().decode()
from tianshu.secrets import get_vault
v = get_vault()
c = v.encrypt('hello-world')
assert v.decrypt(c) == 'hello-world'
print('vault OK')
"
```
Expected: `vault OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/tianshu/secrets/
git commit -m "$(cat <<'EOF'
feat(secrets): SecretVault Fernet wrapper

引入: SecretVault.encrypt/decrypt, get_vault() 单例
使用者: Task 2 CredentialStore, Task 3 CredentialInjector
关闭: TIANSHU_SECRET_MASTER_KEY 未设置 → get_vault() 返回 None；下游工具不注册
EOF
)"
```

---

## Task 2: network_credentials 表 + CredentialStore CRUD

**Files:**
- Create: `src/tianshu/secrets/models.py`
- Create: `src/tianshu/secrets/store.py`
- Modify: `src/tianshu/storage.py`（在 `_create_tables` 里加表，在 Storage 类加 CRUD 方法）

- [ ] **Step 1: 定义 pydantic models**

创建 `src/tianshu/secrets/models.py`：
```python
"""凭证 DTO。Spec Section 4.2。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Credential(BaseModel):
    id: str
    name: str
    host_pattern: str
    header_template: str      # 例: "Authorization: Bearer {value}"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    encrypted_value: bytes    # ciphertext
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None


class CredentialCreate(BaseModel):
    name: str
    host_pattern: str
    header_template: str
    value: str                # plaintext，加密后丢
    extra_headers: dict[str, str] = Field(default_factory=dict)


class CredentialUpdate(BaseModel):
    value: str | None = None
    extra_headers: dict[str, str] | None = None


class CredentialView(BaseModel):
    """返回给前端用，不包含 encrypted_value / value。"""
    id: str
    name: str
    host_pattern: str
    header_template: str
    extra_headers: dict[str, str]
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
```

- [ ] **Step 2: 在 Storage 里加表 DDL**

修改 `src/tianshu/storage.py` 的 `_create_tables` 方法，在 `executescript` 的 SQL 末尾追加：
```sql
CREATE TABLE IF NOT EXISTS network_credentials (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    host_pattern    TEXT NOT NULL,
    header_template TEXT NOT NULL,
    extra_headers   TEXT NOT NULL DEFAULT '{}',
    encrypted_value BLOB NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_used_at    TEXT,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_netcreds_host ON network_credentials(host_pattern);
CREATE INDEX IF NOT EXISTS idx_netcreds_name ON network_credentials(name);
```

在 Storage 类末尾追加方法：
```python
def insert_credential(
    self,
    *,
    cred_id: str,
    name: str,
    host_pattern: str,
    header_template: str,
    extra_headers_json: str,
    encrypted_value: bytes,
    now_iso: str,
) -> None:
    with self._lock, self._conn:
        self._conn.execute(
            """INSERT INTO network_credentials
               (id, name, host_pattern, header_template, extra_headers,
                encrypted_value, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cred_id, name, host_pattern, header_template,
             extra_headers_json, encrypted_value, now_iso, now_iso),
        )

def list_credentials(self) -> list[sqlite3.Row]:
    with self._lock:
        cur = self._conn.execute(
            "SELECT * FROM network_credentials WHERE deleted_at IS NULL "
            "ORDER BY name"
        )
        return cur.fetchall()

def get_credential_by_id(self, cred_id: str) -> sqlite3.Row | None:
    with self._lock:
        cur = self._conn.execute(
            "SELECT * FROM network_credentials WHERE id=? AND deleted_at IS NULL",
            (cred_id,),
        )
        return cur.fetchone()

def find_credentials_by_host(self, host: str) -> list[sqlite3.Row]:
    """返回所有可能匹配此 host 的凭证（literal + 通配）。匹配排序在上层做。"""
    with self._lock:
        cur = self._conn.execute(
            "SELECT * FROM network_credentials "
            "WHERE deleted_at IS NULL "
            "AND (host_pattern=? OR host_pattern LIKE '*.%')",
            (host,),
        )
        return cur.fetchall()

def update_credential(
    self,
    cred_id: str,
    *,
    encrypted_value: bytes | None = None,
    extra_headers_json: str | None = None,
    now_iso: str,
) -> None:
    sets = ["updated_at=?"]
    params: list[object] = [now_iso]
    if encrypted_value is not None:
        sets.append("encrypted_value=?")
        params.append(encrypted_value)
    if extra_headers_json is not None:
        sets.append("extra_headers=?")
        params.append(extra_headers_json)
    params.append(cred_id)
    with self._lock, self._conn:
        self._conn.execute(
            f"UPDATE network_credentials SET {', '.join(sets)} WHERE id=?",
            params,
        )

def mark_credential_used(self, cred_id: str, now_iso: str) -> None:
    with self._lock, self._conn:
        self._conn.execute(
            "UPDATE network_credentials SET last_used_at=? WHERE id=?",
            (now_iso, cred_id),
        )

def soft_delete_credential(self, cred_id: str, now_iso: str) -> None:
    with self._lock, self._conn:
        self._conn.execute(
            "UPDATE network_credentials SET deleted_at=? WHERE id=?",
            (now_iso, cred_id),
        )
```

- [ ] **Step 3: 实现 CredentialStore（业务封装）**

创建 `src/tianshu/secrets/store.py`：
```python
"""CredentialStore：把 Storage 行转为 Credential domain 对象。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from ulid import ULID

from tianshu.secrets.models import (
    Credential,
    CredentialCreate,
    CredentialUpdate,
)
from tianshu.secrets.vault import SecretVault
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_credential(row) -> Credential:
    return Credential(
        id=row["id"],
        name=row["name"],
        host_pattern=row["host_pattern"],
        header_template=row["header_template"],
        extra_headers=json.loads(row["extra_headers"]),
        encrypted_value=row["encrypted_value"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_used_at=(
            datetime.fromisoformat(row["last_used_at"])
            if row["last_used_at"]
            else None
        ),
    )


class CredentialStore:
    def __init__(self, storage: Storage, vault: SecretVault) -> None:
        self._storage = storage
        self._vault = vault

    def create(self, req: CredentialCreate) -> Credential:
        cred_id = str(ULID())
        encrypted = self._vault.encrypt(req.value)
        self._storage.insert_credential(
            cred_id=cred_id,
            name=req.name,
            host_pattern=req.host_pattern,
            header_template=req.header_template,
            extra_headers_json=json.dumps(req.extra_headers),
            encrypted_value=encrypted,
            now_iso=_now_iso(),
        )
        row = self._storage.get_credential_by_id(cred_id)
        return _row_to_credential(row)

    def list_all(self) -> list[Credential]:
        return [_row_to_credential(r) for r in self._storage.list_credentials()]

    def get(self, cred_id: str) -> Credential | None:
        row = self._storage.get_credential_by_id(cred_id)
        return _row_to_credential(row) if row else None

    def find_for_host(self, host: str) -> Credential | None:
        """最具体匹配：字面 > 通配 > None。"""
        rows = self._storage.find_credentials_by_host(host)
        exact = [r for r in rows if r["host_pattern"] == host]
        if exact:
            return _row_to_credential(exact[0])
        for r in rows:
            pat = r["host_pattern"]
            if pat.startswith("*.") and host.endswith(pat[1:]):
                return _row_to_credential(r)
        return None

    def update(self, cred_id: str, req: CredentialUpdate) -> Credential | None:
        if self._storage.get_credential_by_id(cred_id) is None:
            return None
        enc = self._vault.encrypt(req.value) if req.value is not None else None
        extra = (
            json.dumps(req.extra_headers) if req.extra_headers is not None else None
        )
        self._storage.update_credential(
            cred_id,
            encrypted_value=enc,
            extra_headers_json=extra,
            now_iso=_now_iso(),
        )
        return self.get(cred_id)

    def delete(self, cred_id: str) -> bool:
        if self._storage.get_credential_by_id(cred_id) is None:
            return False
        self._storage.soft_delete_credential(cred_id, _now_iso())
        return True

    def mark_used(self, cred_id: str) -> None:
        self._storage.mark_credential_used(cred_id, _now_iso())
```

- [ ] **Step 4: 导出**

修改 `src/tianshu/secrets/__init__.py`：
```python
"""加密凭证子系统（藏兵阁后端）。Spec Section 4。"""

from tianshu.secrets.models import (
    Credential,
    CredentialCreate,
    CredentialUpdate,
    CredentialView,
)
from tianshu.secrets.store import CredentialStore
from tianshu.secrets.vault import SecretVault, get_vault

__all__ = [
    "Credential",
    "CredentialCreate",
    "CredentialUpdate",
    "CredentialView",
    "CredentialStore",
    "SecretVault",
    "get_vault",
]
```

- [ ] **Step 5: Smoke check**

```bash
python -c "
import os, tempfile
from cryptography.fernet import Fernet
os.environ['TIANSHU_SECRET_MASTER_KEY'] = Fernet.generate_key().decode()
from tianshu.storage import Storage
from tianshu.secrets import get_vault, CredentialStore, CredentialCreate
db = tempfile.mktemp(suffix='.db')
s = Storage(db); s.init_db()
store = CredentialStore(s, get_vault())
c = store.create(CredentialCreate(
    name='gh-test', host_pattern='api.github.com',
    header_template='Authorization: Bearer {value}',
    value='ghp_xxx'))
assert c.id
got = store.find_for_host('api.github.com')
assert got.name == 'gh-test'
print('store OK')
"
```
Expected: `store OK`

- [ ] **Step 6: Commit**

```bash
git add src/tianshu/secrets/ src/tianshu/storage.py
git commit -m "$(cat <<'EOF'
feat(secrets): CredentialStore + network_credentials table

引入: network_credentials 表, CredentialStore CRUD, Credential DTOs
使用者: Task 3 CredentialInjector, Task 16 /api/credentials
关闭: 表不存在时 Storage.init_db() 会自动创建；旧库无表走 IF NOT EXISTS 迁移
EOF
)"
```

---

## Task 3: CredentialInjector（host 匹配 + header 渲染）

**Files:**
- Create: `src/tianshu/secrets/injector.py`
- Modify: `src/tianshu/secrets/__init__.py`（导出 Injector + 异常）

- [ ] **Step 1: 实现 CredentialInjector**

创建 `src/tianshu/secrets/injector.py`：
```python
"""host 匹配 + header 注入。Spec Section 4.4-4.5。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.secrets.store import CredentialStore

# 敏感 header 黑名单：用户 api_request(headers=) 传这些直接拒绝
FORBIDDEN_USER_HEADERS = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "proxy-authorization",
})


class CredentialConflict(Exception):
    """用户 header 与注入 header 冲突。"""

    def __init__(self, header: str) -> None:
        super().__init__(f"user header {header!r} conflicts with injected credential")
        self.header = header


class ForbiddenHeader(Exception):
    """用户试图传入敏感 header。"""

    def __init__(self, header: str) -> None:
        super().__init__(f"user cannot set sensitive header {header!r}")
        self.header = header


@dataclass(frozen=True)
class InjectionResult:
    merged_headers: dict[str, str]
    credential_name: str | None   # None 表示没命中凭证


class CredentialInjector:
    def __init__(self, store: CredentialStore) -> None:
        self._store = store

    def validate_user_headers(self, user_headers: dict[str, str]) -> None:
        for name in user_headers:
            if name.lower() in FORBIDDEN_USER_HEADERS:
                raise ForbiddenHeader(name)

    def inject(
        self, url_host: str, user_headers: dict[str, str]
    ) -> InjectionResult:
        self.validate_user_headers(user_headers)
        cred = self._store.find_for_host(url_host)
        if cred is None:
            return InjectionResult(dict(user_headers), None)

        # 解密 → 渲染
        value = self._store._vault.decrypt(cred.encrypted_value)  # noqa: SLF001

        # "Authorization: Bearer {value}" → ("Authorization", "Bearer <real>")
        header_name, template = cred.header_template.split(":", 1)
        header_name = header_name.strip()
        rendered = template.strip().format(value=value)

        injected = {header_name: rendered, **cred.extra_headers}

        # 用户 header 不能与注入 header 同名
        user_lower = {h.lower() for h in user_headers}
        for k in injected:
            if k.lower() in user_lower:
                raise CredentialConflict(header=k)

        merged = {**user_headers, **injected}
        self._store.mark_used(cred.id)
        return InjectionResult(merged, cred.name)


def redact_sensitive_headers(headers: dict[str, str]) -> dict[str, str]:
    """打日志/审计前过一遍。"""
    return {
        k: ("<redacted>" if k.lower() in FORBIDDEN_USER_HEADERS else v)
        for k, v in headers.items()
    }
```

- [ ] **Step 2: 更新 __init__.py**

修改 `src/tianshu/secrets/__init__.py`：
```python
from tianshu.secrets.injector import (
    CredentialConflict,
    CredentialInjector,
    ForbiddenHeader,
    FORBIDDEN_USER_HEADERS,
    InjectionResult,
    redact_sensitive_headers,
)
```
并追加到 `__all__`。

- [ ] **Step 3: Smoke check**

```bash
python -c "
import os, tempfile
from cryptography.fernet import Fernet
os.environ['TIANSHU_SECRET_MASTER_KEY'] = Fernet.generate_key().decode()
from tianshu.storage import Storage
from tianshu.secrets import get_vault, CredentialStore, CredentialCreate, CredentialInjector, ForbiddenHeader
db = tempfile.mktemp(suffix='.db')
s = Storage(db); s.init_db()
store = CredentialStore(s, get_vault())
store.create(CredentialCreate(
    name='gh', host_pattern='api.github.com',
    header_template='Authorization: Bearer {value}',
    value='secret-token-xyz'))
inj = CredentialInjector(store)
r = inj.inject('api.github.com', {'Accept': 'application/json'})
assert r.credential_name == 'gh'
assert r.merged_headers['Authorization'] == 'Bearer secret-token-xyz'
try:
    inj.inject('api.github.com', {'Authorization': 'Bearer foo'})
    assert False, 'should have raised ForbiddenHeader'
except ForbiddenHeader: pass
print('injector OK')
"
```
Expected: `injector OK`

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/secrets/injector.py src/tianshu/secrets/__init__.py
git commit -m "$(cat <<'EOF'
feat(secrets): CredentialInjector host matching + header rendering

引入: CredentialInjector.inject(), ForbiddenHeader/CredentialConflict, redact_sensitive_headers
使用者: Task 8 api_request engine, Task 15 审计 redact
关闭: Injector 无状态，任何异常立即抛出；永远不返回未清理的凭证值
EOF
)"
```

---

## Task 4: NetworkPolicy 扩 api_request 字段

**Files:**
- Modify: `src/tianshu/tools/hongluisi/policy.py`

- [ ] **Step 1: 追加字段**

修改 `src/tianshu/tools/hongluisi/policy.py` 的 `NetworkPolicy` dataclass，在现有字段后追加：
```python
@dataclass(frozen=True)
class NetworkPolicy:
    # 既有字段保持不变...
    fetch_engines: tuple[str, ...] = ("local",)
    fallback_mode: str = "none"
    search_provider: str | None = None
    max_fallback_depth: int = 3
    web_fetch_rate_per_min: int = 20
    web_search_rate_per_min: int = 10
    # 新增 ↓
    allow_api_request: bool = False
    api_request_methods: tuple[str, ...] = ("GET", "HEAD")
    api_request_rate_per_min: int = 30
    web_extract_rate_per_min: int = 10
```

在文件末尾的 3 个常量基础上**不**修改，Task 5 会更新它们。

- [ ] **Step 2: Smoke check**

```bash
python -c "
from tianshu.tools.hongluisi.policy import NetworkPolicy, NETWORK_DEFAULT
p = NetworkPolicy()
assert p.allow_api_request is False
assert p.api_request_methods == ('GET', 'HEAD')
assert NETWORK_DEFAULT.allow_api_request is False  # 既有预设不变
print('policy fields OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/hongluisi/policy.py
git commit -m "$(cat <<'EOF'
feat(policy): NetworkPolicy 扩 api_request 三字段

引入: allow_api_request, api_request_methods, api_request_rate_per_min, web_extract_rate_per_min
使用者: Task 5 profile 预设, Task 8/10 api_request 引擎
关闭: 默认 allow_api_request=False，不影响既有 fetch/search 流程
EOF
)"
```

---

## Task 5: 3 档 profile 对 L1 差异化

**Files:**
- Modify: `src/tianshu/tools/hongluisi/policy.py`（3 个常量）

- [ ] **Step 1: 更新 3 档常量**

修改 `src/tianshu/tools/hongluisi/policy.py` 末尾的三档常量：
```python
NETWORK_OFFLINE = NetworkPolicy(
    fetch_engines=(),
    search_provider=None,
    allow_api_request=False,
    api_request_methods=(),
)

NETWORK_DEFAULT = NetworkPolicy(
    fetch_engines=("local", "jina"),
    fallback_mode="on_error_or_empty",
    search_provider="tavily",
    allow_api_request=False,
    api_request_methods=(),
)

NETWORK_RESEARCH = NetworkPolicy(
    fetch_engines=("local", "jina", "firecrawl"),
    fallback_mode="on_error_or_empty",
    search_provider="tavily",
    allow_api_request=True,
    api_request_methods=("GET", "HEAD"),  # 写方法需 Edict 额外显式启用
)
```

- [ ] **Step 2: Smoke check**

```bash
python -c "
from tianshu.tools.hongluisi.policy import NETWORK_OFFLINE, NETWORK_DEFAULT, NETWORK_RESEARCH
assert NETWORK_OFFLINE.allow_api_request is False
assert NETWORK_DEFAULT.allow_api_request is False
assert NETWORK_RESEARCH.allow_api_request is True
assert 'GET' in NETWORK_RESEARCH.api_request_methods
assert 'POST' not in NETWORK_RESEARCH.api_request_methods
print('profile presets OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/hongluisi/policy.py
git commit -m "$(cat <<'EOF'
feat(policy): 3 档 profile 对 L1 差异化

引入: NETWORK_RESEARCH 启用 api_request (GET/HEAD)；OFFLINE/DEFAULT 保持关闭
使用者: Task 13 NetworkSafetyRule
关闭: 写方法任何 profile 默认都不开，必须 Edict 层单独 opt-in
EOF
)"
```

---

## Task 6: EdictRuntime 扩 host 白名单字段

**Files:**
- Modify: `src/tianshu/models/edict.py`
- Modify: `src/tianshu/models/api.py`

- [ ] **Step 1: 扩 EdictRuntime dataclass**

修改 `src/tianshu/models/edict.py`：
```python
@dataclass(frozen=True)
class EdictRuntime:
    # 既有字段保持不变
    policy_profile: "PolicyProfilePayload"
    fetch_engine_override: str | None = None
    search_provider_override: str | None = None
    # 新增 ↓
    api_request_hosts: tuple[str, ...] = ()       # 允许 api_request 打的 host（读）
    api_request_write_hosts: tuple[str, ...] = () # 允许写方法的 host (⊆ api_request_hosts)
```

注意项目若用 pydantic BaseModel 定义 EdictRuntime，字段用 `Field(default_factory=tuple)`，类型写 `tuple[str, ...]`。按实际源文件现有模式对齐。

- [ ] **Step 2: 扩 EdictRuntimeRequest**

修改 `src/tianshu/models/api.py` 的 `EdictRuntimeRequest`，添加同样两个字段（pydantic BaseModel + Field description）：
```python
api_request_hosts: list[str] = Field(
    default_factory=list,
    description="允许 api_request 调用的 host 列表（读方法）",
)
api_request_write_hosts: list[str] = Field(
    default_factory=list,
    description="允许 api_request 写方法 (POST/PUT/DELETE/PATCH) 的 host；必须是 api_request_hosts 的子集",
)
```

并在 request → runtime 的转换代码里把 list 转 tuple。

- [ ] **Step 3: Smoke check**

```bash
python -c "
from tianshu.models.edict import EdictRuntime
from tianshu.tools.policy_profile import BUILTIN_TEMPLATES
tmpl = list(BUILTIN_TEMPLATES.values())[0]
from tianshu.models.api import EdictRuntimeRequest
r = EdictRuntimeRequest(policy_profile={'template_name': list(BUILTIN_TEMPLATES.keys())[0]})
assert r.api_request_hosts == []
assert r.api_request_write_hosts == []
print('edict runtime fields OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/models/edict.py src/tianshu/models/api.py
git commit -m "$(cat <<'EOF'
feat(edict): EdictRuntime 扩 api_request_hosts / write_hosts

引入: api_request_hosts (读白名单), api_request_write_hosts (写白名单)
使用者: Task 13 NetworkSafetyRule, Task 17 Edict API 子集校验
关闭: 默认空 tuple，既有 Edict 迁移无感（fallback 不启用 api_request）
EOF
)"
```

---

## Task 7: FirecrawlExtractEngine

**Files:**
- Create: `src/tianshu/tools/hongluisi/engines/firecrawl_extract.py`

- [ ] **Step 1: 实现 FirecrawlExtractEngine**

创建 `src/tianshu/tools/hongluisi/engines/firecrawl_extract.py`：
```python
"""Firecrawl /v1/extract engine。Spec Section 6。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from tianshu.tools.hongluisi.http_client import SharedHttpClient
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

FIRECRAWL_EXTRACT_URL = "https://api.firecrawl.dev/v1/extract"


@dataclass(frozen=True)
class ExtractOutcome:
    data: dict | None
    status: str           # "ok" | "error"
    reason: str | None
    http_status: int | None


class FirecrawlExtractEngine:
    name = "firecrawl_extract"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def extract(
        self, url: str, schema: dict, prompt: str | None = None
    ) -> ExtractOutcome:
        try:
            clean_url = await validate_url(url)
        except SSRFViolation as v:
            return ExtractOutcome(None, "error", v.code, None)

        payload: dict[str, Any] = {"urls": [clean_url], "schema": schema}
        if prompt:
            payload["prompt"] = prompt

        client = SharedHttpClient.instance()
        try:
            resp = await client._client.post(
                FIRECRAWL_EXTRACT_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TimeoutException:
            return ExtractOutcome(None, "error", "timeout", None)
        except httpx.HTTPError as e:
            return ExtractOutcome(None, "error", f"http_error:{type(e).__name__}", None)

        if resp.status_code >= 400:
            return ExtractOutcome(
                None, "error", f"http_status:{resp.status_code}", resp.status_code
            )
        body = resp.json()
        if not body.get("success"):
            return ExtractOutcome(
                None, "error", "firecrawl_unsuccess", resp.status_code
            )
        return ExtractOutcome(body.get("data"), "ok", None, resp.status_code)


def build_firecrawl_extract() -> FirecrawlExtractEngine | None:
    key = os.getenv("TIANSHU_FIRECRAWL_API_KEY")
    if not key:
        return None
    return FirecrawlExtractEngine(api_key=key)
```

- [ ] **Step 2: Smoke check（结构检查，不打网）**

```bash
python -c "
from tianshu.tools.hongluisi.engines.firecrawl_extract import FirecrawlExtractEngine, build_firecrawl_extract
e = FirecrawlExtractEngine('fake-key')
assert e.name == 'firecrawl_extract'
print('firecrawl_extract OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/hongluisi/engines/firecrawl_extract.py
git commit -m "$(cat <<'EOF'
feat(hongluisi): FirecrawlExtractEngine (L4 backend)

引入: FirecrawlExtractEngine + build_firecrawl_extract
使用者: Task 11 web_extract 工具
关闭: TIANSHU_FIRECRAWL_API_KEY 缺失 → build 返回 None，上游工具不注册
EOF
)"
```

---

## Task 8: api_request engine wrapper

**Files:**
- Create: `src/tianshu/tools/hongluisi/api_request.py`
- Modify: `src/tianshu/tools/hongluisi/http_client.py`（cache key 加 credential_name）

- [ ] **Step 1: 改 cache key**

修改 `src/tianshu/tools/hongluisi/http_client.py` 的 `get_cached` 方法，把 cache key 从 `(url, engine)` 扩为 `(url, engine, credential_name)`：
```python
async def get_cached(
    self,
    url: str,
    *,
    engine: str,
    credential_name: str | None = None,
) -> tuple[str, dict, bool]:
    key = (url, engine, credential_name)
    # ... 其余逻辑保持
```

既有调用点（`local_fetch.py`, `jina_reader.py`）保持不传 `credential_name` 即可（默认 None）。

- [ ] **Step 2: 实现 api_request engine**

创建 `src/tianshu/tools/hongluisi/api_request.py`：
```python
"""api_request engine：封装 HTTP + 凭证注入。Spec Section 5。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from tianshu.secrets import (
    CredentialConflict,
    CredentialInjector,
    ForbiddenHeader,
)
from tianshu.tools.hongluisi.http_client import BodyTooLarge, SharedHttpClient
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

READ_METHODS = frozenset({"GET", "HEAD"})
WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})
ALL_METHODS = READ_METHODS | WRITE_METHODS


@dataclass(frozen=True)
class ApiResponse:
    status: str           # "ok" | "error"
    http_status: int | None
    headers: dict[str, str]
    body: str
    bytes_read: int
    reason: str | None
    credential_name: str | None
    truncated: bool


class ApiRequestEngine:
    def __init__(self, injector: CredentialInjector) -> None:
        self._injector = injector

    async def request(
        self,
        *,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> ApiResponse:
        headers = headers or {}

        # 1. 方法白名单
        method = method.upper()
        if method not in ALL_METHODS:
            return self._err(f"method_not_supported:{method}")

        # 2. SSRF
        try:
            clean_url = await validate_url(url)
        except SSRFViolation as v:
            return self._err(v.code)

        # 3. 用户 headers 黑名单 + 凭证注入
        host = urlparse(clean_url).hostname or ""
        try:
            inj = self._injector.inject(host, headers)
        except ForbiddenHeader as e:
            return self._err(f"forbidden_header:{e.header}")
        except CredentialConflict as e:
            return self._err(f"credential_conflict:{e.header}")

        # 4. 发请求
        client = SharedHttpClient.instance()
        try:
            resp = await client._client.request(
                method,
                clean_url,
                headers=inj.merged_headers,
                params=query,
                json=json_body if method not in READ_METHODS else None,
            )
        except httpx.TimeoutException:
            return self._err("timeout", credential_name=inj.credential_name)
        except httpx.HTTPError as e:
            return self._err(
                f"http_error:{type(e).__name__}",
                credential_name=inj.credential_name,
            )

        # 5. 读 body（带上限）
        try:
            body_bytes = resp.content
        except Exception as e:
            return self._err(
                f"read_body_error:{type(e).__name__}",
                credential_name=inj.credential_name,
            )

        if len(body_bytes) > SharedHttpClient.MAX_BODY_BYTES:
            return self._err(
                f"response_too_large:{len(body_bytes)}",
                credential_name=inj.credential_name,
            )

        body_text = body_bytes.decode("utf-8", errors="replace")
        truncated = False
        if len(body_text) > 16000:
            body_text = body_text[:16000]
            truncated = True

        status = "ok" if resp.status_code < 400 else "error"
        return ApiResponse(
            status=status,
            http_status=resp.status_code,
            headers={k: v for k, v in resp.headers.items() if k.lower() != "set-cookie"},
            body=body_text,
            bytes_read=len(body_bytes),
            reason=None if status == "ok" else f"http_status:{resp.status_code}",
            credential_name=inj.credential_name,
            truncated=truncated,
        )

    @staticmethod
    def _err(reason: str, credential_name: str | None = None) -> ApiResponse:
        return ApiResponse(
            status="error",
            http_status=None,
            headers={},
            body="",
            bytes_read=0,
            reason=reason,
            credential_name=credential_name,
            truncated=False,
        )
```

- [ ] **Step 3: Smoke check**

```bash
python -c "
import asyncio, os, tempfile
from cryptography.fernet import Fernet
os.environ['TIANSHU_SECRET_MASTER_KEY'] = Fernet.generate_key().decode()
from tianshu.storage import Storage
from tianshu.secrets import get_vault, CredentialStore, CredentialInjector
from tianshu.tools.hongluisi.api_request import ApiRequestEngine
db = tempfile.mktemp(suffix='.db')
s = Storage(db); s.init_db()
store = CredentialStore(s, get_vault())
inj = CredentialInjector(store)
eng = ApiRequestEngine(inj)
# SSRF check
r = asyncio.run(eng.request(url='http://127.0.0.1/x'))
assert r.status == 'error' and 'ssrf' in (r.reason or '')
# Forbidden header
r = asyncio.run(eng.request(url='https://example.com', headers={'Authorization': 'x'}))
assert 'forbidden_header' in r.reason
print('api_request engine OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/tools/hongluisi/api_request.py src/tianshu/tools/hongluisi/http_client.py
git commit -m "$(cat <<'EOF'
feat(hongluisi): api_request engine (HttpClient + credential injection)

引入: ApiRequestEngine.request(), cache key 扩入 credential_name
使用者: Task 10 api_request 工具注册
关闭: 引擎无状态，失败全部走 ApiResponse.status=error + reason 字段
EOF
)"
```

---

## Task 9: web_fetch / web_search 工具注册

承接原 Task 16，但并入新 tools.py。

**Files:**
- Create: `src/tianshu/tools/hongluisi/tools.py`（先只注册 fetch/search；Task 10/11/12 增量补 api_request/extract/ambient）

- [ ] **Step 1: 先创建 tools.py 骨架 + fetch/search 注册函数**

创建 `src/tianshu/tools/hongluisi/tools.py`：
```python
"""鸿胪寺工具注册入口。Spec Section 5-6 + 原 Plan §9。"""

from __future__ import annotations

import logging
from typing import Callable

from tianshu.tools.hongluisi.engine_registry import build_engines
from tianshu.tools.hongluisi.rate_limiter import get_rate_limiter
from tianshu.tools.hongluisi.router import FetchRouter
from tianshu.tools.types import (
    ToolDefinition,
    ToolResult,
    ToolTier,
    error_result,
    ok_result,
)

logger = logging.getLogger(__name__)


def _resolve_edict_context(edict_getter: Callable) -> tuple[str, object, str | None, str | None]:
    """从 ambient ContextVar 拿当前 Edict，解析 NetworkPolicy + override。"""
    edict = edict_getter()
    if edict is None:
        raise RuntimeError("no ambient edict; tool called outside executor")
    # 解析 NetworkPolicy：优先 edict.runtime.policy_profile.template_name
    from tianshu.tools.hongluisi.policy import NetworkPolicy
    from tianshu.tools.policy_profile import BUILTIN_TEMPLATES

    net = NetworkPolicy()
    tmpl_name = getattr(edict.runtime.policy_profile, "template_name", None)
    if tmpl_name and tmpl_name in BUILTIN_TEMPLATES:
        net = BUILTIN_TEMPLATES[tmpl_name].network
    fe_ov = getattr(edict.runtime, "fetch_engine_override", None)
    sp_ov = getattr(edict.runtime, "search_provider_override", None)
    return edict.id, net, fe_ov, sp_ov


def _register_web_fetch(registry, fetch_engines, edict_getter):
    async def handler(args: dict) -> ToolResult:
        url = args.get("url", "")
        edict_id, net, fe_ov, _ = _resolve_edict_context(edict_getter)
        if not net.fetch_engines and fe_ov is None:
            return error_result("fetch_not_allowed_in_profile")

        # rate limit
        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "web_fetch", net.web_fetch_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        router = FetchRouter(fetch_engines, net, override=fe_ov)
        outcome, attempts = await router.dispatch(url)
        if outcome.status == "ok":
            return ok_result(outcome.content)
        return error_result(outcome.reason or "fetch_failed")

    registry.register(
        ToolDefinition(
            name="web_fetch",
            description=(
                "Fetch a public web page and return its readable text as Markdown. "
                "Only public URLs are allowed; internal/private IPs are rejected."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=16000,
        ),
        handler,
    )


def _register_web_search(registry, search_providers, edict_getter):
    async def handler(args: dict) -> ToolResult:
        query = args.get("query", "")
        max_results = args.get("max_results", 5)
        edict_id, net, _, sp_ov = _resolve_edict_context(edict_getter)
        provider_name = sp_ov or net.search_provider
        if provider_name is None:
            return error_result("search_not_allowed_in_profile")
        provider = search_providers.get(provider_name)
        if provider is None:
            return error_result(f"provider_not_registered:{provider_name}")

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "web_search", net.web_search_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        try:
            outcome = await provider.search(query, max_results=max_results)
        except Exception as e:
            return error_result(f"provider_error:{type(e).__name__}")

        if not outcome.results:
            return error_result("search_empty")

        # 渲染为 markdown 列表；SearchOutcome 只有 results+raw_api_meta
        lines = []
        for i, r in enumerate(outcome.results, 1):
            lines.append(f"### {i}. [{r.title}]({r.url})")
            if r.snippet:
                lines.append(r.snippet)
            lines.append("")
        return ok_result("\n".join(lines))

    registry.register(
        ToolDefinition(
            name="web_search",
            description=(
                "Search the public web and return ranked result summaries. "
                "Use for discovery; follow up with web_fetch for full content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {
                        "type": "integer", "minimum": 1, "maximum": 10, "default": 5
                    },
                },
                "required": ["query"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=8000,
        ),
        handler,
    )


def register_hongluisi(registry, edict_getter: Callable) -> None:
    """启动期调用一次。edict_getter 从 Task 12 的 ambient.py 注入。"""
    fetch_engines, search_providers = build_engines()

    _register_web_fetch(registry, fetch_engines, edict_getter)
    if search_providers:
        _register_web_search(registry, search_providers, edict_getter)

    logger.info(
        "[hongluisi] registered: web_fetch, web_search (providers: %s)",
        list(search_providers),
    )
```

注意：`edict_getter` 在 Task 12 前是一个 placeholder 闭包；当前 commit 不挂到 builtins，所以不会真执行。Task 12 才 wire 起来。

- [ ] **Step 2: Smoke check（导入）**

```bash
python -c "
from tianshu.tools.hongluisi.tools import register_hongluisi
print('tools.py imports OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/hongluisi/tools.py
git commit -m "$(cat <<'EOF'
feat(hongluisi): web_fetch / web_search 工具注册

引入: tools.py 骨架, _register_web_fetch, _register_web_search, register_hongluisi
使用者: Task 12 ambient wire-up
关闭: 未挂到 builtins.py 前不会真执行；edict_getter 抛 RuntimeError 兜底
EOF
)"
```

---

## Task 10: api_request 工具注册

**Files:**
- Modify: `src/tianshu/tools/hongluisi/tools.py`

- [ ] **Step 1: 追加注册函数**

在 `tools.py` 末尾、`register_hongluisi` 之前追加：
```python
def _register_api_request(registry, api_engine, edict_getter):
    async def handler(args: dict) -> ToolResult:
        url = args.get("url", "")
        method = args.get("method", "GET")
        headers = args.get("headers") or {}
        query = args.get("query") or {}
        json_body = args.get("json_body")

        edict_id, net, _, _ = _resolve_edict_context(edict_getter)

        if not net.allow_api_request:
            return error_result("api_request_not_allowed_in_profile")

        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        edict = edict_getter()
        allow_hosts = tuple(edict.runtime.api_request_hosts)
        if host not in allow_hosts:
            return error_result("host_not_whitelisted")

        if method.upper() in {"POST", "PUT", "DELETE", "PATCH"}:
            write_hosts = tuple(edict.runtime.api_request_write_hosts)
            if host not in write_hosts:
                return error_result("write_method_host_not_whitelisted")
            # 写方法审批路径由 NetworkSafetyRule 拦截（Task 13-14），这里到达即已批

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "api_request", net.api_request_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        resp = await api_engine.request(
            url=url, method=method, headers=headers, query=query, json_body=json_body
        )
        if resp.status != "ok":
            return error_result(resp.reason or "api_request_failed")

        import json as _json
        return ok_result(
            _json.dumps(
                {
                    "status": resp.http_status,
                    "headers": resp.headers,
                    "body": resp.body,
                    "truncated": resp.truncated,
                },
                ensure_ascii=False,
            )
        )

    registry.register(
        ToolDefinition(
            name="api_request",
            description=(
                "Make an HTTP request to a whitelisted external API. "
                "Credentials are managed by the system — do not pass Authorization/Cookie/X-Api-Key "
                "headers. Methods GET/HEAD are read-only; POST/PUT/DELETE/PATCH require approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"],
                        "default": "GET",
                    },
                    "headers": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "query": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "json_body": {"type": ["object", "array", "null"]},
                },
                "required": ["url"],
            },
            tier=ToolTier.T2_NETWORK.value,   # 写方法由 PolicyRule 在运行时升级
            max_result_chars=16000,
        ),
        handler,
    )
```

- [ ] **Step 2: 扩展 register_hongluisi 签名**

修改 `register_hongluisi` 让它接收 `api_engine` 和 `extract_engine`（先都为 None 可选）：
```python
def register_hongluisi(
    registry,
    edict_getter: Callable,
    *,
    api_engine=None,
    extract_engine=None,
) -> None:
    fetch_engines, search_providers = build_engines()

    _register_web_fetch(registry, fetch_engines, edict_getter)
    if search_providers:
        _register_web_search(registry, search_providers, edict_getter)
    if api_engine is not None:
        _register_api_request(registry, api_engine, edict_getter)

    logger.info(
        "[hongluisi] registered: web_fetch, web_search (%s), api_request=%s, web_extract=%s",
        list(search_providers),
        api_engine is not None,
        extract_engine is not None,
    )
```

- [ ] **Step 3: Smoke check**

```bash
python -c "
from tianshu.tools.hongluisi.tools import _register_api_request, register_hongluisi
print('api_request registration OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/tools/hongluisi/tools.py
git commit -m "$(cat <<'EOF'
feat(hongluisi): api_request 工具注册

引入: _register_api_request, 在 register_hongluisi 里按 api_engine!=None 挂入
使用者: Task 12 ambient wire
关闭: api_engine 为 None 时工具不注册；写方法 host 白名单 + 限流双层防护
EOF
)"
```

---

## Task 11: web_extract 工具注册

**Files:**
- Modify: `src/tianshu/tools/hongluisi/tools.py`

- [ ] **Step 1: 追加 web_extract 注册函数**

在 `tools.py` 里 `_register_api_request` 之后追加：
```python
def _register_web_extract(registry, extract_engine, edict_getter):
    async def handler(args: dict) -> ToolResult:
        url = args.get("url", "")
        schema = args.get("schema") or {}
        prompt = args.get("prompt")

        edict_id, net, _, _ = _resolve_edict_context(edict_getter)
        if "firecrawl" not in net.fetch_engines:
            return error_result("web_extract_not_allowed_in_profile")

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "web_extract", net.web_extract_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        outcome = await extract_engine.extract(url, schema, prompt=prompt)
        if outcome.status != "ok":
            return error_result(outcome.reason or "extract_failed")

        import json as _json
        return ok_result(_json.dumps(outcome.data, ensure_ascii=False))

    registry.register(
        ToolDefinition(
            name="web_extract",
            description=(
                "Extract structured data from a public web page using an AI extractor. "
                "Provide a JSON schema describing fields to extract."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "schema": {"type": "object"},
                    "prompt": {"type": "string"},
                },
                "required": ["url", "schema"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=8000,
        ),
        handler,
    )
```

把 `register_hongluisi` 里的判断补齐：
```python
if extract_engine is not None:
    _register_web_extract(registry, extract_engine, edict_getter)
```

- [ ] **Step 2: Smoke check**

```bash
python -c "
from tianshu.tools.hongluisi.tools import _register_web_extract
print('web_extract registration OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/hongluisi/tools.py
git commit -m "$(cat <<'EOF'
feat(hongluisi): web_extract 工具注册

引入: _register_web_extract (Firecrawl /extract)
使用者: Task 12 ambient wire
关闭: extract_engine 为 None 或 profile 不含 firecrawl 时工具不可用
EOF
)"
```

---

## Task 12: register_hongluisi + ambient Edict ContextVar

**Files:**
- Create: `src/tianshu/executor/ambient.py`
- Modify: `src/tianshu/tools/builtins.py`
- Modify: `src/tianshu/executor/agent.py`（tool 调用前后 set/reset）

- [ ] **Step 1: 创建 ambient.py**

创建 `src/tianshu/executor/ambient.py`：
```python
"""ambient Edict ContextVar：让 tool handler 拿到当前 Edict，不用显式传参。"""

from __future__ import annotations

from contextvars import ContextVar, Token
from contextlib import contextmanager

from tianshu.models import Edict

_current_edict: ContextVar[Edict | None] = ContextVar("current_edict", default=None)


def get_current_edict() -> Edict | None:
    return _current_edict.get()


@contextmanager
def bind_edict(edict: Edict):
    token: Token = _current_edict.set(edict)
    try:
        yield
    finally:
        _current_edict.reset(token)
```

- [ ] **Step 2: 在 agent.py 的 tool 执行点包裹 bind_edict**

修改 `src/tianshu/executor/agent.py` 的 tool 调用路径（大约在 `self._tools.execute(tc["name"], tc["args"])` 处），用 `with bind_edict(self._edict):` 包起来：
```python
from tianshu.executor.ambient import bind_edict

# 原代码：
# result = await self._tools.execute(tc["name"], tc["args"])

# 改为：
with bind_edict(self._edict):
    result = await self._tools.execute(tc["name"], tc["args"])
```

- [ ] **Step 3: 在 builtins.py 挂 register_hongluisi**

修改 `src/tianshu/tools/builtins.py` 的 `register_builtins` 函数末尾：
```python
from tianshu.executor.ambient import get_current_edict
from tianshu.secrets import CredentialInjector, CredentialStore, get_vault
from tianshu.tools.hongluisi.api_request import ApiRequestEngine
from tianshu.tools.hongluisi.engines.firecrawl_extract import build_firecrawl_extract
from tianshu.tools.hongluisi.tools import register_hongluisi

# 在 register_builtins 末尾追加：
def _build_hongluisi_deps(storage):
    vault = get_vault()
    api_engine = None
    extract_engine = build_firecrawl_extract()
    if vault is not None and storage is not None:
        store = CredentialStore(storage, vault)
        injector = CredentialInjector(store)
        api_engine = ApiRequestEngine(injector)
    return api_engine, extract_engine


def register_builtins(registry, workspace_dir, storage=None):  # 加 storage 参数
    # ... 既有逻辑 ...
    api_engine, extract_engine = _build_hongluisi_deps(storage)
    register_hongluisi(
        registry,
        edict_getter=get_current_edict,
        api_engine=api_engine,
        extract_engine=extract_engine,
    )
```

调用 `register_builtins` 的地方（通常在 app.py 启动期）需要把 storage 实例透传过来。

- [ ] **Step 4: Smoke check**

```bash
# 启动应用看是否正常起
uvicorn tianshu.app:app --port 18765 &
sleep 3
curl -s http://127.0.0.1:18765/health
kill %1
```

Expected: 应用起成功，日志有 `[hongluisi] registered: web_fetch, web_search (...), api_request=True/False, web_extract=True/False`。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/executor/ambient.py src/tianshu/tools/builtins.py src/tianshu/executor/agent.py
git commit -m "$(cat <<'EOF'
feat(executor): register_hongluisi 主入口 + ambient Edict ContextVar

引入: ambient.py bind_edict/get_current_edict, builtins.py 挂 hongluisi
使用者: 所有网络工具；agent.py tool 执行前 bind_edict
关闭: storage/vault 缺失时 api_engine=None → api_request 不注册，其他工具仍可用
EOF
)"
```

---

## Task 13: NetworkSafetyRule 扩 4 工具分支

**Files:**
- Create: `src/tianshu/tools/policy_rules/network_safety.py`
- Modify: `src/tianshu/tools/policy_rules/__init__.py`（注册新 rule）

- [ ] **Step 1: 实现 NetworkSafetyRule**

创建 `src/tianshu/tools/policy_rules/network_safety.py`：
```python
"""NetworkSafetyRule：拦截 4 个网络工具，在 PolicyEngine 前置做白名单 + 审批判定。Spec §7.4。"""

from __future__ import annotations

from urllib.parse import urlparse

from tianshu.tools.hongluisi.policy import NetworkPolicy
from tianshu.tools.policy_profile import BUILTIN_TEMPLATES
from tianshu.tools.types import ToolTier

NETWORK_TOOLS = frozenset({"web_fetch", "web_search", "api_request", "web_extract"})
WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


class NetworkSafetyRule:
    priority = 75   # 在 default_tier (100) 之前，在 bash_safety (50) 之后

    def evaluate(self, ctx) -> "PolicyDecision":
        from tianshu.tools.policy_engine import PolicyDecision  # 按项目实际路径

        tool = ctx.tool_call.name
        if tool not in NETWORK_TOOLS:
            return PolicyDecision.pass_through()

        net = self._resolve_network_policy(ctx)

        if tool == "web_fetch":
            if not net.fetch_engines:
                return PolicyDecision.deny("fetch_not_allowed_in_profile")
            return PolicyDecision.pass_through()

        if tool == "web_search":
            if net.search_provider is None:
                return PolicyDecision.deny("search_not_allowed_in_profile")
            return PolicyDecision.pass_through()

        if tool == "api_request":
            return self._evaluate_api_request(ctx, net)

        if tool == "web_extract":
            if "firecrawl" not in net.fetch_engines:
                return PolicyDecision.deny("web_extract_not_allowed_in_profile")
            return PolicyDecision.pass_through()

        return PolicyDecision.pass_through()

    def _resolve_network_policy(self, ctx) -> NetworkPolicy:
        tmpl_name = getattr(
            ctx.edict.runtime.policy_profile, "template_name", None
        )
        if tmpl_name and tmpl_name in BUILTIN_TEMPLATES:
            return BUILTIN_TEMPLATES[tmpl_name].network
        return NetworkPolicy()

    def _evaluate_api_request(self, ctx, net: NetworkPolicy):
        from tianshu.tools.policy_engine import PolicyDecision

        if not net.allow_api_request:
            return PolicyDecision.deny("api_request_not_allowed_in_profile")

        url = ctx.tool_call.args.get("url", "")
        method = ctx.tool_call.args.get("method", "GET").upper()
        host = urlparse(url).hostname or ""

        allow_hosts = set(ctx.edict.runtime.api_request_hosts)
        if host not in allow_hosts:
            return PolicyDecision.deny("host_not_whitelisted")

        if method in WRITE_METHODS:
            write_hosts = set(ctx.edict.runtime.api_request_write_hosts)
            if host not in write_hosts:
                return PolicyDecision.deny("write_method_host_not_whitelisted")
            # 写方法 → 升级到 T3_WRITE 审批
            return PolicyDecision.require_approval(
                tier=ToolTier.T3_WRITE,
                reason=f"api_request {method} {host}",
            )

        if method not in net.api_request_methods:
            return PolicyDecision.deny(f"method_not_allowed:{method}")

        return PolicyDecision.pass_through()
```

> 注：`PolicyDecision` 的 API（`pass_through` / `deny` / `require_approval`）按项目 `policy_engine.py` 实际接口调整。Task 实施者需要看一眼 `src/tianshu/tools/policy_engine.py` 确认方法名。

- [ ] **Step 2: 注册到 default rules**

修改 `src/tianshu/tools/policy_rules/__init__.py` 的 `build_default_rules`（或类似函数）追加：
```python
from tianshu.tools.policy_rules.network_safety import NetworkSafetyRule

# 在 build_default_rules 的 rules 列表追加：
rules.append(NetworkSafetyRule())
```

- [ ] **Step 3: Smoke check**

```bash
python -c "
from tianshu.tools.policy_rules.network_safety import NetworkSafetyRule, NETWORK_TOOLS, WRITE_METHODS
assert 'web_fetch' in NETWORK_TOOLS
assert 'POST' in WRITE_METHODS
r = NetworkSafetyRule()
assert r.priority == 75
print('network safety rule OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/tools/policy_rules/network_safety.py src/tianshu/tools/policy_rules/__init__.py
git commit -m "$(cat <<'EOF'
feat(rules): NetworkSafetyRule (priority=75) 拦截 4 网络工具

引入: NetworkSafetyRule 4 分支 (fetch/search/api_request/extract)
使用者: PolicyEngine
关闭: rule 加入失败或 decision 路径异常 → 工具 handler 有第二道 host 白名单兜底
EOF
)"
```

---

## Task 14: 写方法审批路径接入 PolicyEngine

**Files:**
- Modify: `src/tianshu/tools/policy_engine.py` 或等价审批链入口
- Modify: `src/tianshu/executor/policy_hook.py`

- [ ] **Step 1: 确认审批路径存在**

检查 `src/tianshu/tools/policy_engine.py` 如何处理 `require_approval` 返回值。若既有 T3 审批路径（比如 shell_exec 的审批）已通用，则 Task 13 的 decision 会自动走到审批 UI，**本 task 无代码改动**。

验证方式：
```bash
grep -n "require_approval\|approval_required\|T3_WRITE" src/tianshu/tools/policy_engine.py src/tianshu/executor/policy_hook.py
```

- [ ] **Step 2: 若审批路径不通用，补上 tier 升级钩子**

如果发现 api_request 的 tier 是 `T2_NETWORK` 但审批 UI 只显示 tier ≥ T3 的工具，需要在 `policy_hook.py` 读 decision 的 `require_approval.tier` 并用它覆盖 tool 原始 tier：

```python
# 伪代码，按项目实际入参调整
if decision.kind == "require_approval":
    effective_tier = decision.tier      # T3_WRITE
else:
    effective_tier = tool_def.tier      # T2_NETWORK
# 审批判断用 effective_tier
```

- [ ] **Step 3: Smoke check**

启动应用，创建一个 trusted-automation Edict，配 `api_request_write_hosts=['httpbin.org']`，从 CLI/API 触发 `api_request(method=POST, url=https://httpbin.org/post)`：

```bash
# 期望：审批队列出现待批项，state=approval_required, reason="api_request POST httpbin.org"
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(rules): 写方法 api_request 接入 T3 审批链

引入: NetworkSafetyRule 的 require_approval 在 policy_engine 里生效
使用者: 审批 UI / ApprovalQueuePage
关闭: 若审批链断 → 写方法返回 deny 兜底，不会裸跑
EOF
)"
```

---

## Task 15: network 审计字段 + redact

**Files:**
- Modify: `src/tianshu/models/common.py`（AuditResult 加 network 字段）
- Modify: 审计记录写入点（通常是 `executor/agent.py` 或 `auditor/`）

- [ ] **Step 1: 扩 AuditResult**

修改 `src/tianshu/models/common.py` 的 `AuditResult`，加一个可选 network 子对象：
```python
class NetworkAuditDetail(BaseModel):
    tool: str
    host: str | None = None
    method: str | None = None
    path: str | None = None
    credential_name: str | None = None   # 永不写 credential value
    http_status: int | None = None
    bytes_in: int = 0
    bytes_out: int = 0
    cached: bool = False
    duration_ms: int | None = None
    rate_limit_remaining: int | None = None

class AuditResult(BaseModel):
    # 既有字段...
    network: NetworkAuditDetail | None = None
```

- [ ] **Step 2: 在 tool handler 里填充 network detail**

修改 `tools.py` 的 4 个 handler，把 host/method/credential_name/http_status 通过 audit hook 或返回值附加到 AuditResult。具体接入点视项目既有 audit 写入位置。

关键原则：**永远不写 `headers["Authorization"]` 等敏感 header**。使用 `redact_sensitive_headers` 包一层再写入。

- [ ] **Step 3: Smoke check**

构造一次 web_fetch 调用 → 看 AuditDashboard（后端 API）返回里 `network` 字段有值，且不含 Authorization。

```bash
curl -s http://127.0.0.1:18765/api/audit/recent | jq '.[0].network'
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/models/common.py src/tianshu/tools/hongluisi/tools.py
git commit -m "$(cat <<'EOF'
feat(audit): network 审计字段 + 敏感 header redact

引入: NetworkAuditDetail (host/method/credential_name/http_status/...)
使用者: AuditDashboardPage (Task 21), /api/audit 查询 (Task 18)
关闭: credential_name 仅记名，value 不记；使用 redact_sensitive_headers 二次防护
EOF
)"
```

---

## Task 16: /api/credentials CRUD endpoints

**Files:**
- Create: `src/tianshu/gateway/credentials_api.py`
- Modify: `src/tianshu/app.py`（include_router）

- [ ] **Step 1: 实现 router**

创建 `src/tianshu/gateway/credentials_api.py`：
```python
"""/api/credentials CRUD。Spec §8.1 后端。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tianshu.secrets import (
    CredentialCreate,
    CredentialStore,
    CredentialUpdate,
    CredentialView,
    get_vault,
)
from tianshu.storage import Storage


router = APIRouter(prefix="/api/credentials", tags=["credentials"])


def _store_dep(storage: Storage = Depends(...)) -> CredentialStore:  # 按项目 DI 方式调整
    vault = get_vault()
    if vault is None:
        raise HTTPException(503, "secret vault unavailable")
    return CredentialStore(storage, vault)


def _to_view(c) -> CredentialView:
    return CredentialView(
        id=c.id,
        name=c.name,
        host_pattern=c.host_pattern,
        header_template=c.header_template,
        extra_headers=c.extra_headers,
        created_at=c.created_at,
        updated_at=c.updated_at,
        last_used_at=c.last_used_at,
    )


@router.get("")
def list_credentials(store: CredentialStore = Depends(_store_dep)) -> list[CredentialView]:
    return [_to_view(c) for c in store.list_all()]


@router.post("")
def create_credential(
    req: CredentialCreate,
    store: CredentialStore = Depends(_store_dep),
) -> CredentialView:
    try:
        c = store.create(req)
    except Exception as e:
        raise HTTPException(400, f"create_failed:{e}") from e
    return _to_view(c)


@router.patch("/{cred_id}")
def update_credential(
    cred_id: str,
    req: CredentialUpdate,
    store: CredentialStore = Depends(_store_dep),
) -> CredentialView:
    c = store.update(cred_id, req)
    if c is None:
        raise HTTPException(404, "credential_not_found")
    return _to_view(c)


@router.delete("/{cred_id}")
def delete_credential(
    cred_id: str,
    store: CredentialStore = Depends(_store_dep),
) -> dict:
    # 阻止删除被 Edict 引用的凭证
    # 查询逻辑：遍历所有 Edict，查 api_request_hosts 是否引用此凭证的 host_pattern
    # 简化：只要 host_pattern 被任何活跃 Edict 用到就拒绝（在 Task 17 校验函数里实现）
    # 这里先只做基础删除
    if not store.delete(cred_id):
        raise HTTPException(404, "credential_not_found")
    return {"ok": True}
```

> DI 细节（`Depends(...)`）按项目 `app.py` 里 Storage 依赖注入的实际方式对齐（可能是 `Depends(get_storage)` 或 app state 读取）。

- [ ] **Step 2: 在 app.py 挂 router**

```python
from tianshu.gateway.credentials_api import router as credentials_router
app.include_router(credentials_router)
```

- [ ] **Step 3: Smoke check**

```bash
curl -s http://127.0.0.1:18765/api/credentials | jq .
curl -s -X POST http://127.0.0.1:18765/api/credentials \
  -H 'Content-Type: application/json' \
  -d '{"name":"test","host_pattern":"example.com","header_template":"Authorization: Bearer {value}","value":"xxx"}' | jq .
# 校验响应不含 "value" 字段
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/gateway/credentials_api.py src/tianshu/app.py
git commit -m "$(cat <<'EOF'
feat(api): /api/credentials CRUD endpoints

引入: GET/POST/PATCH/DELETE /api/credentials; 返回 CredentialView 不含 value
使用者: Task 19 前端 ExternalCredentialsTab
关闭: vault 未初始化返回 503；所有响应过 CredentialView 杜绝泄漏
EOF
)"
```

---

## Task 17: Edict API 校验 hosts 子集关系

**Files:**
- Modify: `src/tianshu/gateway/api.py` 的 Edict 创建/更新 endpoint

- [ ] **Step 1: 加校验函数**

在 Edict 创建/更新 handler 里（收到 `EdictRuntimeRequest` 之后、落库之前）追加：
```python
def _validate_network_runtime(req: EdictRuntimeRequest) -> None:
    allow = set(req.api_request_hosts)
    write = set(req.api_request_write_hosts)
    if not write.issubset(allow):
        extra = write - allow
        raise HTTPException(
            400,
            f"api_request_write_hosts must be ⊆ api_request_hosts; extra: {sorted(extra)}",
        )
```

并在 handler 开头调用它。

- [ ] **Step 2: 删除凭证时阻止引用**

在 `credentials_api.py` 的 `delete_credential` 里加引用检查：
```python
@router.delete("/{cred_id}")
def delete_credential(
    cred_id: str,
    store: CredentialStore = Depends(_store_dep),
    storage: Storage = Depends(...),
) -> dict:
    cred = store.get(cred_id)
    if cred is None:
        raise HTTPException(404, "credential_not_found")
    # 查活跃 Edict 引用
    refs = storage.find_edicts_referencing_host(cred.host_pattern)  # 新增 Storage 方法
    if refs:
        raise HTTPException(
            409,
            f"credential referenced by {len(refs)} active edict(s); remove references first",
        )
    store.delete(cred_id)
    return {"ok": True}
```

Storage 加方法 `find_edicts_referencing_host(pattern: str) -> list[str]` 返回 edict_id 列表。

- [ ] **Step 3: Smoke check**

```bash
# 1. 创建 Edict 时写白名单超集 → 400
curl -s -X POST http://127.0.0.1:18765/api/edicts \
  -H 'Content-Type: application/json' \
  -d '{"goal":"t","runtime":{"policy_profile":{"template_name":"..."},"api_request_hosts":["api.github.com"],"api_request_write_hosts":["api.notion.com"]}}'
# 期望 400 + 提示

# 2. 删除被引用凭证 → 409
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/gateway/api.py src/tianshu/gateway/credentials_api.py src/tianshu/storage.py
git commit -m "$(cat <<'EOF'
feat(api): Edict 校验 write_hosts ⊆ hosts + 凭证引用检查

引入: _validate_network_runtime, find_edicts_referencing_host, 删除引用检查
使用者: 前端 NetworkCapabilitySection 友好提示
关闭: 绑定关系被破坏时返回 400/409，前端提示用户调整后重试
EOF
)"
```

---

## Task 18: 审计查询按 host / credential 过滤

**Files:**
- Modify: 审计查询 endpoint（通常在 `src/tianshu/gateway/api.py` 的 `/api/audit` 相关路由）

- [ ] **Step 1: 扩 query 参数**

给现有 audit 列表 endpoint 追加 query params：
```python
@router.get("/api/audit/recent")
def audit_recent(
    host: str | None = None,
    credential_name: str | None = None,
    tool: str | None = None,
    limit: int = 50,
):
    rows = storage.list_audit(
        host=host, credential_name=credential_name, tool=tool, limit=limit
    )
    return rows
```

Storage 里对应 `list_audit` 加 WHERE 子句按这三个字段过滤（从 JSON `audit.network.{host,credential_name}` 提取）。

- [ ] **Step 2: Smoke check**

```bash
curl -s "http://127.0.0.1:18765/api/audit/recent?tool=api_request&host=api.github.com" | jq .
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/gateway/api.py src/tianshu/storage.py
git commit -m "$(cat <<'EOF'
feat(api): /api/audit 查询按 host / credential_name / tool 过滤

引入: /api/audit/recent 三个新 query 参数
使用者: AuditDashboardPage (Task 21) 筛选栏
关闭: 参数缺省时行为不变，兼容旧 UI
EOF
)"
```

---

## Task 19: 藏兵阁 - 外部凭证 tab

**Files:**
- Create: `web/src/components/system/ExternalCredentialsTab.tsx`
- Create: `web/src/api/credentials.ts`
- Modify: `web/src/api/types.ts`
- Modify: `web/src/pages/SystemManagementPage.tsx`（挂新 tab）

- [ ] **Step 1: API types**

`web/src/api/types.ts` 追加：
```typescript
export interface Credential {
  id: string;
  name: string;
  host_pattern: string;
  header_template: string;
  extra_headers: Record<string, string>;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface CredentialCreate {
  name: string;
  host_pattern: string;
  header_template: string;
  value: string;
  extra_headers?: Record<string, string>;
}
```

- [ ] **Step 2: API 客户端**

创建 `web/src/api/credentials.ts`：
```typescript
import { API_BASE } from "./config";
import type { Credential, CredentialCreate } from "./types";

export async function listCredentials(): Promise<Credential[]> {
  const r = await fetch(`${API_BASE}/api/credentials`);
  if (!r.ok) throw new Error(`list_credentials_failed:${r.status}`);
  return r.json();
}

export async function createCredential(req: CredentialCreate): Promise<Credential> {
  const r = await fetch(`${API_BASE}/api/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`create_credential_failed:${r.status}:${t}`);
  }
  return r.json();
}

export async function updateCredential(
  id: string,
  patch: { value?: string; extra_headers?: Record<string, string> }
): Promise<Credential> {
  const r = await fetch(`${API_BASE}/api/credentials/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`update_credential_failed:${r.status}`);
  return r.json();
}

export async function deleteCredential(id: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/credentials/${id}`, { method: "DELETE" });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`delete_credential_failed:${r.status}:${t}`);
  }
}
```

- [ ] **Step 3: Tab 组件**

创建 `web/src/components/system/ExternalCredentialsTab.tsx`：
```tsx
import { useEffect, useState } from "react";
import { Table, Button, Modal, TextInput, PasswordInput, Stack, Group, Code } from "@mantine/core";
import { listCredentials, createCredential, deleteCredential } from "../../api/credentials";
import type { Credential } from "../../api/types";

export function ExternalCredentialsTab() {
  const [items, setItems] = useState<Credential[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState({
    name: "",
    host_pattern: "",
    header_template: "Authorization: Bearer {value}",
    value: "",
  });

  const reload = () => listCredentials().then(setItems);
  useEffect(() => { reload(); }, []);

  const onCreate = async () => {
    await createCredential(form);
    setFormOpen(false);
    setForm({
      name: "", host_pattern: "",
      header_template: "Authorization: Bearer {value}", value: ""
    });
    reload();
  };

  const onDelete = async (id: string) => {
    if (!confirm("删除此凭证？引用它的 Edict 将无法再注入 header。")) return;
    try {
      await deleteCredential(id);
      reload();
    } catch (e: any) {
      alert(e.message);
    }
  };

  return (
    <Stack>
      <Group justify="flex-end">
        <Button onClick={() => setFormOpen(true)}>新增凭证</Button>
      </Group>

      <Table striped withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>名称</Table.Th>
            <Table.Th>匹配域</Table.Th>
            <Table.Th>Header 模板</Table.Th>
            <Table.Th>最近使用</Table.Th>
            <Table.Th>操作</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.map(c => (
            <Table.Tr key={c.id}>
              <Table.Td>{c.name}</Table.Td>
              <Table.Td><Code>{c.host_pattern}</Code></Table.Td>
              <Table.Td><Code>{c.header_template.replace(/\{value\}/, "•••")}</Code></Table.Td>
              <Table.Td>{c.last_used_at ?? "—"}</Table.Td>
              <Table.Td>
                <Button size="xs" color="red" variant="subtle"
                        onClick={() => onDelete(c.id)}>删除</Button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Modal opened={formOpen} onClose={() => setFormOpen(false)} title="新增凭证">
        <Stack>
          <TextInput label="名称" required
                     value={form.name}
                     onChange={e => setForm({...form, name: e.target.value})} />
          <TextInput label="匹配域 (例: api.github.com 或 *.notion.com)" required
                     value={form.host_pattern}
                     onChange={e => setForm({...form, host_pattern: e.target.value})} />
          <TextInput label="Header 模板"
                     description="用 {value} 占位"
                     value={form.header_template}
                     onChange={e => setForm({...form, header_template: e.target.value})} />
          <PasswordInput label="Value" required
                         value={form.value}
                         onChange={e => setForm({...form, value: e.target.value})} />
          <Button onClick={onCreate}>保存</Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
```

- [ ] **Step 4: 挂到 SystemManagementPage**

修改 `web/src/pages/SystemManagementPage.tsx`：
1. import `ExternalCredentialsTab`
2. 在现有 Tabs 配置末尾追加一个新 Tab：`{ value: "external-creds", label: "外部凭证", component: <ExternalCredentialsTab /> }` —— 结构对齐现有 SkillsTab/ToolsTab 风格。

- [ ] **Step 5: Smoke check**

```bash
cd web && pnpm dev
# 打开浏览器 → 藏兵阁 → "外部凭证" tab
# 新增一条 → 表格应出现且 header 模板显示为 Bearer •••
```

- [ ] **Step 6: Commit**

```bash
git add web/src/components/system/ExternalCredentialsTab.tsx web/src/api/credentials.ts web/src/api/types.ts web/src/pages/SystemManagementPage.tsx
git commit -m "$(cat <<'EOF'
feat(web): 藏兵阁 - 外部凭证 tab

引入: ExternalCredentialsTab + credentials API 客户端
使用者: 用户通过 UI 管理外部 API 凭证
关闭: 加载失败 / 删除被引用 → 弹 alert，表格保持最后一次成功状态
EOF
)"
```

---

## Task 20: Edict 创建页 - 网络能力 section

**Files:**
- Create: `web/src/components/edict/NetworkCapabilitySection.tsx`
- Modify: `web/src/pages/EdictCreatePage.tsx`

- [ ] **Step 1: 实现 NetworkCapabilitySection**

创建 `web/src/components/edict/NetworkCapabilitySection.tsx`：
```tsx
import { Checkbox, MultiSelect, Stack, Paper, Text, Tooltip } from "@mantine/core";
import { useEffect, useState } from "react";
import { listCredentials } from "../../api/credentials";

type Props = {
  profileTemplate: string;   // "safe-explore" | "refactor-in-place" | "trusted-automation"
  apiRequestHosts: string[];
  apiRequestWriteHosts: string[];
  onChange: (patch: {
    api_request_hosts?: string[];
    api_request_write_hosts?: string[];
  }) => void;
};

export function NetworkCapabilitySection(props: Props) {
  const [allowWrite, setAllowWrite] = useState(props.apiRequestWriteHosts.length > 0);
  const [credHosts, setCredHosts] = useState<string[]>([]);

  useEffect(() => {
    listCredentials().then(cs => setCredHosts(cs.map(c => c.host_pattern)));
  }, []);

  const disabled = props.profileTemplate !== "trusted-automation";
  const hostOptions = [...new Set([...credHosts, ...props.apiRequestHosts])];

  return (
    <Paper withBorder p="md">
      <Stack>
        <Text fw={600}>网络能力</Text>

        <Tooltip
          disabled={!disabled}
          label='需切换到 "trusted-automation" profile 才能启用 api_request'
        >
          <MultiSelect
            label="允许调用的 API host"
            description="api_request 工具仅允许访问这些 host"
            data={hostOptions}
            searchable creatable
            disabled={disabled}
            value={props.apiRequestHosts}
            onChange={hosts => props.onChange({ api_request_hosts: hosts })}
          />
        </Tooltip>

        <Checkbox
          label="允许写方法 (POST/PUT/DELETE/PATCH) — 需要审批"
          disabled={disabled}
          checked={allowWrite}
          onChange={e => {
            const v = e.currentTarget.checked;
            setAllowWrite(v);
            if (!v) props.onChange({ api_request_write_hosts: [] });
          }}
        />

        {allowWrite && !disabled && (
          <MultiSelect
            label="允许写入的 host（必须是上面列表的子集）"
            data={props.apiRequestHosts}
            value={props.apiRequestWriteHosts}
            onChange={hosts => props.onChange({ api_request_write_hosts: hosts })}
          />
        )}
      </Stack>
    </Paper>
  );
}
```

- [ ] **Step 2: 挂到 EdictCreatePage**

修改 `web/src/pages/EdictCreatePage.tsx`：import + 渲染 + 绑定 state。用 profile 选择器的值作为 `profileTemplate` prop，表单提交时把 `api_request_hosts` / `api_request_write_hosts` 放进 `runtime` payload。

- [ ] **Step 3: Smoke check**

```bash
cd web && pnpm dev
# 创建 Edict → 切 profile 到 trusted-automation → 勾选 host → 提交
# 查 DB: sqlite3 .../db "SELECT runtime FROM edicts WHERE id='..'"
# runtime JSON 应含 api_request_hosts 数组
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/edict/NetworkCapabilitySection.tsx web/src/pages/EdictCreatePage.tsx
git commit -m "$(cat <<'EOF'
feat(web): Edict 创建页 - 网络能力 section

引入: NetworkCapabilitySection (host multi-select + 写方法开关)
使用者: 用户在创建 Edict 时精细配置 api_request 白名单
关闭: profile != trusted-automation 时 section 灰色 + tooltip 引导
EOF
)"
```

---

## Task 21: AuditDashboard - 网络事件行

**Files:**
- Modify: `web/src/pages/AuditDashboardPage.tsx`
- Modify: `web/src/api/types.ts`

- [ ] **Step 1: 加类型**

`web/src/api/types.ts` 追加：
```typescript
export interface NetworkAuditDetail {
  tool: string;
  host: string | null;
  method: string | null;
  path: string | null;
  credential_name: string | null;
  http_status: number | null;
  bytes_in: number;
  bytes_out: number;
  cached: boolean;
  duration_ms: number | null;
  rate_limit_remaining: number | null;
}
```

并扩展 `AuditRecord`（或类似名称）加入 `network?: NetworkAuditDetail`。

- [ ] **Step 2: 在 AuditDashboardPage 渲染**

修改 `web/src/pages/AuditDashboardPage.tsx`：
- 在现有表格行里，检测 `row.network` 存在时额外渲染一列：
  ```tsx
  {row.network && (
    <Badge color={row.network.http_status && row.network.http_status < 400 ? "green" : "red"}>
      {row.network.method} {row.network.host} [{row.network.http_status ?? "—"}]
      {row.network.credential_name && ` · ${row.network.credential_name}`}
    </Badge>
  )}
  ```

- 顶部筛选区增加 host / credential_name / tool 三个下拉/输入，提交时用 `/api/audit/recent?host=...&credential_name=...`。

- [ ] **Step 3: Smoke check**

```bash
cd web && pnpm dev
# 触发一次 web_fetch → AuditDashboard 应看到带 host / method / status 的徽章
```

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/AuditDashboardPage.tsx web/src/api/types.ts
git commit -m "$(cat <<'EOF'
feat(web): AuditDashboard 渲染 network 审计事件

引入: NetworkAuditDetail 类型, 表格 host/method/status 徽章, 顶部 host/credential 筛选
使用者: ops 排查 network 工具使用情况
关闭: row.network 缺失时行为不变
EOF
)"
```

---

## Task 22: 宫殿首页 "外部感知" 解锁提示

**Files:**
- Modify: `web/src/pages/CabinetPage.tsx` 或首页组件（按项目实际）

- [ ] **Step 1: 定位首页能力卡片**

```bash
grep -rn "工具\|能力\|解锁\|skill" web/src/pages/ | head -10
```
找到能力展示区（如 CabinetPage 里的 tool list 或 home 的 capability cards）。

- [ ] **Step 2: 加 "外部感知" 入口**

在能力卡片区追加一张卡片：
```tsx
<Card>
  <Text fw={600}>外部感知</Text>
  <Text size="sm" c="dimmed">
    读网页 / 搜索 / 调 API / 结构化抽取，凭证由藏兵阁托管
  </Text>
  <Group mt="sm">
    <Button size="xs" variant="light" component="a" href="/system?tab=external-creds">
      管理凭证
    </Button>
    <Button size="xs" variant="subtle" component="a" href="/docs/skills">
      查看技能
    </Button>
  </Group>
</Card>
```

- [ ] **Step 3: Smoke check**

```bash
cd web && pnpm dev
# 首页应能看到 "外部感知" 卡片，点击能跳到藏兵阁
```

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/CabinetPage.tsx
git commit -m "$(cat <<'EOF'
feat(web): 宫殿首页展示 "外部感知" 能力卡片

引入: CabinetPage 能力区加外部感知卡片 + 入口链接
使用者: 用户概览已解锁能力
关闭: 无能力时默认卡片仍显示（跳转链接正常）
EOF
)"
```

---

## Task 23: 测试统一补齐

**Files:**
- Create: `tests/secrets/test_vault.py`
- Create: `tests/secrets/test_store.py`
- Create: `tests/secrets/test_injector.py`
- Create: `tests/tools/hongluisi/test_api_request.py`
- Create: `tests/tools/hongluisi/test_web_extract.py`
- Create: `tests/tools/hongluisi/test_tools_registration.py`
- Create: `tests/tools/policy_rules/test_network_safety.py`
- Create: `tests/gateway/test_credentials_api.py`
- Create: `tests/integration/test_api_request_e2e.py`

- [ ] **Step 1: SecretVault 单测**

`tests/secrets/test_vault.py`：
```python
import os
import pytest
from cryptography.fernet import Fernet

from tianshu.secrets.vault import SecretVault, get_vault, reset_vault


@pytest.fixture
def master_key():
    return Fernet.generate_key().decode()


def test_encrypt_decrypt_roundtrip(master_key):
    v = SecretVault(master_key)
    enc = v.encrypt("secret-token")
    assert v.decrypt(enc) == "secret-token"


def test_decrypt_invalid_raises(master_key):
    v = SecretVault(master_key)
    with pytest.raises(ValueError, match="decryption failed"):
        v.decrypt(b"not-a-fernet-token")


def test_get_vault_returns_none_without_key(monkeypatch):
    reset_vault()
    monkeypatch.delenv("TIANSHU_SECRET_MASTER_KEY", raising=False)
    assert get_vault() is None


def test_get_vault_singleton(monkeypatch, master_key):
    reset_vault()
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)
    v1 = get_vault()
    v2 = get_vault()
    assert v1 is v2
```

- [ ] **Step 2: CredentialStore 单测**

`tests/secrets/test_store.py`：测试 create / list_all / get / find_for_host / update / delete / mark_used / 通配符匹配、字面优先。

- [ ] **Step 3: CredentialInjector 单测**

`tests/secrets/test_injector.py`：
- 命中凭证 → header 注入正确、value 正确
- 无命中 → user headers 原样返回
- user 传 Authorization → ForbiddenHeader
- user 传的 header 与 extra_headers 冲突 → CredentialConflict
- `redact_sensitive_headers` 覆盖所有黑名单 header

- [ ] **Step 4: api_request 工具测**

`tests/tools/hongluisi/test_api_request.py`：用 `httpx.MockTransport` 模拟服务端：
- SSRF 拒绝（127.0.0.1, 169.254.169.254, userinfo 里含 @）
- forbidden_header
- credential_conflict
- 大响应截断
- 超时 / http_error
- GET / POST 成功流
- 凭证命中时 Authorization 正确拼接

- [ ] **Step 5: web_extract 工具测**

`tests/tools/hongluisi/test_web_extract.py`：MockTransport 返回 Firecrawl 风格 JSON，验证 schema 匹配 / unsuccess 分支 / 大响应处理。

- [ ] **Step 6: NetworkSafetyRule 测**

`tests/tools/policy_rules/test_network_safety.py`：
- OFFLINE profile + web_fetch → deny
- DEFAULT + api_request → deny (profile 不允许)
- RESEARCH + api_request GET api.github.com（在 hosts） → pass
- RESEARCH + api_request POST api.github.com（不在 write_hosts） → deny
- RESEARCH + api_request POST api.github.com（在 write_hosts） → require_approval

- [ ] **Step 7: Credentials API 测**

`tests/gateway/test_credentials_api.py`：用 FastAPI TestClient：
- POST → 返回不含 value
- GET 列表
- DELETE 有引用 → 409
- DELETE 无引用 → 200

- [ ] **Step 8: 集成测试**

`tests/integration/test_api_request_e2e.py`：全链路（mock httpx 服务端）创建 Edict + 凭证 + 调 api_request，验证审计字段完整。

- [ ] **Step 9: 覆盖率验证**

```bash
pytest --cov=src/tianshu --cov-report=term-missing tests/
# 目标 >= 80%
```

- [ ] **Step 10: Commit**

```bash
git add tests/
git commit -m "$(cat <<'EOF'
test: 网络能力端到端 + 单元测试统一补齐

引入: 9 个 test 文件覆盖 secrets / api_request / web_extract / policy rules / API / 集成
使用者: CI pipeline
关闭: 覆盖率门槛在 CI 里由 pytest-cov 把关
EOF
)"
```

---

## Task 24: 文档更新

**Files:**
- Modify: `README.md`
- Modify: `docs/impl/skills.md` 或等价工具文档
- Create: `docs/ops/credentials.md`

- [ ] **Step 1: README 增加网络能力章节**

在 README 的"能力"或"工具"章节加：
```markdown
### 外部网络通讯（鸿胪寺）

天枢内置 4 个网络工具，按 profile 差异化启用：

| 工具 | Tier | OFFLINE | DEFAULT | RESEARCH |
|------|------|---------|---------|----------|
| `web_fetch` | T2 | ❌ | ✅ (local+jina) | ✅ (+firecrawl) |
| `web_search` | T2 | ❌ | ✅ (tavily) | ✅ |
| `api_request` | T2/T3 | ❌ | ❌ | ✅ (GET/HEAD) |
| `web_extract` | T2 | ❌ | ❌ | ✅ (firecrawl) |

写方法 (POST/PUT/DELETE/PATCH) 任何 profile 都需要在 Edict 里单独显式启用并走审批。

凭证通过藏兵阁页面管理，见 [凭证运维手册](docs/ops/credentials.md)。
```

- [ ] **Step 2: ops 凭证手册**

创建 `docs/ops/credentials.md`：
```markdown
# 外部凭证运维手册

## 生成主密钥
\`\`\`python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
\`\`\`
写入 `.env` 或部署配置：
\`\`\`
TIANSHU_SECRET_MASTER_KEY=<generated-key>
\`\`\`

**⚠️ 丢失主密钥 = 所有已存凭证不可恢复**。备份到密钥管理器。

## 添加凭证
1. 登录后进入「藏兵阁 → 外部凭证」
2. 点「新增」，填名称 / host_pattern / header 模板 / value
3. host_pattern 支持 `api.github.com` 精确或 `*.notion.com` 通配

## 轮换凭证
UI 编辑同一条凭证，替换 value 字段即可。host_pattern 不可改（改动等于新建）。

## 删除凭证
删除前系统会检查有无 Edict 引用此 host。有引用会阻止删除，提示先从 Edict 移除。

## 降级场景
- 主密钥未设置 → `api_request` 工具自动不注册，web_fetch/web_search/web_extract 不受影响
- DB 迁移失败 → Storage init 时会抛错，停止启动
- 单个凭证解密失败 → 对应 api_request 调用返回 `credential_conflict` 错误，不影响其他凭证
```

- [ ] **Step 3: skills.md 更新**

修改 `docs/impl/skills.md`（或工具清单文档），追加 4 个工具的使用示例和参数说明。

- [ ] **Step 4: Commit**

```bash
git add README.md docs/
git commit -m "$(cat <<'EOF'
docs: 外部网络能力 README + ops 凭证手册 + skills 工具说明

引入: README 网络能力表, docs/ops/credentials.md, skills.md 4 工具段
使用者: 新入职开发 / ops
关闭: 文档过期时不影响运行（但 PR review 会被打回）
EOF
)"
```

---

## Task 25: prompt_builder 自描述 + 示例 edict 模板

**Files:**
- Modify: `src/tianshu/executor/prompt_builder.py` 或等价 prompt 构建位置
- Modify: Edict 模板样例文件（若有）

- [ ] **Step 1: 定位 prompt_builder**

```bash
grep -rn "system_prompt\|build_prompt\|tool_description" src/tianshu/executor/ src/tianshu/planner/ | head -10
```

- [ ] **Step 2: 注入网络能力说明**

在 prompt builder 的 "tools available" 段追加（按实际风格改）：
```python
NETWORK_CAPABILITY_HINT = """
**外部网络能力**（仅当 profile 允许时可用）：
- `web_fetch(url)` —— 读公开网页正文
- `web_search(query)` —— 搜索
- `api_request(url, method, ...)` —— 调三方 API；写方法 (POST/PUT/DELETE/PATCH) 触发审批
- `web_extract(url, schema)` —— 按 schema 抽结构化数据

调 `api_request` 时：
- 不要在 headers 里写 Authorization / Cookie / X-Api-Key；系统按 host 自动注入
- 只能调用 Edict 白名单内的 host，否则返回 host_not_whitelisted
"""
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/executor/prompt_builder.py
git commit -m "$(cat <<'EOF'
chore: prompt_builder 暴露 4 个网络工具说明给 LLM

引入: NETWORK_CAPABILITY_HINT 注入系统 prompt
使用者: 六部官员 LLM 推理
关闭: 文案与实际工具契约出入时，PolicyRule 会拒绝无效调用兜底
EOF
)"
```

---

## 合入前 checklist（Task 1-25 全部完成后）

```
[1]  git log feat_phase5..HEAD → ~26 新 commit，层次清晰
[2]  ruff + black + isort + mypy 全绿
[3]  pytest --cov >= 80%
[4]  本地启动：日志显示 4 工具按 env 状态 enabled
[5]  UI 藏兵阁新增凭证 → DB encrypted_value 是 bytes
[6]  safe-explore Edict → web_fetch / api_request 都拒绝
[7]  refactor-in-place → web_fetch/search/extract 通，api_request 仍拒
[8]  trusted-automation + hosts=["api.github.com"] → GET 通，POST 触发审批
[9]  headers 传 Authorization → 返回 forbidden_header
[10] 删除有 Edict 引用的凭证 → 409
[11] 关 TIANSHU_SECRET_MASTER_KEY 重启 → api_request 不注册，日志 WARN
[12] SSRF (127.0.0.1 / 169.254.169.254 / user@evil.com) 全拒
[13] rate limit 打满 → retry_after_*s
[14] AuditDashboard 看到 network 审计，credential_name 有值、value 为空
```

合入 PR 标题：`feat: 外部网络通讯能力 (fetch / search / api_request / extract + 藏兵阁凭证)`
