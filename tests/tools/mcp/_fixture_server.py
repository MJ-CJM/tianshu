"""集测用最小 stdio MCP server（FastMCP）。

由 ``test_manager.py`` 通过 ``python -m`` 启动为子进程，提供 2 个工具：
- ``echo(text: str) -> str`` — 简单回显
- ``add(a: int, b: int) -> int`` — 两数相加

不引入额外依赖，仅依赖已经装到 venv 的 ``mcp`` 包。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tianshu-mcp-fixture")

if child_pid_file := os.environ.get("MCP_CHILD_PID_FILE"):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(child_pid_file).write_text(str(child.pid), encoding="utf-8")


@mcp.tool()
def echo(text: str) -> str:
    """Echo back the given text."""
    return f"echo:{text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def env_value(name: str) -> str:
    """Return one environment variable for clean-environment tests."""
    return os.environ.get(name, "")


if __name__ == "__main__":
    mcp.run()
