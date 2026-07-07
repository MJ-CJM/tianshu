"""集测用最小 stdio MCP server（FastMCP）。

由 ``test_manager.py`` 通过 ``python -m`` 启动为子进程，提供 2 个工具：
- ``echo(text: str) -> str`` — 简单回显
- ``add(a: int, b: int) -> int`` — 两数相加

不引入额外依赖，仅依赖已经装到 venv 的 ``mcp`` 包。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tianshu-mcp-fixture")


@mcp.tool()
def echo(text: str) -> str:
    """Echo back the given text."""
    return f"echo:{text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


if __name__ == "__main__":
    mcp.run()
