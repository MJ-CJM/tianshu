"""飞书 markdown 兼容层。

飞书的 post(tag=md) 与卡片 markdown 元素都不支持 GFM 表格语法
（| col | col |），渲染会被吃掉。本模块把表格转成"项: 值" 形式的列表
并提供超长内容的安全分段。
"""

from __future__ import annotations

import re

# 单条飞书消息文本上限（保守值；卡片 markdown 元素实际可承载更大）
DEFAULT_CHUNK_SIZE = 8000

_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_DIVIDER_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _split_row(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def convert_tables_to_lists(md: str) -> str:
    """把 markdown 表格转成「项: 值」的无序列表。

    单行表格（仅 header + divider）会被丢弃；常规两列表格按 `- key: value`
    渲染；多列表格按 `- col1 / col2 / col3` 渲染（保持信息不丢）。
    """
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _TABLE_LINE_RE.match(line) and i + 1 < n and _TABLE_DIVIDER_RE.match(lines[i + 1]):
            header = _split_row(line)
            i += 2  # 跳过 divider
            rows: list[list[str]] = []
            while i < n and _TABLE_LINE_RE.match(lines[i]):
                rows.append(_split_row(lines[i]))
                i += 1
            for row in rows:
                if len(header) == 2 and len(row) == 2:
                    out.append(f"- **{row[0]}**：{row[1]}")
                else:
                    pairs = [f"{h}: {v}" for h, v in zip(header, row, strict=False) if v]
                    out.append("- " + " / ".join(pairs))
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def split_long(text: str, max_len: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """按段落边界拆长文本，保证每段 ≤ max_len。"""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    buf = ""
    for p in paragraphs:
        candidate = (buf + "\n\n" + p) if buf else p
        if len(candidate) <= max_len:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        # 单段已超 max_len：硬切
        while len(p) > max_len:
            chunks.append(p[:max_len])
            p = p[max_len:]
        buf = p
    if buf:
        chunks.append(buf)
    return chunks


__all__ = ["convert_tables_to_lists", "split_long", "DEFAULT_CHUNK_SIZE"]
