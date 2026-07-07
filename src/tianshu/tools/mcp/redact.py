"""日志/错误消息中的凭据脱敏。

目的：连接失败、调用异常时，错误信息常常携带 ``Authorization: Bearer xxx`` /
``api_key=...`` 等令牌；直接落到日志或返回给前端会泄露敏感信息。
"""

from __future__ import annotations

import re
from collections.abc import Callable

_REDACTED = "[REDACTED]"

# 常见 token / key 模式；命中即整段替换。
_PATTERNS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    # Authorization: Bearer xxx / Bearer xxx
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]+"), f"Bearer {_REDACTED}"),
    # Basic auth header
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]+"), f"Basic {_REDACTED}"),
    # GitHub style PAT
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _REDACTED),
    # query string / form: api_key=xxx, apikey=xxx, token=xxx, access_token=xxx
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password)"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9._\-+/=]{6,})['\"]?"
        ),
        lambda m: f"{m.group(1)}={_REDACTED}",
    ),
    # Long opaque hex/base64-looking strings (last resort, ≥32 chars, no spaces)
    # 注意：放最后；过于激进会误伤正常 ID。这里仅匹配 ≥40 的。
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), _REDACTED),
]


def redact(text: str) -> str:
    """对字符串做凭据脱敏，返回新字符串。"""
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text) if callable(repl) else pattern.sub(repl, text)
    return text
