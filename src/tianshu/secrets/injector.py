"""host 匹配 + header 注入。Spec Section 4.4-4.5。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.secrets.store import CredentialStore

# 敏感 header 黑名单：用户 api_request(headers=) 传这些直接拒绝
FORBIDDEN_USER_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "proxy-authorization",
    }
)


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
    credential_name: str | None  # None 表示没命中凭证


class CredentialInjector:
    def __init__(self, store: CredentialStore) -> None:
        self._store = store

    def validate_user_headers(self, user_headers: dict[str, str]) -> None:
        for name in user_headers:
            if name.lower() in FORBIDDEN_USER_HEADERS:
                raise ForbiddenHeader(name)

    def inject(self, url_host: str, user_headers: dict[str, str]) -> InjectionResult:
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
        k: ("<redacted>" if k.lower() in FORBIDDEN_USER_HEADERS else v) for k, v in headers.items()
    }
