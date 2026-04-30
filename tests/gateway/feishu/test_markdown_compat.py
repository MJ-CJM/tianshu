"""markdown_compat 单元测试。"""
from __future__ import annotations

from tianshu.gateway.feishu.markdown_compat import (
    DEFAULT_CHUNK_SIZE,
    convert_tables_to_lists,
    split_long,
)


def test_convert_two_column_table():
    md = (
        "概况：\n\n"
        "| 项目 | 值 |\n"
        "|------|-----|\n"
        "| 标题 | demo |\n"
        "| 总页数 | 26 页 |\n"
    )
    out = convert_tables_to_lists(md)
    assert "- **标题**：demo" in out
    assert "- **总页数**：26 页" in out
    # 原表格行应被替换
    assert "|------|" not in out


def test_convert_three_column_table_keeps_all_columns():
    md = (
        "| A | B | C |\n"
        "|---|---|---|\n"
        "| 1 | 2 | 3 |\n"
    )
    out = convert_tables_to_lists(md)
    assert "1" in out and "2" in out and "3" in out
    # 多列用斜杠分隔
    assert "/" in out


def test_no_table_unchanged():
    md = "标题\n\n正文段落。\n\n- a\n- b"
    assert convert_tables_to_lists(md) == md


def test_table_at_eof_no_trailing_newline():
    md = "| k | v |\n|---|---|\n| x | y |"
    out = convert_tables_to_lists(md)
    assert "- **x**：y" in out


def test_split_long_short_returns_single_chunk():
    text = "abc"
    chunks = split_long(text, max_len=100)
    assert chunks == [text]


def test_split_long_breaks_on_paragraph_boundary():
    p1 = "a" * 50
    p2 = "b" * 50
    text = f"{p1}\n\n{p2}"
    chunks = split_long(text, max_len=60)
    # 两段都 <60，但合并 102>60 → 必须拆
    assert len(chunks) == 2
    assert p1 in chunks[0]
    assert p2 in chunks[1]


def test_split_long_hard_split_when_paragraph_oversize():
    text = "x" * 250
    chunks = split_long(text, max_len=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_default_chunk_size_sane():
    assert DEFAULT_CHUNK_SIZE > 1000
