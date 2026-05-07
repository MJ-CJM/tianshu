"""tianshu.tools.mcp.naming 单元测试。"""

from __future__ import annotations

import pytest

from tianshu.tools.mcp.naming import (
    NAME_PREFIX,
    decode_tool_name,
    encode_tool_name,
    is_mcp_tool,
)


@pytest.mark.unit
class TestEncodeToolName:
    def test_basic(self) -> None:
        assert encode_tool_name("github", "create_issue") == "mcp_github_create_issue"

    def test_tool_with_underscores_preserved(self) -> None:
        assert (
            encode_tool_name("filesystem", "list_directory_recursive")
            == "mcp_filesystem_list_directory_recursive"
        )

    def test_empty_server_rejected(self) -> None:
        with pytest.raises(ValueError, match="server"):
            encode_tool_name("", "x")

    def test_empty_tool_rejected(self) -> None:
        with pytest.raises(ValueError, match="tool"):
            encode_tool_name("github", "")

    def test_underscore_in_server_rejected(self) -> None:
        with pytest.raises(ValueError, match="underscore"):
            encode_tool_name("git_hub", "x")


@pytest.mark.unit
class TestDecodeToolName:
    def test_basic_roundtrip(self) -> None:
        assert decode_tool_name("mcp_github_create_issue") == ("github", "create_issue")

    def test_tool_with_underscores(self) -> None:
        # decode 用 split("_", 2)，保留 tool 名内部的下划线
        assert decode_tool_name("mcp_fs_list_directory_recursive") == (
            "fs",
            "list_directory_recursive",
        )

    def test_non_mcp_tool_returns_none(self) -> None:
        assert decode_tool_name("read_file") is None
        assert decode_tool_name("foo_bar_baz") is None  # prefix 不是 mcp
        assert decode_tool_name("mcp") is None  # 段数不够
        assert decode_tool_name("mcp_github") is None  # 缺 tool


@pytest.mark.unit
class TestIsMCPTool:
    def test_mcp_tool(self) -> None:
        assert is_mcp_tool("mcp_x_y") is True

    def test_non_mcp_tool(self) -> None:
        assert is_mcp_tool("write_file") is False
        assert is_mcp_tool("") is False


def test_prefix_constant() -> None:
    assert NAME_PREFIX == "mcp"
