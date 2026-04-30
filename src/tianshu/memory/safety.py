"""Memory write safety — pattern scanning + size caps + Unicode hygiene.

借鉴 hermes-agent (`tools/memory_tool.py:90-102`) 的注入扫描设计。
本模块仅负责"内容是否允许写入"的纯函数判断；持久化与去重在
markdown_backend.write_section() 中处理。
"""

from __future__ import annotations

import re
import unicodedata

# 单条 add 的内容字符上限
MAX_CONTENT_CHARS = 4000
# 整个 MEMORY.md 文件的字符上限
MAX_FILE_CHARS = 32000

# 18 种已知威胁模式（prompt injection / 凭证外泄 / 协议越权）
# 直接照搬 hermes 的 INJECTION_PATTERNS，按需补充本地化变体。
_INJECTION_REGEXES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"ignore (all |the )?(previous|prior|above) (instructions|prompts)",
        r"disregard (all |the )?(previous|prior|above) (instructions|prompts)",
        r"forget (all |the )?(previous|prior|above) (instructions|prompts)",
        r"system\s*[:=]\s*['\"]?you are",
        r"\[INST\]|\[/INST\]",
        r"<\|system\|>|<\|user\|>|<\|assistant\|>",
        r"### system\s",
        r"</?\s*system\s*>",
        r"act as (a |an )?(unrestricted|jailbroken|developer mode)",
        r"reveal (your |the )?(system )?prompt",
        r"print (your |the )?(system )?prompt",
        # 凭证 / 外泄
        r"curl\s+[^\n]*\$(\w*token|\w*key|\w*secret|api[_-]?key)",
        r"wget\s+[^\n]*\$(\w*token|\w*key|\w*secret|api[_-]?key)",
        r"BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY",
        r"AKIA[0-9A-Z]{16}",                     # AWS access key
        r"sk-[A-Za-z0-9]{20,}",                  # OpenAI / Anthropic style
        r"ghp_[A-Za-z0-9]{20,}",                 # GitHub PAT
        # 中文越狱
        r"忽略(以上|之前|前面)(所有)?(指令|要求|提示)",
    )
)

# 不可见 / 控制字符（除常见空白：\t \n \r 之外）
_INVISIBLE_RE = re.compile(
    r"[​-‏‪-‮⁠-⁯﻿]"
)


class MemorySafetyError(ValueError):
    """内容被安全检查拒绝。message 为人类可读的拒绝原因。"""


def validate_content(content: str) -> None:
    """对 add/replace 的新内容做安全校验。失败时抛 MemorySafetyError。

    校验项：
      1. 非空且为合法 UTF-8（str 已隐含合法）
      2. 单条字符数 ≤ MAX_CONTENT_CHARS
      3. 不含已知 prompt injection / 凭证外泄模式
      4. 不含不可见控制字符
    """
    if not content or not content.strip():
        raise MemorySafetyError("memory content 为空")

    if len(content) > MAX_CONTENT_CHARS:
        raise MemorySafetyError(
            f"memory content 过长（{len(content)} 字 > {MAX_CONTENT_CHARS} 上限）；"
            "请精简或拆成多次写入。",
        )

    if _INVISIBLE_RE.search(content):
        raise MemorySafetyError(
            "memory content 含不可见 Unicode 字符（zero-width / RTL override 等），已拒绝。",
        )

    # 控制字符（除允许的空白）
    for ch in content:
        cat = unicodedata.category(ch)
        if cat == "Cc" and ch not in ("\t", "\n", "\r"):
            raise MemorySafetyError(
                f"memory content 含控制字符 U+{ord(ch):04X}，已拒绝。",
            )

    for rx in _INJECTION_REGEXES:
        m = rx.search(content)
        if m:
            raise MemorySafetyError(
                f"memory content 命中安全模式 `{m.group(0)[:60]}…`，疑似 prompt injection / 凭证泄露，已拒绝。",
            )


def check_file_size(new_total_chars: int) -> None:
    """check 总字符数是否超 MAX_FILE_CHARS，超则抛 MemorySafetyError。"""
    if new_total_chars > MAX_FILE_CHARS:
        raise MemorySafetyError(
            f"MEMORY.md 写入后将达 {new_total_chars} 字，超出 {MAX_FILE_CHARS} 字上限；"
            "请先用 memory_write(action=\"remove\"...) 清理旧条目，再写新内容。",
        )


def normalize_section(section: str) -> str:
    """把 agent 提供的 section 标题归一化为 `## xxx` 形式。

    允许 agent 传 "心学要旨" / "## 心学要旨" / "###" 等多种写法；
    本函数统一为 H2 锚点，避免 H1（撞文件标题）/ 多级标题混乱。
    """
    s = section.strip()
    # 去掉所有前导 #
    while s.startswith("#"):
        s = s[1:].strip()
    if not s:
        raise MemorySafetyError("section 标题为空")
    if len(s) > 80:
        raise MemorySafetyError(f"section 标题过长（{len(s)} 字 > 80 上限）")
    return f"## {s}"
