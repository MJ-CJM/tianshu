"""Markdown → Telegram MarkdownV2 转换 + UTF-16 安全分片。

移植自 hermes-agent gateway/platforms（telegram.py / base.py），适配天枢。

要点：
- Telegram MarkdownV2 转义规则严苛（`_*[]()~`>#+-=|{}.!\\` 均需转义）；
- 代码块/行内代码内容需保护，不被转义破坏；
- GFM 表格 Telegram 无原生语法 → 用 ``` 包裹成等宽预格式；
- 消息长度上限 4096 按 **UTF-16 码元** 计（emoji 占 2 单元），分片须按 UTF-16。
"""

from __future__ import annotations

import re

_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#\+\-=|{}.!\\])")


def escape_mdv2(text: str) -> str:
    """转义 Telegram MarkdownV2 特殊字符。"""
    return _MDV2_ESCAPE_RE.sub(r"\\\1", text)


def strip_mdv2(text: str) -> str:
    """去掉 MarkdownV2 转义反斜杠与格式标记，得到干净纯文本（回退用）。"""
    cleaned = re.sub(r"\\([_*\[\]()~`>#\+\-=|{}.!\\])", r"\1", text)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", cleaned)
    cleaned = re.sub(r"~([^~]+)~", r"\1", cleaned)
    cleaned = re.sub(r"\|\|([^|]+)\|\|", r"\1", cleaned)
    return cleaned


# --- GFM 表格 → 代码块 ---

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$")


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and "|" in stripped


def wrap_markdown_tables(text: str) -> str:
    """把 GFM 管道表格用 ``` 包裹，让 Telegram 以等宽块渲染（保留对齐）。"""
    if "|" not in text or "-" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[i + 1]):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and _is_table_row(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append("```")
            out.extend(table_block)
            out.append("```")
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def format_message(content: str) -> str:
    """标准 markdown → Telegram MarkdownV2。保护代码块/行内代码，转换标题/粗斜体/链接，转义其余。"""
    if not content:
        return content

    placeholders: dict[str, str] = {}
    counter = [0]

    def _ph(value: str) -> str:
        key = f"\x00PH{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = value
        return key

    text = content
    text = wrap_markdown_tables(text)

    # 1) 保护围栏代码块
    def _protect_fenced(m: re.Match) -> str:
        raw = m.group(0)
        open_end = raw.index("\n") + 1 if "\n" in raw[3:] else 3
        opening = raw[:open_end]
        body_and_close = raw[open_end:]
        body = body_and_close[:-3]
        body = body.replace("\\", "\\\\").replace("`", "\\`")
        return _ph(opening + body + "```")

    text = re.sub(r"(```(?:[^\n]*\n)?[\s\S]*?```)", _protect_fenced, text)

    # 2) 保护行内代码
    text = re.sub(
        r"(`[^`]+`)",
        lambda m: _ph(m.group(0).replace("\\", "\\\\")),
        text,
    )

    # 3) 链接
    def _convert_link(m: re.Match) -> str:
        display = escape_mdv2(m.group(1))
        url = m.group(2).replace("\\", "\\\\").replace(")", "\\)")
        return _ph(f"[{display}]({url})")

    text = re.sub(r"\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)", _convert_link, text)

    # 4) 标题 → 粗体
    def _convert_header(m: re.Match) -> str:
        inner = m.group(1).strip()
        inner = re.sub(r"\*\*(.+?)\*\*", r"\1", inner)
        return _ph(f"*{escape_mdv2(inner)}*")

    text = re.sub(r"^#{1,6}\s+(.+)$", _convert_header, text, flags=re.MULTILINE)

    # 5) 粗体 **x** → *x*
    text = re.sub(
        r"\*\*(.+?)\*\*",
        lambda m: _ph(f"*{escape_mdv2(m.group(1))}*"),
        text,
    )

    # 6) 斜体 *x* → _x_
    text = re.sub(
        r"\*([^*\n]+)\*",
        lambda m: _ph(f"_{escape_mdv2(m.group(1))}_"),
        text,
    )

    # 7) 删除线 ~~x~~ → ~x~
    text = re.sub(
        r"~~(.+?)~~",
        lambda m: _ph(f"~{escape_mdv2(m.group(1))}~"),
        text,
    )

    # 8) 剧透 ||x||
    text = re.sub(
        r"\|\|(.+?)\|\|",
        lambda m: _ph(f"||{escape_mdv2(m.group(1))}||"),
        text,
    )

    # 9) 引用块
    def _convert_blockquote(m: re.Match) -> str:
        prefix = m.group(1)
        body = m.group(2)
        if prefix.startswith("**") and body.endswith("||"):
            return _ph(f"{prefix} {escape_mdv2(body[:-2])}||")
        return _ph(f"{prefix} {escape_mdv2(body)}")

    text = re.sub(r"^((?:\*\*)?>{1,3}) (.+)$", _convert_blockquote, text, flags=re.MULTILINE)

    # 10) 转义其余
    text = escape_mdv2(text)

    # 11) 还原占位（逆序，处理嵌套）
    for key in reversed(list(placeholders.keys())):
        text = text.replace(key, placeholders[key])

    return text


# --- UTF-16 安全长度与分片 ---


def utf16_len(s: str) -> int:
    """UTF-16 码元数（emoji/非 BMP 字符占 2 单元）。"""
    return len(s.encode("utf-16-le")) // 2


def _prefix_within_utf16_limit(s: str, limit: int) -> str:
    if utf16_len(s) <= limit:
        return s
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if utf16_len(s[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo]


def truncate_message(text: str, limit: int = 4096) -> list[str]:
    """按 UTF-16 ≤limit 分片；优先在换行边界切，避免割裂代理对。"""
    if utf16_len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while utf16_len(remaining) > limit:
        prefix = _prefix_within_utf16_limit(remaining, limit)
        # 尽量在最后一个换行处断开（保留段落可读性）
        cut = prefix.rfind("\n")
        if cut <= 0:
            cut = len(prefix)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks
