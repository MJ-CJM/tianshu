"""MarkdownV2 转换 + UTF-16 分片。"""
from __future__ import annotations

from tianshu.gateway.telegram.markdown_v2 import (
    escape_mdv2,
    format_message,
    truncate_message,
    utf16_len,
    wrap_markdown_tables,
)


def test_escape_special_chars():
    out = escape_mdv2("a.b-c!")
    assert out == "a\\.b\\-c\\!"


def test_format_bold_to_single_star():
    assert format_message("**bold**") == "*bold*"


def test_format_protects_inline_code():
    out = format_message("see `a_b` here")
    assert "`a_b`" in out  # 代码内 _ 不转义


def test_format_empty():
    assert format_message("") == ""


def test_utf16_len_emoji_counts_two():
    assert utf16_len("a") == 1
    assert utf16_len("😀") == 2
    assert utf16_len("ab😀") == 4


def test_truncate_short_returns_single():
    assert truncate_message("hello", 4096) == ["hello"]


def test_truncate_splits_within_limit():
    text = "x" * 5000
    chunks = truncate_message(text, 4096)
    assert len(chunks) >= 2
    for c in chunks:
        assert utf16_len(c) <= 4096


def test_truncate_does_not_split_surrogate():
    # 全 emoji（每个 2 单元），限额奇数也不能割裂代理对
    text = "😀" * 3000  # 6000 UTF-16 单元
    chunks = truncate_message(text, 4095)
    for c in chunks:
        assert utf16_len(c) <= 4095
        # 重新编码不应报错（无半个代理）
        c.encode("utf-16-le")
    assert "".join(chunks) == text


def test_wrap_markdown_tables():
    md = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    wrapped = wrap_markdown_tables(md)
    assert wrapped.startswith("```")
    assert wrapped.strip().endswith("```")
