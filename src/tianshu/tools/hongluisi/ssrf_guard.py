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
    ipaddress.ip_network("169.254.169.254/32"),  # AWS / GCP metadata
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("fd00::/8"),  # IPv6 ULA
)


@dataclass(frozen=True)
class SSRFViolation(Exception):
    code: str  # 可透传给 LLM
    internal_reason: str  # 审计日志

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
        infos = await loop.run_in_executor(None, lambda: socket.getaddrinfo(host, None))
    except socket.gaierror as e:
        raise SSRFViolation("ssrf_dns_failed", f"getaddrinfo: {e}") from e

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
        cleaned = urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        return cleaned
    return url
