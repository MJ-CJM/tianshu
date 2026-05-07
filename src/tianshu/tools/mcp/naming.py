"""MCP 工具命名编解。

约定：
- 注入 ToolRegistry 的工具名格式 ``mcp_<server>_<tool>``。
- ``server`` 与 ``tool`` 都允许字母 / 数字 / 下划线 / 中横线 / 点；
  ``server`` 在编码时不再做转义（必须是合法标识），``tool`` 同理。
- 解码采用 ``str.split("_", 2)``，首段固定 ``mcp``，第二段是 server，第三段是
  原始 tool 名（可包含下划线）。
"""

from __future__ import annotations

NAME_PREFIX = "mcp"


def encode_tool_name(server: str, tool: str) -> str:
    """生成注入 ``ToolRegistry`` 的工具名。"""
    if not server:
        raise ValueError("server name must be non-empty")
    if not tool:
        raise ValueError("tool name must be non-empty")
    if "_" in server:
        raise ValueError(
            f"server name must not contain underscore (got {server!r}); "
            "下划线用于切分 server 与 tool"
        )
    return f"{NAME_PREFIX}_{server}_{tool}"


def decode_tool_name(name: str) -> tuple[str, str] | None:
    """从 ``mcp_<server>_<tool>`` 解出 ``(server, tool)``；非 MCP 工具返回 None。"""
    parts = name.split("_", 2)
    if len(parts) != 3 or parts[0] != NAME_PREFIX:
        return None
    return parts[1], parts[2]


def is_mcp_tool(name: str) -> bool:
    """判断给定工具名是否属于 MCP 命名空间。"""
    return decode_tool_name(name) is not None
