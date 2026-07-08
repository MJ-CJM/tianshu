"""出站凭证脱敏 —— WS 广播 / webhook / 通知渠道外发前的统一 redact。

语料合并 multica ``server/pkg/redact/redact.go``(已接线的生产语料)与
zeroclaw ``leak_detector.rs``;两个项目独立实现同一件事是「必须做」的强信号。
额外遮蔽本机家目录路径(防泄漏用户名)。

职责边界:这里管**平台出站面**(外发最后一公里);``tools.mcp.redact`` 管
MCP 连接错误日志的窄场景,保持独立。memorial.result 不脱敏(执行语义,
follow-up 要重建 history);events 落库 payload 由调用方选择性使用。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 规则按序检查;更特异的模式在前,泛化 key=value 兜底在后。
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # AWS access key id(恒以 AKIA 开头)
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED AWS KEY]"),
    # AWS secret(40 位 base64-ish,带常见前导名)
    (
        re.compile(
            r"(?i)(?:aws_secret_access_key|secret_?access_?key)\s*[=:]\s*[A-Za-z0-9/+=]{40}"
        ),
        "[REDACTED AWS SECRET]",
    ),
    # PEM 私钥(多行)
    (
        re.compile(
            r"-----BEGIN[A-Z\s]*PRIVATE KEY-----.*?-----END[A-Z\s]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    # GitHub tokens(classic PAT / fine-grained / OAuth 等)
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}\b"), "[REDACTED GITHUB TOKEN]"),
    # OpenAI / Anthropic 风格 API key
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED API KEY]"),
    # Slack tokens
    (re.compile(r"\bxox[bporas]-[A-Za-z0-9-]{10,}\b"), "[REDACTED SLACK TOKEN]"),
    # GitLab PAT
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), "[REDACTED GITLAB TOKEN]"),
    # JWT(三段 base64url)
    (
        re.compile(r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "[REDACTED JWT]",
    ),
    # Bearer <token>
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer [REDACTED]"),
    # 带密码的连接串
    (
        re.compile(r"(?i)\b(?:postgres|mysql|mongodb|redis|amqp)(?:ql)?://[^:\s]+:[^@\s]+@"),
        "[REDACTED CONNECTION STRING]@",
    ),
    # 泛化 KEY=value / KEY: value(常见 secret 环境变量名)
    (
        re.compile(
            r"(?i)\b(?:API_KEY|API_SECRET|SECRET_KEY|ACCESS_TOKEN|AUTH_TOKEN|PRIVATE_KEY"
            r"|DATABASE_URL|DB_PASSWORD|DB_URL|REDIS_URL|MASTER_KEY|PASSWORD|SECRET|TOKEN)"
            r"\s*[=:]\s*\S+"
        ),
        "[REDACTED CREDENTIAL]",
    ),
]

# 家目录遮蔽:/Users/john → /Users/****(multica 同款,防泄漏本机用户名)
_HOME = str(Path.home())
_USERNAME = os.path.basename(_HOME) if _HOME else ""


def redact_text(s: str) -> str:
    """扫描已知 secret 模式并替换为占位符;同时遮蔽家目录路径。"""
    if not s:
        return s
    for pattern, replacement in _PATTERNS:
        s = pattern.sub(replacement, s)
    if _HOME and _USERNAME:
        masked = _HOME.replace(_USERNAME, "****")
        s = s.replace(_HOME, masked)
    return s


def redact_mapping(m: dict) -> dict:
    """返回 m 的副本:字符串值经 redact_text,嵌套 dict/list 递归,其余原样。"""
    return {k: _redact_value(v) for k, v in m.items()}


def _redact_value(v: object) -> object:
    if isinstance(v, str):
        return redact_text(v)
    if isinstance(v, dict):
        return redact_mapping(v)
    if isinstance(v, list):
        return [_redact_value(item) for item in v]
    return v
