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
from tianshu.tools.hongluisi.http_client import MAX_BODY_BYTES, SharedHttpClient
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

READ_METHODS = frozenset({"GET", "HEAD"})
WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})
ALL_METHODS = READ_METHODS | WRITE_METHODS

MAX_BODY_TEXT_CHARS = 16000


@dataclass(frozen=True)
class ApiResponse:
    status: str  # "ok" | "error"
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

        # 5. 读 body (带上限)
        try:
            body_bytes = resp.content
        except Exception as e:
            return self._err(
                f"read_body_error:{type(e).__name__}",
                credential_name=inj.credential_name,
            )

        if len(body_bytes) > MAX_BODY_BYTES:
            return self._err(
                f"response_too_large:{len(body_bytes)}",
                credential_name=inj.credential_name,
            )

        body_text = body_bytes.decode("utf-8", errors="replace")
        truncated = False
        if len(body_text) > MAX_BODY_TEXT_CHARS:
            body_text = body_text[:MAX_BODY_TEXT_CHARS]
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
